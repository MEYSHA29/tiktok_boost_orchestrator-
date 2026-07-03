"""
TikTok Boost Orchestrator - Vipto Provider Adapter
Vipto.de shares architecture with Zefoy - browser-based approach.
"""

from .zefoy import ZefoyProvider


class ViptoProvider(ZefoyProvider):
    """Vipto adapter - inherits Zefoy behavior with different URL."""
    
    def __init__(self, config: dict, captcha_solver, proxy_manager):
        super().__init__(config, captcha_solver, proxy_manager)
        self.name = "vipto"
        self.url = config.get("url", "https://vipto.de")
