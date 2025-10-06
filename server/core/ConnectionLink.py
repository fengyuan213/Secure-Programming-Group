from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional, Set

from shared.crypto.signer import Signer
import websockets

from shared.envelope import Envelope, create_envelope
from shared.log import get_logger

if TYPE_CHECKING:
    from server.core.MemoryTable import ServerRecord, UserRecord
    from server.server import SOCPServer

from server.core.MessageTypes import MessageType
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

    def __init__(self, websocket: websockets.ClientConnection | websockets.ServerConnection,singer:Signer, local_server: ServerRecord,connection_type: Optional[str] = None):
        self.websocket = websocket
        self.singer = singer
        self.connection_type = connection_type  # "user" or "server"
        self.identified = False
        self.user_id: Optional[str] = None
        self.server_id: Optional[str] = None
        self.local_server: ServerRecord = local_server
        self.last_seen: float = time.monotonic()
        self.is_bootstrap_handshake = False  # Flag for temporary bootstrap connections

        
    def get_user_record(self, server: "SOCPServer") -> Optional[UserRecord]:
        return server.users.get(self.user_id) if self.user_id else None
    async def send_error(
        self,
        error_code: str,
        detail: str,
        *,
        to_id: Optional[str] = None,
    ) -> None:
        """Send ERROR message to connection"""
        target = to_id or self.user_id or self.server_id
        if not target:
            logger.warning("Cannot send ERROR %s: no recipient", error_code)
            return
        payload = {"code": error_code, "detail": detail}
        envelope = create_envelope(
            MessageType.ERROR.value,
            self.local_server.id,
            target,
            payload,
        )
        await self.send_message(envelope)
    
    # Convenience methods for SOCP-compliant error codes (§9.5)
    async def on_error_user_not_found(self, detail: str, *, to_id: Optional[str] = None) -> None:
        """Send USER_NOT_FOUND error - user doesn't exist or not reachable"""
        await self.send_error("USER_NOT_FOUND", detail, to_id=to_id)
    
    async def on_error_invalid_sig(self, detail: str, *, to_id: Optional[str] = None) -> None:
        """Send INVALID_SIG error - signature verification failed"""
        await self.send_error("INVALID_SIG", detail, to_id=to_id)
    
    async def on_error_bad_key(self, detail: str, *, to_id: Optional[str] = None) -> None:
        """Send BAD_KEY error - key/payload errors"""
        await self.send_error("BAD_KEY", detail, to_id=to_id)
    
    async def on_error_timeout(self, detail: str, *, to_id: Optional[str] = None) -> None:
        """Send TIMEOUT error - timeout errors"""
        await self.send_error("TIMEOUT", detail, to_id=to_id)
    
    async def on_error_unknown_type(self, detail: str, *, to_id: Optional[str] = None) -> None:
        """Send UNKNOWN_TYPE error - unknown message type"""
        await self.send_error("UNKNOWN_TYPE", detail, to_id=to_id)
    
    async def on_error_name_in_use(self, detail: str, *, to_id: Optional[str] = None) -> None:
        """Send NAME_IN_USE error - user ID already taken"""
        await self.send_error("NAME_IN_USE", detail, to_id=to_id)
    async def send_message(self, envelope: Envelope) -> None:
        """Send an envelope as JSON over WebSocket No encryption or signing for now"""
        try:
            if envelope.type not in self._ALLOW_UNSIGNED_TYPES:
                envelope.sig = self.singer.sign(envelope.payload)
            json_str = envelope.to_json()
            await self.websocket.send(json_str)
            logger.debug(f"Sent message {envelope.type} to {self.connection_type} to {envelope.to}")
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

