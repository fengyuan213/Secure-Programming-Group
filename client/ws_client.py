from __future__ import annotations
import asyncio
from typing import Optional, Callable, Awaitable, Dict, Set

import websockets

from shared.crypto.signer import Signer
from shared.envelope import Envelope
from shared.log import get_logger

logger = get_logger(__name__)


MessageHandler = Callable[[Envelope], Awaitable[None]]

# Message types that don't require signatures (per SOCP §7)
_ALLOW_UNSIGNED_TYPES: Set[str] = {
    "USER_HELLO",
    "SERVER_HELLO_JOIN",
    "SERVER_HELLO_LINK",
    "SERVER_WELCOME",
    "SERVER_ANNOUNCE",
}


class ClientSession:
    """
    SOCP client session managing WebSocket connection and message handling.
    
    Handles automatic transport signature signing per SOCP §12.
    """
    
    def __init__(self, user_id: str, server_ws_url: str, signer: Signer) -> None:
        self.user_id = user_id
        self.server_ws_url = server_ws_url
        self.signer = signer
        self.websocket: Optional[websockets.ClientConnection] = None
        self.handlers: Dict[str, MessageHandler] = {}
    
    async def connect(self) -> None:
        """Connect to SOCP server via WebSocket"""
        self.websocket = await websockets.connect(self.server_ws_url, ping_interval=15, ping_timeout=45)

    async def send(self, envelope: Envelope) -> None:
        """
        Send envelope with automatic transport signing per SOCP §12.
        
        Note: Content signatures (content_sig in payload) should be added
        before calling this method. This only adds transport signatures (sig field).
        """
        assert self.websocket is not None
        
        # Sign envelope if required and signer is available
        if envelope.type not in _ALLOW_UNSIGNED_TYPES and self.signer is not None:
            # Use canonical payload signing per SOCP §12 (transport signature)
            envelope.sig = self.signer.sign(envelope.payload)
            logger.debug("Signed %s envelope", envelope.type)
        
        await self.websocket.send(envelope.to_json())

    def on(self, msg_type: str, handler: MessageHandler) -> None:
        self.handlers[msg_type] = handler

    async def recv_loop(self, default_handler: Optional[MessageHandler] = None) -> None:
        assert self.websocket is not None
        async for raw in self.websocket:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8')
                env = Envelope.from_json(raw)
                handler = self.handlers.get(env.type, default_handler)
                if handler:
                    await handler(env)
            except Exception as e:
                logger.error("Failed to parse/process inbound frame: %s", e)

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


