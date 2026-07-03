"""
TikTok Boost Orchestrator - Mytoolstown Provider Adapter
Credit-based system requiring farming before spending.
"""

import asyncio
import re
from datetime import datetime
from typing import List

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from .base import BaseProvider, BoostResult, ServiceStatus
from core.utils import random_delay


class MytoolstownProvider(BaseProvider):
    """Mytoolstown adapter with credit farming support."""
    
    SERVICES = {
        "followers": "followers",
        "views": "views",
        "likes": "likes",
    }
    
    def __init__(self, config: dict, captcha_solver, proxy_manager):
        super().__init__("mytoolstown", config, captcha_solver, proxy_manager)
        self.url = config.get("url", "https://mytoolstown.com/tiktok")
        self.credit_config = config.get("credit_farming", {"enabled": True, "max_farm_per_session": 50})
        self.client: Optional[httpx.AsyncClient] = None
        self.credits: int = 0
        self.farmed_count: int = 0
    
    async def initialize(self) -> bool:
        """Initialize session."""
        logger.info(f"[{self.name}] Initializing...")
        
        proxy = self.proxy.get_next()
        proxy_dict = self.proxy.get_proxy_dict(proxy) if proxy else {}
        
        self.client = httpx.AsyncClient(
            proxies=proxy_dict,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        
        try:
            resp = await self.client.get(self.url)
            self.session_valid = resp.status_code == 200
            return self.session_valid
        except Exception as e:
            logger.error(f"[{self.name}] Init error: {e}")
            return False
    
    async def get_services(self) -> List[ServiceStatus]:
        services = []
        for key in self.SERVICES:
            cooldown = self.get_cooldown_remaining(key)
            available = self.session_valid and cooldown == 0 and self.credits > 0
            services.append(ServiceStatus(
                name=key,
                available=available,
                cooldown_until=datetime.now() + __import__('datetime').timedelta(seconds=cooldown) if cooldown > 0 else None
            ))
        return services
    
    async def is_available(self, service: str) -> bool:
        if not self.session_valid or self.get_cooldown_remaining(service) > 0:
            return False
        if self.credits <= 0 and self.credit_config.get("enabled", True):
            await self._farm_credits()
        return self.credits > 0
    
    async def _farm_credits(self) -> None:
        """Farm credits by following/liking other users."""
        if not self.credit_config.get("enabled", True):
            return
        
        max_farm = self.credit_config.get("max_farm_per_session", 50)
        logger.info(f"[{self.name}] Farming credits (max {max_farm})...")
        
        try:
            for i in range(max_farm):
                # Get list of users to follow
                resp = await self.client.get(f"{self.url}/get-follow-tasks")
                tasks = resp.json() if resp.status_code == 200 else []
                
                for task in tasks:
                    if self.farmed_count >= max_farm:
                        break
                    
                    # Follow/like task
                    task_id = task.get("id")
                    await self.client.post(f"{self.url}/complete-task", data={"task_id": task_id})
                    self.farmed_count += 1
                    self.credits += task.get("reward", 1)
                    
                    random_delay(3, 7)
                
                if not tasks:
                    break
                    
        except Exception as e:
            logger.error(f"[{self.name}] Credit farming error: {e}")
    
    async def boost(self, service: str, target: str) -> BoostResult:
        """Spend credits to boost target."""
        if not self.session_valid:
            await self.initialize()
        
        # Ensure we have credits
        if self.credits <= 0:
            await self._farm_credits()
        
        if self.credits <= 0:
            return BoostResult(False, service, message="No credits available", provider=self.name)
        
        try:
            data = {
                "target": target,
                "service": self.SERVICES.get(service, service),
                "credits": min(self.credits, 10),  # Spend up to 10 credits per boost
            }
            
            random_delay(2, 5)
            resp = await self.client.post(f"{self.url}/boost", data=data)
            content = resp.text
            
            if "success" in content.lower():
                self.credits -= data["credits"]
                self.stats["success"] += 1
                return BoostResult(True, service, amount=data["credits"] * 10, message="Success", provider=self.name)
            
            self.stats["failed"] += 1
            return BoostResult(False, service, message="Failed", provider=self.name)
            
        except Exception as e:
            logger.error(f"[{self.name}] Boost error: {e}")
            return BoostResult(False, service, message=str(e), provider=self.name)
    
    async def cleanup(self):
        if self.client:
            await self.client.aclose()
