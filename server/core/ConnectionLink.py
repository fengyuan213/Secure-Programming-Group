from __future__ import annotations

import time
from typing import Optional

from shared.crypto.signer import Signer
import websockets

from shared.envelope import Envelope
from typing import Any, Dict, Optional, Set, Tuple

from shared.log import get_logger
logger = get_logger(__name__)
class ConnectionLink:
    """Wrapper around WebSocket connection with connection metadata"""
    # message types where 'sig' MAY be omitted (first-contact flows) when update this, update envelop.py as well
    _ALLOW_UNSIGNED_TYPES: Set[str] = {
        "USER_HELLO",
        "SERVER_HELLO_JOIN",
        "SERVER_HELLO_LINK",
        "SERVER_WELCOME",
        "SERVER_ANNOUNCE",
    }

    def __init__(self, websocket: websockets.ClientConnection | websockets.ServerConnection,singer:Signer, connection_type: Optional[str] = None):
        self.websocket = websocket
        self.singer = singer
        self.connection_type = connection_type  # "user" or "server"
        self.identified = False
        self.user_id: Optional[str] = None
        self.server_id: Optional[str] = None
        self.last_seen: float = time.monotonic()
        
    async def send_message(self, envelope: Envelope) -> None:
        """Send an envelope as JSON over WebSocket No encryption or signing for now"""
        try:
            if envelope.type not in self._ALLOW_UNSIGNED_TYPES:
                envelope.sig = self.singer.sign(envelope.payload)
            json_str = envelope.to_json()
            await self.websocket.send(json_str)
            logger.debug(f"Sent message {envelope.type} to {self.connection_type}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"Connection closed while sending {envelope.type}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None:
        """Close the WebSocket connection"""
        try:
            await self.websocket.close(code=code, reason=reason or "Connection closed for unknown reason")
        except Exception as e:
            logger.error(f"Error closing connection: {e}")

