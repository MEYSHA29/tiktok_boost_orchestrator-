"""
TikTok Boost Orchestrator - Main Orchestration Engine
Manages provider rotation, cooldown scheduling, and request distribution.
"""

import asyncio
import random
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from core.captcha_solver import CaptchaSolver
from core.proxy_manager import ProxyManager
from core.utils import StatsTracker, random_delay, load_proxy_list
from providers.zefoy import ZefoyProvider
from providers.fireliker import FirelikerProvider
from providers.mytoolstown import MytoolstownProvider
from providers.vipto import ViptoProvider


class BoostOrchestrator:
    """
    Central orchestrator that manages multiple providers, rotates between them
    based on cooldown status, and distributes boost requests optimally.
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.target = config["target"]
        self.services = config["services"]
        self.global_config = config["global"]
        
        # Initialize subsystems
        self.captcha = CaptchaSolver(
            solver_type=config.get("captcha_solver", "ocr"),
            api_key_2captcha=config.get("api_key_2captcha", ""),
            api_key_anticaptcha=config.get("api_key_anticaptcha", ""),
        )
        
        proxies = load_proxy_list(self.global_config.get("proxy_list_file", ""))
        self.proxy_manager = ProxyManager(proxies)
        
        self.stats = StatsTracker(self.global_config.get("stats_file", "./sessions/stats.json"))
        
        # Initialize providers
        self.providers: Dict[str, object] = {}
        self._init_providers(config.get("providers", {}))
        
        self.running = False
        self.boost_queue: asyncio.Queue = asyncio.Queue()
        self.results: List[dict] = []
    
    def _init_providers(self, provider_configs: dict):
        """Initialize enabled providers."""
        provider_map = {
            "zefoy": ZefoyProvider,
            "fireliker": FirelikerProvider,
            "mytoolstown": MytoolstownProvider,
            "vipto": ViptoProvider,
        }
        
        for name, p_config in provider_configs.items():
            if not p_config.get("enabled", False):
                continue
            
            cls = provider_map.get(name)
            if not cls:
                logger.warning(f"Unknown provider: {name}")
                continue
            
            # Add provider-specific proxy to pool if configured
            if p_config.get("proxy"):
                self.proxy_manager.add_proxy(p_config["proxy"])
            
            self.providers[name] = cls(p_config, self.captcha, self.proxy_manager)
            logger.info(f"Provider registered: {name}")
    
    async def start(self):
        """Main loop: initialize all providers and process boost queue."""
        logger.info("═" * 60)
        logger.info("🚀 TikTok Boost Orchestrator Starting")
        logger.info("═" * 60)
        logger.info(f"Target: {self.target['username']}")
        logger.info(f"Video: {self.target['video_url']}")
        logger.info(f"Services: {', '.join(self.services)}")
        logger.info(f"Providers: {', '.join(self.providers.keys())}")
        logger.info("═" * 60)
        
        self.running = True
        
        # Initialize all providers concurrently
        init_tasks = [p.initialize() for p in self.providers.values()]
        init_results = await asyncio.gather(*init_tasks, return_exceptions=True)
        
        for (name, provider), result in zip(self.providers.items(), init_results):
            if isinstance(result, Exception) or not result:
                logger.error(f"[{name}] Failed to initialize, removing from pool")
                self.providers.pop(name, None)
        
        if not self.providers:
            logger.error("No providers available! Check configuration and connectivity.")
            return
        
        # Populate boost queue
        for service in self.services:
            target = self.target["username"] if service == "followers" else self.target["video_url"]
            await self.boost_queue.put({"service": service, "target": target, "priority": 0})
        
        # Start worker tasks
        workers = []
        max_concurrent = self.global_config.get("max_concurrent", 2)
        for i in range(max_concurrent):
            task = asyncio.create_task(self._worker_loop(f"worker-{i+1}"))
            workers.append(task)
        
        # Monitor and report
        monitor = asyncio.create_task(self._monitor_loop())
        
        await asyncio.gather(*workers, monitor)
    
    async def _worker_loop(self, worker_id: str):
        """Worker that pulls from queue and executes boosts."""
        logger.info(f"[{worker_id}] Started")
        
        while self.running:
            try:
                # Get next boost request
                request = await asyncio.wait_for(self.boost_queue.get(), timeout=5.0)
                service = request["service"]
                target = request["target"]
                
                # Find best available provider
                provider = await self._select_provider(service)
                
                if not provider:
                    # No provider available, requeue with delay
                    request["priority"] += 1
                    await asyncio.sleep(10)
                    await self.boost_queue.put(request)
                    continue
                
                logger.info(f"[{worker_id}] Using {provider.name} for {service}")
                
                # Execute boost
                result = await provider.boost(service, target)
                self.stats.record(provider.name, service, result.success, result.amount)
                
                if result.success:
                    logger.success(f"[{worker_id}] {result.message}")
                    # Requeue for next cycle after cooldown
                    cooldown = result.cooldown_seconds or random.randint(300, 600)
                else:
                    if result.cooldown_seconds > 0:
                        logger.info(f"[{worker_id}] Cooldown on {provider.name}: {result.cooldown_seconds}s")
                        cooldown = result.cooldown_seconds
                    else:
                        logger.warning(f"[{worker_id}] Failed: {result.message}")
                        cooldown = random.randint(60, 180)
                
                # Requeue for continuous farming
                await asyncio.sleep(cooldown + self.global_config.get("cooldown_buffer", 30))
                await self.boost_queue.put(request)
                
                # Random delay between requests
                random_delay(
                    self.global_config.get("delay_min", 3),
                    self.global_config.get("delay_max", 8)
                )
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"[{worker_id}] Error: {e}")
                await asyncio.sleep(5)
    
    async def _select_provider(self, service: str) -> Optional[object]:
        """
        Select best provider for service based on:
        1. Availability (not in cooldown)
        2. Priority (configured order)
        3. Success rate
        4. Randomization to avoid patterns
        """
        strategy = self.global_config.get("rotation_strategy", "priority")
        
        candidates = []
        for name, provider in self.providers.items():
            if await provider.is_available(service):
                score = provider.stats["success"] - provider.stats["failed"] * 2
                cooldown = provider.get_cooldown_remaining(service)
                candidates.append((provider, score, cooldown))
        
        if not candidates:
            return None
        
        if strategy == "priority":
            # Sort by cooldown (0 first), then by score
            candidates.sort(key=lambda x: (x[2], -x[1]))
            return candidates[0][0]
        
        elif strategy == "random":
            return random.choice([c[0] for c in candidates])
        
        else:  # round_robin
            # Simple round-robin based on last used time
            candidates.sort(key=lambda x: x[0].last_used or datetime.min)
            provider = candidates[0][0]
            provider.last_used = datetime.now()
            return provider
    
    async def _monitor_loop(self):
        """Periodic status reporting."""
        while self.running:
            await asyncio.sleep(300)  # Every 5 minutes
            logger.info("═" * 60)
            logger.info(self.stats.get_summary())
            
            # Health check providers
            for name, provider in self.providers.items():
                healthy = await provider.health_check()
                if not healthy:
                    logger.warning(f"[{name}] Health check failed, reinitializing...")
                    await provider.initialize()
    
    async def stop(self):
        """Graceful shutdown."""
        logger.info("Stopping orchestrator...")
        self.running = False
        
        for provider in self.providers.values():
            await provider.cleanup()
        
        logger.info(self.stats.get_summary())
        logger.success("Orchestrator stopped")
