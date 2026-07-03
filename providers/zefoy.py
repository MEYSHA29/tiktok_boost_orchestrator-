"""
TikTok Boost Orchestrator - Zefoy Provider Adapter
Browser-based automation for zefoy.com with CAPTCHA bypass and cooldown tracking.
"""

import asyncio
import re
from datetime import datetime
from typing import List, Optional

from playwright.async_api import async_playwright, Page, Browser
from loguru import logger

from .base import BaseProvider, BoostResult, ServiceStatus
from core.utils import random_delay, parse_cooldown, save_session, load_session


class ZefoyProvider(BaseProvider):
    """Zefoy adapter using Playwright with stealth patches."""
    
    SERVICES = {
        "followers": "Followers",
        "hearts": "Hearts", 
        "views": "Views",
        "shares": "Shares",
        "favorites": "Favorites",
        "comments_hearts": "Comments Hearts",
    }
    
    def __init__(self, config: dict, captcha_solver, proxy_manager):
        super().__init__("zefoy", config, captcha_solver, proxy_manager)
        self.url = config.get("url", "https://zefoy.com")
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self._service_map = {}  # Maps internal names to page element identifiers
    
    async def initialize(self) -> bool:
        """Launch browser, bypass Cloudflare, solve CAPTCHA, reach dashboard."""
        logger.info(f"[{self.name}] Initializing session...")
        
        self.playwright = await async_playwright().start()
        
        # Browser args for stealth
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        
        proxy = self.proxy.get_next()
        pw_proxy = self.proxy.get_playwright_proxy(proxy) if proxy else None
        
        browser_type = self.playwright.chromium
        self.browser = await browser_type.launch(
            headless=self.config.get("browser_mode", "headless") == "headless",
            args=args,
            proxy=pw_proxy,
        )
        
        context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )
        
        # Inject stealth script to hide automation
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = { runtime: {} };
        """)
        
        self.page = await context.new_page()
        
        try:
            # Navigate to Zefoy
            logger.info(f"[{self.name}] Navigating to {self.url}")
            await self.page.goto(self.url, wait_until="networkidle", timeout=60000)
            
            # Check for Cloudflare challenge
            if await self._is_cloudflare_challenge():
                logger.info(f"[{self.name}] Cloudflare challenge detected, waiting...")
                await self._handle_cloudflare()
            
            # Check for and solve CAPTCHA
            if await self._has_captcha():
                logger.info(f"[{self.name}] CAPTCHA detected, solving...")
                success = await self._solve_captcha()
                if not success:
                    logger.error(f"[{self.name}] Failed to solve CAPTCHA")
                    return False
            
            # Wait for dashboard to load
            await self.page.wait_for_selector('input[placeholder*="Enter Video URL"], input[placeholder*="Enter Username"]', timeout=15000)
            
            # Parse available services from dashboard
            await self._parse_services()
            
            self.session_valid = True
            logger.success(f"[{self.name}] Session initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"[{self.name}] Initialization failed: {e}")
            await self.cleanup()
            return False
    
    async def _is_cloudflare_challenge(self) -> bool:
        """Detect Cloudflare IUAM or Turnstile challenge."""
        content = await self.page.content()
        indicators = [
            "Just a moment...",
            "Checking your browser",
            "cf-browser-verification",
            "turnstile",
            "challenge-platform",
        ]
        return any(ind in content for ind in indicators)
    
    async def _handle_cloudflare(self) -> None:
        """Wait for Cloudflare challenge to resolve or try bypass."""
        # Wait up to 30 seconds for challenge to clear
        for _ in range(30):
            await asyncio.sleep(1)
            content = await self.page.content()
            if not self._is_cloudflare_challenge():
                return
        logger.warning(f"[{self.name}] Cloudflare challenge may still be active")
    
    async def _has_captcha(self) -> bool:
        """Detect if CAPTCHA is present on page."""
        selectors = [
            'img[src*="captcha"]',
            'img[alt*="captcha" i]',
            '#captchatoken',
            'input[name*="captcha"]',
            '.g-recaptcha',
            '[data-sitekey]',
        ]
        for sel in selectors:
            if await self.page.query_selector(sel):
                return True
        return False
    
    async def _solve_captcha(self) -> bool:
        """Extract and solve CAPTCHA image."""
        try:
            # Find CAPTCHA image
            img_elem = await self.page.query_selector('img[src*="captcha"]')
            if not img_elem:
                # Try alternative selectors
                img_elem = await self.page.query_selector('img[alt*="captcha" i]')
            
            if img_elem:
                # Screenshot the CAPTCHA image
                img_bytes = await img_elem.screenshot()
                solution = self.captcha.solve_image_text(img_bytes)
                
                if solution:
                    # Find input field and submit
                    input_sel = 'input[name*="captcha"], #captchatoken, input[type="text"]'
                    await self.page.fill(input_sel, solution)
                    await self.page.click('button[type="submit"], input[type="submit"]')
                    await self.page.wait_for_load_state("networkidle")
                    
                    # Check if we're past CAPTCHA
                    if not await self._has_captcha():
                        return True
            
            # Check for reCAPTCHA/hCaptcha
            recaptcha = await self.page.query_selector('.g-recaptcha')
            if recaptcha:
                site_key = await recaptcha.get_attribute("data-sitekey")
                if site_key:
                    solution = self.captcha.solve_recaptcha(site_key, self.url)
                    if solution:
                        await self.page.evaluate(f'document.getElementById("g-recaptcha-response").innerHTML="{solution}"')
                        await self.page.click('button[type="submit"]')
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"[{self.name}] CAPTCHA solving error: {e}")
            return False
    
    async def _parse_services(self) -> None:
        """Parse available services and their status from dashboard."""
        content = await self.page.content()
        # Zefoy shows services as cards with titles and status badges
        for key, label in self.SERVICES.items():
            # Check if service button/card exists
            sel = f'text={label}'
            elem = await self.page.query_selector(sel)
            self._service_map[key] = {
                "available": elem is not None,
                "element": sel,
            }
    
    async def get_services(self) -> List[ServiceStatus]:
        """Return current service availability."""
        services = []
        for key, label in self.SERVICES.items():
            cooldown = self.get_cooldown_remaining(key)
            available = self._service_map.get(key, {}).get("available", False)
            if cooldown > 0:
                available = False
            services.append(ServiceStatus(
                name=key,
                available=available,
                cooldown_until=datetime.now() + __import__('datetime').timedelta(seconds=cooldown) if cooldown > 0 else None
            ))
        return services
    
    async def is_available(self, service: str) -> bool:
        """Check if service is ready for requests."""
        if not self.session_valid:
            return False
        if self.get_cooldown_remaining(service) > 0:
            return False
        return self._service_map.get(service, {}).get("available", False)
    
    async def boost(self, service: str, target: str) -> BoostResult:
        """Execute boost request for given service."""
        if not self.session_valid:
            await self.initialize()
        
        label = self.SERVICES.get(service, service)
        
        try:
            # Click on service tab/button
            await self.page.click(f'text={label}')
            await asyncio.sleep(1)
            
            # Fill target input
            input_sel = 'input[placeholder*="Enter Video URL"], input[placeholder*="Enter Username"], input[type="text"]'
            await self.page.fill(input_sel, target)
            
            # Click submit/search button
            await self.page.click('button[type="submit"], input[type="submit"], button:has-text("Search")')
            
            # Wait for response (success, cooldown, or error)
            await asyncio.sleep(3)
            
            content = await self.page.content()
            
            # Parse result
            cooldown = parse_cooldown(content)
            if cooldown:
                self.set_cooldown(service, cooldown + self.config.get("cooldown_buffer", 30))
                return BoostResult(
                    success=False,
                    service=service,
                    cooldown_seconds=cooldown,
                    message=f"Cooldown: {cooldown}s",
                    provider=self.name,
                )
            
            # Check for success indicators
            success_patterns = [
                r"Successfully\\s+(\\d+)\\s+views sent",
                r"(\\d+)\\s+Hearts successfully sent",
                r"(\\d+)\\s+Shares successfully sent",
                r"(\\d+)\\s+Followers successfully sent",
            ]
            for pattern in success_patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    amount = int(match.group(1))
                    self.stats["success"] += 1
                    return BoostResult(
                        success=True,
                        service=service,
                        amount=amount,
                        message=f"Success: +{amount} {service}",
                        provider=self.name,
                    )
            
            # Check for errors
            error_patterns = [
                "This service is currently not working",
                "Server too busy",
                "Too many requests",
                "An error occurred",
            ]
            for err in error_patterns:
                if err.lower() in content.lower():
                    self.stats["failed"] += 1
                    return BoostResult(
                        success=False,
                        service=service,
                        message=err,
                        provider=self.name,
                    )
            
            return BoostResult(
                success=False,
                service=service,
                message="Unknown response",
                provider=self.name,
            )
            
        except Exception as e:
            logger.error(f"[{self.name}] Boost error: {e}")
            self.stats["failed"] += 1
            return BoostResult(
                success=False,
                service=service,
                message=str(e),
                provider=self.name,
            )
    
    async def health_check(self) -> bool:
        """Check if page is still responsive."""
        try:
            if self.page:
                await self.page.evaluate("1 + 1")
                return True
        except:
            pass
        return False
    
    async def cleanup(self):
        """Close browser and release resources."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.session_valid = False
        logger.info(f"[{self.name}] Cleaned up")
