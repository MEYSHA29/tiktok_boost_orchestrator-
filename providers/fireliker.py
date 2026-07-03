"""
TikTok Boost Orchestrator - Fireliker Provider Adapter
Simpler request-based or browser-based adapter for fireliker.com
"""

import asyncio
import re
from datetime import datetime
from typing import List

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from .base import BaseProvider, BoostResult, ServiceStatus
from core.utils import random_delay, parse_cooldown


class FirelikerProvider(BaseProvider):
    """Fireliker adapter - hybrid request/browser approach."""
    
    SERVICES = {
        "hearts": "hearts",
        "views": "views", 
        "shares": "shares",
        "followers": "fans",
    }
    
    def __init__(self, config: dict, captcha_solver, proxy_manager):
        super().__init__("fireliker", config, captcha_solver, proxy_manager)
        self.url = config.get("url", "https://fireliker.com")
        self.client: Optional[httpx.AsyncClient] = None
        self.csrf_token: str = ""
    
    async def initialize(self) -> bool:
        """Initialize HTTP session and extract CSRF tokens."""
        logger.info(f"[{self.name}] Initializing...")
        
        proxy = self.proxy.get_next()
        proxy_dict = self.proxy.get_proxy_dict(proxy) if proxy else {}
        
        self.client = httpx.AsyncClient(
            proxies=proxy_dict,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        
        try:
            resp = await self.client.get(self.url)
            if resp.status_code != 200:
                logger.error(f"[{self.name}] Failed to load: {resp.status_code}")
                return False
            
            # Extract CSRF token if present
            soup = BeautifulSoup(resp.text, "html.parser")
            csrf_input = soup.find("input", {"name": "csrf_token"}) or soup.find("meta", {"name": "csrf-token"})
            if csrf_input:
                self.csrf_token = csrf_input.get("value", "") or csrf_input.get("content", "")
            
            self.session_valid = True
            logger.success(f"[{self.name}] Session initialized")
            return True
            
        except Exception as e:
            logger.error(f"[{self.name}] Init error: {e}")
            return False
    
    async def get_services(self) -> List[ServiceStatus]:
        """Check which services are available."""
        services = []
        for key in self.SERVICES:
            cooldown = self.get_cooldown_remaining(key)
            services.append(ServiceStatus(
                name=key,
                available=cooldown == 0,
                cooldown_until=datetime.now() + __import__('datetime').timedelta(seconds=cooldown) if cooldown > 0 else None
            ))
        return services
    
    async def is_available(self, service: str) -> bool:
        return self.session_valid and self.get_cooldown_remaining(service) == 0
    
    async def boost(self, service: str, target: str) -> BoostResult:
        """Submit boost request via HTTP."""
        if not self.session_valid:
            await self.initialize()
        
        try:
            # Fireliker uses simple form POST
            data = {
                "username": target if service == "followers" else "",
                "video_url": target if service != "followers" else "",
                "service": self.SERVICES.get(service, service),
            }
            if self.csrf_token:
                data["csrf_token"] = self.csrf_token
            
            random_delay(2, 5)
            
            resp = await self.client.post(f"{self.url}/submit", data=data)
            content = resp.text
            
            # Parse response
            cooldown = parse_cooldown(content)
            if cooldown:
                self.set_cooldown(service, cooldown + 60)
                return BoostResult(False, service, cooldown_seconds=cooldown, message=f"Cooldown", provider=self.name)
            
            if "success" in content.lower() or "sent" in content.lower():
                match = re.search(r"(\\d+)", content)
                amount = int(match.group(1)) if match else 0
                self.stats["success"] += 1
                return BoostResult(True, service, amount=amount, message="Success", provider=self.name)
            
            self.stats["failed"] += 1
            return BoostResult(False, service, message="Failed", provider=self.name)
            
        except Exception as e:
            logger.error(f"[{self.name}] Boost error: {e}")
            return BoostResult(False, service, message=str(e), provider=self.name)
    
    async def cleanup(self):
        if self.client:
            await self.client.aclose()
