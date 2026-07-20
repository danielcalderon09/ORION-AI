"""Deprecated smoke entry point retained for installation compatibility."""

import asyncio

from scripts.smoke_production_openrouter import smoke

if __name__ == "__main__":
    asyncio.run(smoke())
