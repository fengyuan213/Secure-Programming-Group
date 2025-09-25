#!/usr/bin/env python3

from __future__ import annotations
import asyncio
from dataclasses import dataclass
import os
import json
import tempfile

import uuid
from typing import Dict, NamedTuple, Optional, Set, Tuple, Any
from server.core.MemoryTable import *
import websockets
from websockets.server import WebSocketServerProtocol

from server.core.ConnectionLink import ConnectionLink
from shared.envelope import Envelope, create_envelope
from shared.utils import is_uuid_v4
from shared.log import get_logger

# Confiure Logging
logger = get_logger(__name__)
class SOCPServer:
    current_server_id: str
    servers: Dict[str, ServerRecord] 
    users: Dict[str, UserRecord] 
    
    
    def add_server(self, server_id: str, endpoint: ServerEndpoint, link: ConnectionLink):
       
        record = ServerRecord(id=server_id, link=link, endpoint=endpoint)
        self.servers[server_id] = record
        self.current_server_id = server_id

    def add_local_user(self, user_id: str, link: ConnectionLink):
        record = UserRecord(id=user_id, link=link, location="local")
        self.users[user_id] = record
        
    def __init__(self, host: str = "localhost", port: int = 8765):
        self.servers: Dict[str, ServerRecord] = {}
        self.users: Dict[str, UserRecord] = {}

        # initialise required in-memory tables
        self.local_users: Dict[str, ConnectionLink] = {}
        self.user_locations: Dict[str, str] = {}
        self.server_addrs: Dict[str, Tuple[str, int]] = {}
        self.all_connections: Set[ConnectionLink] = set()
        
        # load or create persistent server UUID and register ourselves
        persisted_id = self._load_or_create_server_id()
        self.add_server(persisted_id, ServerEndpoint(host=host, port=port), None)
        
        self.host = host
        self.port = port
        logger.info(f"Initialized SOCP Server with ID: {self.current_server_id}")
    
    async def start_server(self) -> None:
        """Start the WebSocket server"""
        logger.info(f"Starting SOCP server on {self.host}:{self.port}")
        
        async with websockets.serve(
            self.handle_connection,
            self.host,
            self.port,
            ping_interval=15,  # Send ping every 15s (heartbeat support)
            ping_timeout=45,   # Timeout after 45s without pong
        ):
            logger.info(f"SOCP server listening on ws://{self.host}:{self.port}")
            # Keep server running
            await asyncio.Future()  # Run forever
    
    async def handle_connection(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """
        Handle new WebSocket connection
        
        Per SOCP spec:
        - A connecting Server/User MUST send an identifying first message
        - For servers: SERVER_HELLO_JOIN (Section 8.1)
        - For users: USER_HELLO (Section 9.1)
        """
        # NOT FINISHED SKETON ONLY
        connection = ConnectionLink(websocket)
        self.all_connections.add(connection)
        
        remote_addr = websocket.remote_address
        logger.info(f"New connection from {remote_addr}")
        
        try:
            # Wait for first message to identify connection type
            await self.handle_first_message(connection)
            
            # Continue handling messages for identified connection
            async for message in websocket:
                try:
                    await self.process_message(connection, message)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response if possible
                    await self.send_error(connection, "UNKNOWN_TYPE", str(e))
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection {remote_addr} closed")
        except Exception as e:
            logger.error(f"Error handling connection {remote_addr}: {e}")
        finally:
            await self.cleanup_connection(connection)
    
    def _iter_server_links(self):
        """Yield ConnectionLink objects for all connected remote servers."""
        for value in self.servers.values():
            # value may be a ConnectionLink (post-HELLO) or a ServerRecord (bootstrap entry)
            link = None
            if isinstance(value, ConnectionLink):
                link = value
            else:
                # Try to access .link on ServerRecord
                try:
                    link = value.link  # type: ignore[attr-defined]
                except Exception:
                    link = None
            if link is not None:
                yield link

    def _state_path(self) -> str:
        """Return path to server state file (JSON)."""
        # Place next to this module: server/state.json
        return os.path.join(os.path.dirname(__file__), "state.json")

    def _load_or_create_server_id(self) -> str:
        """Load persisted server_id if valid; otherwise create, persist, and return a new UUIDv4."""
        path = self._state_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sid = data.get("server_id")
                if isinstance(sid, str) and is_uuid_v4(sid):
                    return sid
        except Exception:
            # Corrupt or unreadable state; fall through to regenerate
            pass
        # Generate new and persist
        new_id = str(uuid.uuid4())
        self._persist_server_id_atomic(new_id)
        return new_id

    def _persist_server_id_atomic(self, server_id: str) -> None:
        """Write server_id to state.json atomically to avoid corruption."""
        path = self._state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"server_id": server_id}
        # Write to a temp file then replace
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(prefix="state.", suffix=".json", dir=dir_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(payload, tmp, separators=(",", ":"), sort_keys=True)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, path)
        finally:
            # If replace failed, ensure temp is cleaned up
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def handle_first_message(self, connection: ConnectionLink) -> None:
        """
        Handle the first message to identify connection type
        
        S0.2 First-message identification:
        - USER_HELLO: identifies as user connection
        - SERVER_HELLO_JOIN: identifies as server connection
        """
        try:
            # Wait for first message with timeout
            first_message = await asyncio.wait_for(
                connection.websocket.recv(), 
                timeout=30.0  # 30 second timeout for first message
            )
            
            # Parse the message
            envelope = Envelope.from_json(first_message)
            logger.info(f"First message type: {envelope.type} from {envelope.from_}")
            
            # Identify connection type based on first message
            if envelope.type == "USER_HELLO":
                await self.handle_user_hello(connection, envelope)
            elif envelope.type == "SERVER_HELLO_JOIN":
                await self.handle_server_hello_join(connection, envelope)
            else:
                raise ValueError(f"Invalid first message type: {envelope.type}")
                
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for first message")
            raise
        except Exception as e:
            logger.error(f"Error handling first message: {e}")
            raise
    
    async def handle_user_hello(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """
        Handle USER_HELLO message (Section 9.1)
        
        Payload must contain:
        - client: string (e.g., "cli-v1")
        - pubkey: base64url RSA-4096 public key
        - enc_pubkey: base64url RSA-4096 encryption key (can duplicate pubkey)
        """
        user_id = envelope.from_

        # Defensive: ensure UUIDv4 (Envelope validator should have enforced already)
        if not is_uuid_v4(user_id):
            await self.send_error(connection, "BAD_KEY", "from must be UUIDv4")
            return
        
        # Validate user_id is not already in use locally
        if user_id in self.local_users:
            logger.warning(f"User ID {user_id} already in use")
            await self.send_error(connection, "NAME_IN_USE", f"User {user_id} already connected")
            return
        
        # Validate payload structure
        required_fields = {"client", "pubkey", "enc_pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await self.send_error(connection, "BAD_KEY", f"Missing required fields: {missing}")
            return
        
        # Register the user
        connection.connection_type = "user"
        connection.user_id = user_id
        connection.identified = True
        
        self.local_users[user_id] = connection
        self.user_locations[user_id] = "local"
        
        logger.info(f"User {user_id} connected with client {envelope.payload['client']}")
        
        # Broadcast USER_ADVERTISE to all servers
        await self.broadcast_user_advertise(user_id, envelope.payload.get("meta", {}))
    
    async def handle_server_hello_join(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """
        Handle SERVER_HELLO_JOIN message (Section 8.1)
        
        This is the bootstrap flow where a new server joins the network.
        Payload must contain:
        - host: string (server's IP)
        - port: int (server's WebSocket port)  
        - pubkey: base64url RSA-4096 public key
        """
        server_id = envelope.from_
        
        # Validate server_id is not already registered
        if server_id in self.servers:
            logger.warning(f"Server ID {server_id} already registered")
            # For now, allow reconnection - could be server restart
            await self.cleanup_server_connection(server_id)
        
        # Validate payload structure
        required_fields = {"host", "port", "pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await self.send_error(connection, "BAD_KEY", f"Missing required fields: {missing}")
            return
        
        # Register the server
        connection.connection_type = "server"
        connection.server_id = server_id
        connection.identified = True
        
        self.servers[server_id] = connection
        self.server_addrs[server_id] = (envelope.payload["host"], envelope.payload["port"])
        
        logger.info(f"Server {server_id} joined from {envelope.payload['host']}:{envelope.payload['port']}")
        
        # Send SERVER_WELCOME response (for now, simple acknowledgment)
        welcome_payload = {
            "assigned_id": server_id,  # Keep same ID if unique
            "clients": []  # TODO: Include current server list
        }
        welcome_envelope = create_envelope(
            "SERVER_WELCOME",
            self.current_server_id,
            server_id,
            welcome_payload
        )
        await connection.send_message(welcome_envelope)
        
        # Broadcast SERVER_ANNOUNCE to all other servers
        await self.broadcast_server_announce(server_id, envelope.payload)
    
    async def process_message(self, connection: ConnectionLink, message: str) -> None:
        """Process messages from identified connections"""
        try:
            envelope = Envelope.from_json(message)
            logger.debug(f"Processing {envelope.type} from {connection.connection_type}")
            
            # Route message based on type and connection
            if connection.connection_type == "user":
                await self.handle_user_message(connection, envelope)
            elif connection.connection_type == "server":
                await self.handle_server_message(connection, envelope)
            else:
                logger.warning(f"Message from unidentified connection: {envelope.type}")
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self.send_error(connection, "UNKNOWN_TYPE", str(e))
    
    async def handle_user_message(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """Handle messages from user connections"""
        # TODO: Implement user message handling (MSG_DIRECT, MSG_PUBLIC_CHANNEL, etc.)
        logger.info(f"User message {envelope.type} from {connection.user_id}")
    
    async def handle_server_message(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """Handle messages from server connections"""  
        # TODO: Implement server message handling (USER_ADVERTISE, SERVER_DELIVER, etc.)
        logger.info(f"Server message {envelope.type} from {connection.server_id}")
    
    async def broadcast_user_advertise(self, user_id: str, meta: Dict[str, Any]) -> None:
        """Broadcast USER_ADVERTISE to all connected servers"""
        payload = {
            "user_id": user_id,
            "server_id": self.current_server_id,
            "meta": meta
        }
        envelope = create_envelope("USER_ADVERTISE", self.current_server_id, "*", payload)
        
        # Send to all connected servers
        for server_link in self._iter_server_links():
            await server_link.send_message(envelope)
        
        logger.info(f"Broadcasted USER_ADVERTISE for {user_id}")
    
    async def broadcast_server_announce(self, server_id: str, server_info: Dict[str, Any]) -> None:
        """Broadcast SERVER_ANNOUNCE to all other servers"""
        payload = {
            "host": server_info["host"],
            "port": server_info["port"], 
            "pubkey": server_info["pubkey"]
        }
        envelope = create_envelope("SERVER_ANNOUNCE", server_id, "*", payload)
        
        # Send to all other connected servers
        for other_server_id, value in self.servers.items():
            if other_server_id == server_id:
                continue
            # Resolve link
            link = value if isinstance(value, ConnectionLink) else getattr(value, "link", None)
            if link is not None:
                await link.send_message(envelope)
        
        logger.info(f"Broadcasted SERVER_ANNOUNCE for {server_id}")
    
    async def send_error(self, connection: ConnectionLink, error_code: str, detail: str) -> None:
        """Send ERROR message to connection"""
        payload = {
            "code": error_code,
            "detail": detail
        }
        envelope = create_envelope("ERROR", self.current_server_id, 
                                 connection.user_id or connection.server_id or "unknown", 
                                 payload)
        await connection.send_message(envelope)
    
    async def cleanup_connection(self, connection: ConnectionLink) -> None:
        """Clean up when connection closes"""
        self.all_connections.discard(connection)
        
        if connection.user_id:
            await self.cleanup_user_connection(connection.user_id)
        elif connection.server_id:
            await self.cleanup_server_connection(connection.server_id)
        
        await connection.close()
    
    async def cleanup_user_connection(self, user_id: str) -> None:
        """Clean up user connection and broadcast USER_REMOVE"""
        if user_id in self.local_users:
            del self.local_users[user_id]
        if user_id in self.user_locations:
            del self.user_locations[user_id]
        
        # Broadcast USER_REMOVE
        payload = {"user_id": user_id, "server_id": self.current_server_id}
        envelope = create_envelope("USER_REMOVE", self.current_server_id, "*", payload)
        
        for server_link in self._iter_server_links():
            await server_link.send_message(envelope)
        
        logger.info(f"Cleaned up user {user_id}")
    
    async def cleanup_server_connection(self, server_id: str) -> None:
        """Clean up server connection"""
        if server_id in self.servers:
            del self.servers[server_id]
        if server_id in self.server_addrs:
            del self.server_addrs[server_id]
        
        # TODO: Update user_locations to remove references to this server
        # TODO: Attempt reconnection after delay
        
        logger.info(f"Cleaned up server {server_id}")


async def main():
    """Main entry point"""

    server = SOCPServer(host="localhost", port=8765)
    await server.start_server()
if __name__ == "__main__":
    asyncio.run(main())
