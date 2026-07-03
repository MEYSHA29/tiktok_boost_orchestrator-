"""
TikTok Boost Orchestrator - Base Provider Interface
All provider adapters must implement these methods.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class BoostResult:
    success: bool
    service: str
    amount: int = 0
    cooldown_seconds: int = 0
    message: str = ""
    provider: str = ""


@dataclass
class ServiceStatus:
    name: str
    available: bool
    cooldown_until: Optional[datetime] = None


class BaseProvider(ABC):
    """Abstract base class for all SMM panel providers."""
    
    def __init__(self, name: str, config: dict, captcha_solver, proxy_manager):
        self.name = name
        self.config = config
        self.captcha = captcha_solver
        self.proxy = proxy_manager
        self.session_valid = False
        self.last_used = None
        self.cooldowns: Dict[str, datetime] = {}
        self.stats = {"success": 0, "failed": 0}
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize session: bypass Cloudflare, solve CAPTCHA, establish cookies."""
        pass
    
    @abstractmethod
    async def get_services(self) -> List[ServiceStatus]:
        """Return list of available services and their cooldown status."""
        pass
    
    @abstractmethod
    async def boost(self, service: str, target: str) -> BoostResult:
        """Submit boost request for given service and target (username or URL)."""
        pass
    
    @abstractmethod
    async def is_available(self, service: str) -> bool:
        """Check if provider can accept requests for this service right now."""
        pass
    
    def set_cooldown(self, service: str, seconds: int) -> None:
        """Set cooldown expiry for a service."""
        from datetime import timedelta
        self.cooldowns[service] = datetime.now() + timedelta(seconds=seconds)
    
    def get_cooldown_remaining(self, service: str) -> int:
        """Get remaining cooldown seconds, 0 if ready."""
        if service not in self.cooldowns:
            return 0
        remaining = (self.cooldowns[service] - datetime.now()).total_seconds()
        return int(max(0, remaining))
    
    async def health_check(self) -> bool:
        """Quick check if provider is responsive."""
        return True
