"""Allow running as: python -m chloe.mcp"""

import asyncio

from chloe.mcp.server import run_server

asyncio.run(run_server())
