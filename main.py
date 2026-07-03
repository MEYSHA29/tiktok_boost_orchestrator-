#!/usr/bin/env python3
"""
TikTok Boost Orchestrator - Entry Point
Multi-provider TikTok engagement automation with intelligent rotation.

Usage:
    python main.py

Configuration:
    Edit config.yaml with your TikTok username, video URL, and provider settings.
"""

import asyncio
import signal
import sys

import yaml
from loguru import logger

from core.orchestrator import BoostOrchestrator
from core.utils import setup_logging


async def main():
    """Load configuration and start orchestrator."""
    # Load config
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config.yaml not found! Please copy from config.yaml.example and edit.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)
    
    # Setup logging
    setup_logging(config["global"].get("log_level", "INFO"))
    
    # Create orchestrator
    orchestrator = BoostOrchestrator(config)
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received...")
        asyncio.create_task(orchestrator.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start
    try:
        await orchestrator.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if config["global"].get("auto_restart", True):
            logger.info("Auto-restart enabled, restarting in 10 seconds...")
            await asyncio.sleep(10)
            await main()
        else:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
