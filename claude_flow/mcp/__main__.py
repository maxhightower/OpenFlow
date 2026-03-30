"""Allow running as: python -m claude_flow.mcp"""

import asyncio

from claude_flow.mcp.server import run_server

asyncio.run(run_server())
