from __future__ import annotations

from typing import Optional
import websockets
from websockets.server import WebSocketServerProtocol
from server.core.ConnectionLink import ConnectionLink
from shared.envelope import Envelope

from shared.log import get_logger
logger = get_logger(__name__)
class ConnectionLink:
    """Wrapper around WebSocket connection with connection metadata"""
    
    def __init__(self, websocket: WebSocketServerProtocol, connection_type: Optional[str] = None):
        self.websocket = websocket
        self.connection_type = connection_type  # "user" or "server"
        self.identified = False
        self.user_id: Optional[str] = None
        self.server_id: Optional[str] = None
        
    async def send_message(self, envelope: Envelope) -> None:
        """Send an envelope as JSON over WebSocket No encryption or signing for now"""
        try:
            json_str = envelope.to_json()
            await self.websocket.send(json_str)
            logger.debug(f"Sent message {envelope.type} to {self.connection_type}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"Connection closed while sending {envelope.type}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    async def close(self) -> None:
        """Close the WebSocket connection"""
        try:
            await self.websocket.close(code=1000)  # Normal closure
        except Exception as e:
            logger.error(f"Error closing connection: {e}")

