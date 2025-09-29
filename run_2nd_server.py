"""
python file for testing Bootstrap (Server Introducer Flow).

full guide for testing this is in tests/manual_bootstrap.py

"""

import asyncio
from server.server import SOCPServer

async def main():
    server = SOCPServer(host="127.0.0.1", port=8767)
    await server.start_server()

asyncio.run(main())
