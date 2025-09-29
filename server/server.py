#!/usr/bin/env python3

from __future__ import annotations
import asyncio
from dataclasses import dataclass
import os
import json
import tempfile
from pathlib import Path
from contextlib import suppress

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
    
    
    def add_server(self, server_id: str, endpoint: ServerEndpoint, link: Optional[ConnectionLink]):

        record = ServerRecord(id=server_id, link=link, endpoint=endpoint)  # type: ignore[arg-type]
        self.servers[server_id] = record
        self.current_server_id = server_id
        self.server_addrs[server_id] = (endpoint.host, endpoint.port)

        # Track pinned pubkey for gossip/bootstrap lists. Production builds will
        # load the real key from disk; tests rely on this placeholder.
        if server_id not in self.server_pubkeys:
            self.server_pubkeys[server_id] = self.local_pubkey

    def add_local_user(self, user_id: str, link: ConnectionLink):
        record = UserRecord(id=user_id, link=link, location="local")
        self.users[user_id] = record
        
    def __init__(self, host: str = "localhost", port: int = 8765):
        self.servers: Dict[str, ServerRecord] = {} # Map of remote server_id → record for that server. Used to track connected servers and their links.
        self.users: Dict[str, UserRecord] = {} # Map of local user_id → record. High-level registry of users known to this server (locals primarily).

        # initialise required in-memory tables
        self.local_users: Dict[str, ConnectionLink] = {} # Map of local user_id → ConnectionLink. Active WebSocket connections for locally attached users; used to deliver frames to local clients.
        self.user_locations: Dict[str, str] = {} # Map of user_id → "local" or hosting server_id. Network directory used for routing (decides local deliver vs forward to a remote server).
        self.server_addrs: Dict[str, Tuple[str, int]] = {} # Map of server_id → (host, port). Known advertised addresses for servers; used for reconnects and bootstrap
        self.server_pubkeys: Dict[str, str] = {} # Map of server_id → pinned pubkey as advertised in welcome/broadcast frames.
        self.all_connections: Set[ConnectionLink] = set() #  Set of all ConnectionLink objects (both users and servers). Useful for lifecycle management and cleanup.
        self._background_tasks: Set[asyncio.Task] = set()

        # load or create persistent server UUID and register ourselves
        self.local_pubkey = "AA"
        persisted_id = self._load_or_create_server_id()
        self.add_server(persisted_id, ServerEndpoint(host=host, port=port), None)
        
        self.host = host
        self.port = port
        logger.info(f"Initialized SOCP Server with ID: {self.current_server_id}")

    def _track_background_task(self, task: asyncio.Task) -> None:
        """Keep a strong reference to background tasks until completion."""
        self._background_tasks.add(task)

        def _discard(_task: asyncio.Task) -> None:
            self._background_tasks.discard(_task)

        task.add_done_callback(_discard)

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
            # Kick off bootstrap and outbound connection maintenance in background
            bootstrap_task = asyncio.create_task(self.bootstrap())
            connect_task = asyncio.create_task(self._connect_loop())
            # Keep server running
            try:
                await asyncio.Future()  # Run forever
            except asyncio.CancelledError as exc:
                logger.info("Server task cancelled")
                raise RuntimeError("Server cancelled") from exc
            finally:
                for t in (*self._background_tasks, bootstrap_task, connect_task):
                    t.cancel()
                    with suppress(asyncio.CancelledError):
                        await t
    
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
                    await self.send_error(
                        connection,
                        "UNKNOWN_TYPE",
                        str(e),
                        to_id=connection.server_id or connection.user_id,
                    )
                    
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
                logger.debug(
                    "Iter server link -> %s", getattr(link, "server_id", None)
                )
                yield link

    def _verify_server_signature(self, server_id: str, envelope: Envelope) -> bool:
        """Best-effort signature check using pinned pubkeys from bootstrap/HELLO flows."""
        if not is_uuid_v4(server_id):
            return False
        # Local server messages are trusted (we generated them)
        if server_id == self.current_server_id:
            return True
        expected_sig = self.server_pubkeys.get(server_id)
        if not expected_sig:
            logger.warning("No pubkey on record for server %s", server_id)
            return False
        if envelope.sig != expected_sig:
            logger.warning(
                "Signature mismatch for %s: expected %s got %s",
                server_id,
                expected_sig,
                envelope.sig,
            )
            return False
        return True

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
        """Write server_id to state.json atomically to avoid corruption.
            
            WARNING: this causes multiple servers to reuse the same server ID if you run them from the same repository."""
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

    def _bootstrap_path(self) -> Path:
        """Return path to bootstrap YAML file if present."""
        return Path(os.path.dirname(__file__)) / "bootstrap.yaml"

    def _load_bootstrap_list(self) -> list:
        """Load static bootstrap introducers from YAML. Returns list of dicts with host/port/pubkey."""
        path = self._bootstrap_path()
        if not path.exists():
            logger.info("No bootstrap.yaml found; skipping bootstrap")
            return []
        try:
            import yaml  # type: ignore
        except Exception:
            logger.warning("PyYAML not installed; cannot read bootstrap.yaml")
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            entries = data.get("bootstrap_servers") or data or []
            # Normalize to list of dicts
            if isinstance(entries, dict):
                entries = [entries]
            result = []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                host = e.get("host"); port = e.get("port"); pubkey = e.get("pubkey", "")
                if isinstance(host, str) and isinstance(port, int):
                    result.append({"host": host, "port": port, "pubkey": pubkey})
            return result
        except Exception as e:
            logger.error(f"Error reading bootstrap.yaml: {e}")
            return []

    async def bootstrap(self) -> None:
        """Attempt to join via introducers and populate server_addrs; broadcast announcement."""
        introducers = self._load_bootstrap_list()
        if not introducers:
            return
        if len(introducers) < 3:
            logger.warning("Bootstrap list has fewer than 3 introducers; redundancy requirements not met")

        for entry in introducers:
            host = entry["host"]; port = entry["port"]
            # Skip self to avoid self-bootstrapping loops
            if (host == self.host or host in {"127.0.0.1", "localhost"} and self.host in {"127.0.0.1", "localhost"}) and port == self.port:
                logger.info("Skipping bootstrap entry pointing to self")
                continue
            url = f"ws://{host}:{port}"
            try:
                async with websockets.connect(url) as ws:
                    link = ConnectionLink(ws, connection_type="server")
                    # Send SERVER_HELLO_JOIN to introducer (to=host:port per spec)
                    join_env = create_envelope(
                        "SERVER_HELLO_JOIN",
                        self.current_server_id,
                        f"{host}:{port}",
                        {"host": self.host, "port": self.port, "pubkey": self.local_pubkey},
                        signature=self.local_pubkey,
                    )
                    await link.send_message(join_env)
                    # Expect a SERVER_WELCOME (optional wait)
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        welcome = Envelope.from_json(raw)
                        if welcome.type == "SERVER_WELCOME":
                            assigned = welcome.payload.get("assigned_id")
                            if isinstance(assigned, str) and is_uuid_v4(assigned):
                                if assigned != self.current_server_id:
                                    self.current_server_id = assigned
                                    self._persist_server_id_atomic(assigned)
                            # introducer identity from frame
                            introducer_id = welcome.from_
                            if is_uuid_v4(introducer_id):
                                self.server_addrs[introducer_id] = (host, port)
                                if entry.get("pubkey"):
                                    self.server_pubkeys[introducer_id] = entry.get("pubkey", "")
                            # Populate server_addrs from payload (accept key 'servers')
                            peers = welcome.payload.get("servers") or []
                            for p in peers:
                                if not isinstance(p, dict):
                                    continue
                                sid = p.get("server_id")
                                h = p.get("host"); prt = p.get("port")
                                if isinstance(sid, str) and isinstance(h, str) and isinstance(prt, int):
                                    if sid != self.current_server_id:
                                        self.server_addrs[sid] = (h, prt)
                                        if "pubkey" in p and isinstance(p.get("pubkey"), str):
                                            self.server_pubkeys[sid] = p.get("pubkey", "")
                    except asyncio.TimeoutError:
                        pass
                    # Announce ourselves network-wide
                    await self.broadcast_server_announce(self.current_server_id, {"host": self.host, "port": self.port, "pubkey": self.local_pubkey})
                    break  # stop after first successful introducer
            except Exception as e:
                logger.warning(f"Bootstrap to {url} failed: {e}")
                continue

    async def _connect_loop(self) -> None:
        """Background loop to establish outbound connections to known servers."""
        while True:
            try:
                await self.connect_to_known_servers()
            except Exception as e:
                logger.warning(f"connect_to_known_servers error: {e}")
            await asyncio.sleep(2)

    async def connect_to_known_servers(self) -> None:
        """Attempt outbound connections to all entries in server_addrs that aren't connected."""
        for server_id, (h, p) in list(self.server_addrs.items()):
            if server_id == self.current_server_id:
                continue
            # If already connected (link present), skip
            existing = self.servers.get(server_id)
            if isinstance(existing, ConnectionLink):
                continue
            link: Optional[ConnectionLink] = None
            try:
                url = f"ws://{h}:{p}"
                logger.info(f"Connecting to server {server_id} at {h}:{p}")
                ws = await websockets.connect(url)
                link = ConnectionLink(ws, connection_type="server")
                link.server_id = server_id
                link.identified = True
                self.all_connections.add(link)
                # Identify ourselves with SERVER_HELLO_LINK (to = remote server_id)
                link_env = create_envelope(
                    "SERVER_HELLO_LINK",
                    self.current_server_id,
                    server_id,
                    {"host": self.host, "port": self.port, "pubkey": "AA"},
                    signature="AA",
                )
                await link.send_message(link_env)
                # Optional: await welcome
                with suppress(asyncio.TimeoutError):
                    initial = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    await self.process_message(link, initial)
                # Register link
                link.connection_type = "server"
                self.servers[server_id] = link
                reader = asyncio.create_task(self._run_server_link(link))
                self._track_background_task(reader)
                logger.info(f"Connected and linked to server {server_id} at {h}:{p}")
            except Exception as e:
                logger.debug(f"Connect to {server_id}@{h}:{p} failed: {e}")
                if link and link in self.all_connections:
                    self.all_connections.discard(link)
                if link is not None:
                    with suppress(Exception):
                        await link.close()

    async def _run_server_link(self, link: ConnectionLink) -> None:
        """Continuously read frames from an outbound server link."""
        try:
            while True:
                message = await link.websocket.recv()
                await self.process_message(link, message)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Server link %s closed", getattr(link, "server_id", None))
        except Exception as exc:
            logger.error("Error on server link %s: %s", getattr(link, "server_id", None), exc)
        finally:
            await self.cleanup_connection(link)

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
            elif envelope.type == "SERVER_HELLO_LINK":
                await self.handle_server_hello_link(connection, envelope)
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
            await self.send_error(connection, "BAD_KEY", "from must be UUIDv4", to_id=envelope.from_)
            return
        
        # Validate user_id is not already in use locally
        if user_id in self.local_users:
            logger.warning(f"User ID {user_id} already in use")
            await self.send_error(connection, "NAME_IN_USE", f"User {user_id} already connected", to_id=user_id)
            return
        
        # Validate payload structure
        required_fields = {"client", "pubkey", "enc_pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await self.send_error(connection, "BAD_KEY", f"Missing required fields: {missing}", to_id=user_id)
            return
        
        # Register the user
        connection.connection_type = "user"
        connection.user_id = user_id
        connection.identified = True
        
        self.local_users[user_id] = connection
        self.user_locations[user_id] = "local"
        # Maintain unified user table for presence tracking
        self.add_local_user(user_id, connection)
        
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
        requested_server_id = envelope.from_

        # Ignore self-join attempts
        if requested_server_id == self.current_server_id:
            logger.warning("Ignoring SERVER_HELLO_JOIN from self")
            return

        # Validate payload structure
        required_fields = {"host", "port", "pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await self.send_error(connection, "BAD_KEY", f"Missing required fields: {missing}", to_id=envelope.from_)
            return

        # Assign server ID ensuring uniqueness as per spec
        assigned_server_id = requested_server_id if is_uuid_v4(requested_server_id) else str(uuid.uuid4())
        if assigned_server_id in self.server_addrs or assigned_server_id == self.current_server_id:
            logger.info(f"Reassigning duplicate server_id {requested_server_id}")
            assigned_server_id = str(uuid.uuid4())
            while assigned_server_id in self.server_addrs or assigned_server_id == self.current_server_id:
                assigned_server_id = str(uuid.uuid4())

        # Register the server
        connection.connection_type = "server"
        connection.server_id = assigned_server_id
        connection.identified = True

        self.servers[assigned_server_id] = connection
        self.server_addrs[assigned_server_id] = (envelope.payload["host"], envelope.payload["port"])
        self.server_pubkeys[assigned_server_id] = envelope.payload.get("pubkey", "")

        logger.info(f"Server {assigned_server_id} joined from {envelope.payload['host']}:{envelope.payload['port']}")

        # Send SERVER_WELCOME response (for now, simple acknowledgment)
        welcome_payload = {
            "assigned_id": assigned_server_id,  # Keep same ID if unique
            "servers": [
                {
                    "server_id": sid,
                    "host": addr[0],
                    "port": addr[1],
                    "pubkey": self.server_pubkeys.get(sid, ""),
                }
                for sid, addr in self.server_addrs.items()
                if sid != assigned_server_id
            ],
        }
        welcome_envelope = create_envelope(
            "SERVER_WELCOME",
            self.current_server_id,
            assigned_server_id,
            welcome_payload
        )
        await connection.send_message(welcome_envelope)

        # Broadcast SERVER_ANNOUNCE to all other servers
        await self.broadcast_server_announce(assigned_server_id, envelope.payload)

    async def handle_server_hello_link(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """Handle SERVER_HELLO_LINK for direct link establishment after welcome list."""
        server_id = envelope.from_
        # Validate payload
        required_fields = {"host", "port", "pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await self.send_error(connection, "BAD_KEY", f"Missing required fields: {missing}", to_id=envelope.from_)
            return
        # Register/replace link
        connection.connection_type = "server"
        connection.server_id = server_id
        connection.identified = True
        self.servers[server_id] = connection
        self.server_addrs[server_id] = (envelope.payload["host"], envelope.payload["port"])
        self.server_pubkeys[server_id] = envelope.payload.get("pubkey", "")
        logger.info(f"Linked server {server_id} at {envelope.payload['host']}:{envelope.payload['port']}")
    
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
            await self.send_error(
                connection,
                "UNKNOWN_TYPE",
                str(e),
                to_id=connection.server_id or connection.user_id,
            )
    
    async def handle_user_message(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """Handle messages from user connections"""
        # TODO: Implement user message handling (MSG_DIRECT, MSG_PUBLIC_CHANNEL, etc.)
        logger.info(f"User message {envelope.type} from {connection.user_id}")
    
    async def handle_server_message(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """Handle messages from server connections"""  
        # TODO: Implement server message handling (USER_ADVERTISE, SERVER_DELIVER, etc.)
        msg_type = envelope.type
        origin_server_id = envelope.from_
        logger.info(
            "Server message %s from %s via link %s",
            msg_type,
            origin_server_id,
            connection.server_id,
        )

        # PRESENCE GOSSIP: maintain user_locations
        if msg_type in {"USER_ADVERTISE", "USER_REMOVE"}:
            payload = envelope.payload
            user_id = payload.get("user_id")
            server_id = payload.get("server_id")

            if not (isinstance(user_id, str) and isinstance(server_id, str)):
                await self.send_error(
                    connection,
                    "UNKNOWN_TYPE",
                    f"Malformed {msg_type} payload",
                    to_id=envelope.from_,
                )
                return

            # Ensure we trust the sending server before mutating shared state
            if not self._verify_server_signature(origin_server_id, envelope):
                logger.warning(
                    "Rejected %s from %s due to signature mismatch",
                    msg_type,
                    origin_server_id,
                )
                return

            if server_id != origin_server_id:
                logger.debug(
                    "Forwarded %s claims host %s while signed by %s", msg_type, server_id, origin_server_id
                )

            if msg_type == "USER_ADVERTISE":
                # Advertise: map the user to the announced hosting server
                self.user_locations[user_id] = server_id
                self.users[user_id] = UserRecord(id=user_id, link=None, location=server_id)
            else:
                # USER_REMOVE: only delete if mapping still matches the sender's claim
                if self.user_locations.get(user_id) == server_id:
                    del self.user_locations[user_id]
                if user_id in self.users and self.users[user_id].location == server_id:
                    del self.users[user_id]

            # Gossip to other connected servers unchanged
            for link in self._iter_server_links():
                if link is connection:
                    continue
                if link.server_id and link.server_id == origin_server_id:
                    continue
                logger.debug(
                    "Forwarding %s for %s to server %s",
                    msg_type,
                    user_id,
                    link.server_id,
                )
                await link.send_message(envelope)
            return

        # TODO: Other server message types will be implemented later (e.g., SERVER_DELIVER)
    
    async def broadcast_user_advertise(self, user_id: str, meta: Dict[str, Any]) -> None:
        """Broadcast USER_ADVERTISE to all connected servers"""
        payload = {
            "user_id": user_id,
            "server_id": self.current_server_id,
            "meta": meta
        }
        envelope = create_envelope(
            "USER_ADVERTISE",
            self.current_server_id,
            "*",
            payload,
            signature=self.local_pubkey,
        )
        
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

        self.server_addrs[server_id] = (server_info["host"], server_info["port"])
        self.server_pubkeys[server_id] = server_info.get("pubkey", "")

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
    
    async def send_error(
        self,
        connection: ConnectionLink,
        error_code: str,
        detail: str,
        *,
        to_id: Optional[str] = None,
    ) -> None:
        """Send ERROR message to connection"""
        target = to_id or connection.user_id or connection.server_id
        if not target:
            logger.warning("Cannot send ERROR %s: no recipient", error_code)
            return
        payload = {"code": error_code, "detail": detail}
        envelope = create_envelope(
            "ERROR",
            self.current_server_id,
            target,
            payload,
            signature=self.local_pubkey,
        )
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
        if user_id in self.users:
            del self.users[user_id]
        
        # Broadcast USER_REMOVE
        payload = {"user_id": user_id, "server_id": self.current_server_id}
        envelope = create_envelope(
            "USER_REMOVE",
            self.current_server_id,
            "*",
            payload,
            signature=self.local_pubkey,
        )
        
        for server_link in self._iter_server_links():
            await server_link.send_message(envelope)
        
        logger.info(f"Cleaned up user {user_id}")
    
    async def cleanup_server_connection(self, server_id: str) -> None:
        """Clean up server connection"""
        if server_id in self.servers:
            del self.servers[server_id]
            
        # Remove any user_locations that point to this server as host
        stale_users = [u for u, loc in self.user_locations.items() if loc == server_id]
        for u in stale_users:
            del self.user_locations[u]
            
        # TODO: Attempt reconnection after delay
        
        logger.info(f"Cleaned up server {server_id}")


async def main():
    """Main entry point"""

    server = SOCPServer(host="localhost", port=8765)
    await server.start_server()
if __name__ == "__main__":
    asyncio.run(main())
