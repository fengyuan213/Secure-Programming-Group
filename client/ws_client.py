from __future__ import annotations
import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, Dict

import websockets

from shared.envelope import Envelope, create_envelope
from shared.log import get_logger

logger = get_logger(__name__)


MessageHandler = Callable[[Envelope], Awaitable[None]]


@dataclass
class ClientSession:
    user_id: str
    server_ws_url: str
    websocket: Optional[websockets.WebSocketClientProtocol] = None
    handlers: Dict[str, MessageHandler] = None

    async def connect(self) -> None:
        self.websocket = await websockets.connect(self.server_ws_url, ping_interval=15, ping_timeout=45)
        self.handlers = {}

    async def send(self, envelope: Envelope) -> None:
        assert self.websocket is not None
        await self.websocket.send(envelope.to_json())

    def on(self, msg_type: str, handler: MessageHandler) -> None:
        self.handlers[msg_type] = handler

    async def recv_loop(self, default_handler: Optional[MessageHandler] = None) -> None:
        assert self.websocket is not None
        async for raw in self.websocket:
            try:
                env = Envelope.from_json(raw)
                handler = self.handlers.get(env.type, default_handler)
                if handler:
                    await handler(env)
            except Exception as e:
                logger.error(f"Failed to parse/process inbound frame: {e}")

    async def close(self) -> None:
        if self.websocket:
            await self.websocket.close(code=1000)

    async def reconnect(self, max_retries: int = 5, base_delay: float = 1.0) -> bool:
        """Reconnect with exponential backoff"""
        for attempt in range(max_retries):
            try:
                delay = base_delay * (2 ** attempt)
                logger.info(f"Reconnecting in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                await self.connect()
                return True
            except Exception as e:
                logger.warning(f"Reconnect attempt {attempt + 1} failed: {e}")
        return False


