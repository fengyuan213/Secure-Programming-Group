#!/usr/bin/env python3

from __future__ import annotations
import asyncio
from dataclasses import dataclass
from mimetypes import init
import os
import json
import tempfile
from pathlib import Path
from contextlib import suppress
import time

import uuid
from typing import Dict, NamedTuple, Optional, Set, Tuple, Any
from server.core.MemoryTable import *
from shared.crypto.crypto import rsassa_pss_verify
from shared.crypto.keys import RSAKeypair, load_keypair, save_keypair

from shared.crypto.signer import RSATransportSigner
import websockets

from server.core.ConnectionLink import ConnectionLink
from server.core.MessageCache import MessageDeduplicationCache
from server.core.MessageTypes import MessageType, ConnectionType
from server.core.MessageHandlers import SERVER_HANDLER_REGISTRY, USER_HANDLER_REGISTRY
from server.core.PublicChannelManager import PublicChannelManager
from server.storage import SOCPStorage
from shared.envelope import BadKeyError, Envelope, InvalidSigError, NameInUseError, UnknownTypeError, UserNotFoundError, create_envelope, verify_transport_envelope
from shared.utils import is_uuid_v4
from shared.log import configure_root_logging, get_logger

# Confiure Logging
logger = get_logger(__name__)

class SOCPServer:

    def add_update_server(
        self,
        server_id: str,
        endpoint: Optional[ServerEndpoint] = None,
        link: Optional[ConnectionLink] = None,
        pubkey: Optional[str] = None,
    ) -> None:
        existing = self.servers.get(server_id)
        if existing:
            if endpoint is not None:
                existing.endpoint = endpoint
            if link is not None:
                existing.link = link
            if pubkey is not None:
                existing.pubkey = pubkey
            return

        # creating new record: require mandatory fields
        if endpoint is None or pubkey is None:
            raise ValueError("endpoint and pubkey required for new server")
        self.servers[server_id] = ServerRecord(id=server_id, link=link, endpoint=endpoint, pubkey=pubkey)
    def add_update_user(self, user_id: str, link: ConnectionLink | None, location: str):
        record = UserRecord(id=user_id, link=link, location=location)
        self.users[user_id] = record
    @property    
    def connected_users(self) -> Dict[str, UserRecord]:
        return {k: v for k, v in self.users.items() if v.link is not None}
    
    @property
    def connected_local_users(self) -> Dict[str, UserRecord]:
        return {k: v for k, v in self.users.items() if v.location == "local" and v.link is not None}
    @property
    def all_server_connection_links(self) -> Set[ConnectionLink]:
        """Derive all connections from servers and users"""
        connections = set()
        
        # Add server connections
        connections.update(
            record.link for record in self.servers.values()
            if record.link is not None
        )
        
        return connections

        
    def __init__(
        self,
        storage_path: Path,
        host: str = "localhost",
        port: int = 8765,
        *,
        heartbeat_interval: float = 15.0,
        heartbeat_timeout: float = 45.0,
        dedup_ttl: float = 120.0,
        autosave_interval: float = 60.0,  # Save state every 60 seconds
    ):
        
        # These two are single source of truth for all servers and users on the network.
        self.servers: Dict[str, ServerRecord] = {} # Map of remote server_id → record for that server. Used to track connected servers and their links.
        self.users: Dict[str, UserRecord] = {} # Map of local user_id → record. High-level registry of users known to this server (locals primarily).
        self.storage_path = storage_path
        self._background_tasks: Set[asyncio.Task] = set()

        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.autosave_interval = autosave_interval
        
        # Track reconnection attempts per server (for exponential backoff)
        self._reconnection_attempts: Dict[str, int] = {}

        # Prevent concurrent reconnections to same server
        self._reconnecting_servers: Set[str] = set()
        
        # Duplicate suppression per SOCP §10
        self.message_cache = MessageDeduplicationCache(ttl=dedup_ttl)
        
        # Initialize persistent storage
        self.storage = SOCPStorage(storage_path)
        
        # Public channel manager per SOCP §9.3
        self.public_channel = PublicChannelManager(self)

        # Initialize storage path for pubkey directory
        self.keypair =  self._load_or_create_server_key()
        self.signer = RSATransportSigner(self.keypair)
        
        
        # load or create persistent server UUID and register ourselves
  
        persisted_id = self._load_or_create_server_id()
        self.add_update_server(persisted_id, ServerEndpoint(host=host, port=port), None, self.keypair.public_b64url())
        
        self.local_server = ServerRecord(id=persisted_id, link=None, endpoint=ServerEndpoint(host=host, port=port), pubkey=self.keypair.public_b64url())
       
        logger.info(f"Initialized SOCP Server with ID: {self.local_server.id}")

    def _track_background_task(self, task: asyncio.Task) -> None:
        """Keep a strong reference to background tasks until completion."""
        self._background_tasks.add(task)

        def _discard(_task: asyncio.Task) -> None:
            self._background_tasks.discard(_task)

        task.add_done_callback(_discard)

    async def start_server(self) -> None:
        """Start the WebSocket server"""
        logger.info(f"Starting SOCP server on {self.local_server.endpoint.host}:{self.local_server.endpoint.port}")
        
        async with websockets.serve(
            self.handle_connection,
            self.local_server.endpoint.host,
            self.local_server.endpoint.port,
            ping_interval=15,  # Send ping every 15s (heartbeat support)
            ping_timeout=45,   # Timeout after 45s without pong
        ):
            logger.info(f"SOCP server listening on ws://{self.local_server.endpoint.host}:{self.local_server.endpoint.port}")
            # Kick off bootstrap and outbound connection maintenance in background
            for coroutine in (self.bootstrap(), self._connect_loop(), self._health_loop()):
                task = asyncio.create_task(coroutine)
                self._track_background_task(task)
            # Keep server running
            try:
                await asyncio.Future()  # Run forever
            except asyncio.CancelledError as exc:
                logger.info("Server task cancelled")
                raise RuntimeError("Server cancelled") from exc
            finally:
                for task in list(self._background_tasks):
                    task.cancel()
                    
                    with suppress(asyncio.CancelledError):
                        await task
    
    async def handle_connection(self, websocket: websockets.ServerConnection) -> None:
        """
        Handle new WebSocket connection
        
        Per SOCP spec:
        - A connecting Server/User MUST send an identifying first message
        - For servers: SERVER_HELLO_JOIN (Section 8.1)
        - For users: USER_HELLO (Section 9.1)
        """
        # NOT FINISHED SKETON ONLY
        
        connection = ConnectionLink(websocket, self.signer, self.local_server)
     
        remote_addr = websocket.remote_address
        logger.info(f"New connection from {remote_addr}")
        
        try:
            # Wait for first message to identify connection type
            await self.handle_first_message(connection)
            
            # Continue handling messages for identified connection
            async for message in websocket:
                try:
                    connection.last_seen = time.monotonic()
                    if isinstance(message, bytes):
                        message = message.decode("utf-8")
                    await self.process_message(connection, message)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response if possible
                    await connection.on_error_unknown_type(str(e))
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection {remote_addr} closed")
        except Exception as e:
            logger.error(f"Error handling connection {remote_addr}: {e}")
        finally:
            await self.cleanup_connection(connection)
    


    def _resolve_server_link(self, server_id: str) -> Optional[ConnectionLink]:
        """Return the ConnectionLink for the given server_id if connected."""
        entry = self.servers.get(server_id)
        if isinstance(entry, ConnectionLink):
            return entry
        return getattr(entry, "link", None)


    def _verify_server_signature(self, server_id: str, envelope: Envelope) -> bool:
        """Verify RSASSA-PSS over canonicalized payload using the sender's pinned pubkey.

        The canonical form is JSON.dumps(payload, separators=(",", ":"), sort_keys=True).
        Pubkeys are pinned from bootstrap.yaml, SERVER_WELCOME.servers[*].pubkey, and
        SERVER_ANNOUNCE frames.
        """
        if not is_uuid_v4(server_id):
            return False
        
        record = self.servers.get(server_id)
        pubkey_b64url = getattr(record, "pubkey", "") if record is not None else ""
        if not pubkey_b64url:
            logger.warning("No pinned pubkey for server %s; cannot verify signature", server_id)
            return False

        if envelope.sig is None:
            logger.warning("Missing signature on message %s from %s", envelope.type, server_id)
            return False
        try:
            ok = verify_transport_envelope(envelope, pubkey_b64url)
            if not ok:
                logger.warning("Transport signature verification failed for %s from %s", envelope.type, server_id)
            return ok
        except Exception as exc:
            logger.error("Failed to canonicalize payload for signature verification: %s", exc)
            return False

      


    def _load_or_create_server_id(self) -> str:
        """Load persisted server_id if valid; otherwise create, persist, and return a new UUIDv4."""
        path = self.storage_path / "server_id.json"
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
        path = self.storage_path / "server_id.json"
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
    def _load_or_create_server_key(self) -> RSAKeypair:
        """Load existing RSA keys or generate new ones"""
        def generate_new_key(key_path: Path):
            keypair = RSAKeypair.generate()
            save_keypair(key_path, keypair)
            return keypair
        
        key_path =self.storage_path / "server_keys/pem"
        if os.path.exists(key_path):
            keypair = load_keypair(key_path)
            if not keypair:
                keypair = generate_new_key(key_path)
            return keypair
        return generate_new_key(key_path)
        
       
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
            entries = data.get("bootstrap_servers", []) if isinstance(data, dict) else []
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
            if (host == self.local_server.endpoint.host or host in {"127.0.0.1", "localhost"} and self.local_server.endpoint.host in {"127.0.0.1", "localhost"}) and port == self.local_server.endpoint.port:
                logger.info("Skipping bootstrap entry pointing to self")
                continue
            url = f"ws://{host}:{port}"
            try:
                async with websockets.connect(url) as ws:
                    link = ConnectionLink(ws, self.signer, self.local_server, connection_type="server")
                    # Send SERVER_HELLO_JOIN to introducer (to=host:port per spec)
                    join_env = create_envelope(
                        MessageType.SERVER_HELLO_JOIN.value,
                        self.local_server.id,
                        f"{host}:{port}",
                        {"host": self.local_server.endpoint.host, "port": self.local_server.endpoint.port, "pubkey": self.local_server.pubkey},
                    )
                    await link.send_message(join_env)
                    # Expect a SERVER_WELCOME (optional wait)
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        welcome = Envelope.from_json(raw)
                        if welcome.type == MessageType.SERVER_WELCOME.value:
                            assigned = welcome.payload.get("assigned_id")
                            if isinstance(assigned, str) and is_uuid_v4(assigned):
                                if assigned != self.local_server.id:
                                    self.local_server.id = assigned
                                    self._persist_server_id_atomic(assigned)
                            # introducer identity from frame
                            introducer_id = welcome.from_
                            if is_uuid_v4(introducer_id):
                                self.add_update_server(introducer_id, ServerEndpoint(host=host, port=port), link, entry.get("pubkey", ""))
                                 #self.server_addrs[introducer_id] = (host, port)
                                #if entry.get("pubkey"):
                                #    self.server_pubkeys[introducer_id] = entry.get("pubkey", "")
                            # Populate server_addrs from payload (accept key 'servers')
                            peers = welcome.payload.get("servers") or []
                            for p in peers:
                                if not isinstance(p, dict):
                                    continue
                                sid = p.get("server_id")
                                h = p.get("host"); prt = p.get("port")
                                if isinstance(sid, str) and isinstance(h, str) and isinstance(prt, int):
                                    if sid != self.local_server.id:
                                        self.add_update_server(sid, ServerEndpoint(host=h, port=prt), None, p.get("pubkey", ""))
                                        #self.server_addrs[sid] = (h, prt)
                                        #if "pubkey" in p and isinstance(p.get("pubkey"), str):
                                        #    self.server_pubkeys[sid] = p.get("pubkey", "")
                    except asyncio.TimeoutError:
                        pass
                    # Announce ourselves network-wide
                    await self.broadcast_server_announce(self.local_server.id, {"host": self.local_server.endpoint.host, "port": self.local_server.endpoint.port, "pubkey": self.local_server.pubkey})
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

    async def _health_loop(self) -> None:
        """Periodically send heartbeats, close stale server links, and prune message cache."""
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            now = time.monotonic()
            
            # Prune expired message IDs from deduplication cache
            self.message_cache.prune_expired()
            
            # Check for stale connections and send heartbeats
            stale_links: list[ConnectionLink] = []
            for link in list(self.all_server_connection_links):
                server_id = getattr(link, "server_id", None)
                if not server_id or server_id == self.local_server.id:
                    continue
                last_seen = getattr(link, "last_seen", now)
                if now - last_seen >= self.heartbeat_timeout:
                    stale_links.append(link)
                    continue
                heartbeat = create_envelope(
                    MessageType.HEARTBEAT.value,
                    self.local_server.id,
                    server_id,
                    {},
                )
                await link.send_message(heartbeat)
            for link in stale_links:
                logger.warning(
                    "Closing stale server link %s after %.2fs of inactivity",
                    getattr(link, "server_id", None),
                    now - getattr(link, "last_seen", now),
                )
                await self.cleanup_connection(
                    link,
                    close_code=1011,
                    close_reason="health timeout",
                )

    async def connect_to_known_servers(self) -> None:
        """Attempt outbound connections to all entries in server_addrs that aren't connected."""
        for server_id, server in list(self.servers.items()):
            h = server.endpoint.host
            p = server.endpoint.port
            if server_id == self.local_server.id:
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
                link = ConnectionLink(ws, self.signer, self.local_server, connection_type="server")
                link.server_id = server_id
                link.identified = True
              
                link.last_seen = time.monotonic()

                # Identify ourselves with SERVER_HELLO_LINK (to = remote server_id)
                link_env = create_envelope(
                    MessageType.SERVER_HELLO_LINK.value,
                    self.local_server.id,
                    server_id,
                    {"host": self.local_server.endpoint.host, "port": self.local_server.endpoint.port, "pubkey": self.local_server.pubkey},
                )
                await link.send_message(link_env)
                # Optional: await welcome
                with suppress(asyncio.TimeoutError):
                    initial = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    link.last_seen = time.monotonic()
                    if isinstance(initial, bytes):
                        initial = initial.decode("utf-8")
                    await self.process_message(link, initial)
                # Register link
                link.connection_type = "server"
                self.add_update_server(server_id, ServerEndpoint(host=h, port=p), link, server.pubkey)
                reader = asyncio.create_task(self._run_server_link(link))
                self._track_background_task(reader)
                logger.info(f"Connected and linked to server {server_id} at {h}:{p}")
            except Exception as e:
                logger.debug(f"Connect to {server_id}@{h}:{p} failed: {e}")
                if link is not None:
                    with suppress(Exception):
                        await link.close()

    async def _run_server_link(self, link: ConnectionLink) -> None:
        """Continuously read frames from an outbound server link."""
        try:
            while True:
                message = await link.websocket.recv()
                link.last_seen = time.monotonic()

                if isinstance(message, bytes):
                    message = message.decode("utf-8")
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
            if isinstance(first_message, bytes):
                first_message = first_message.decode("utf-8")
            connection.last_seen = time.monotonic()

            # Parse the message
            envelope = Envelope.from_json(first_message)
            logger.info(f"First message type: {envelope.type} from {envelope.from_}")
            
            # Identify connection type based on first message
            if envelope.type == MessageType.USER_HELLO.value:
                await self.handle_user_hello(connection, envelope)
            elif envelope.type == MessageType.SERVER_HELLO_JOIN.value:
                await self.handle_server_hello_join(connection, envelope)
            elif envelope.type == MessageType.SERVER_HELLO_LINK.value:
                await self.handle_server_hello_link(connection, envelope)
            else:
                raise ValueError(f"Invalid first message type: {envelope.type}")
                
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for first message")
            raise
        except ValueError as e:
            error_msg = str(e)
            logger.error(f"First message validation error: {error_msg}")
            
            # Route to appropriate SOCP error code per §9.5
            if "requires signature" in error_msg or "sig" in error_msg.lower():
                await connection.on_error_bad_key(error_msg)
            else:
                await connection.on_error_bad_key(error_msg)
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
            await connection.on_error_bad_key("from must be UUIDv4", to_id=envelope.from_)
            return
        
        # Validate user_id is not already in use locally
        if user_id in self.connected_users:
            logger.warning(f"User ID {user_id} already in use")
            await connection.on_error_name_in_use(f"User {user_id} already connected", to_id=user_id)
            return
        
        # Validate payload structure
        required_fields = {"client", "pubkey", "enc_pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await connection.on_error_bad_key(f"Missing required fields: {missing}", to_id=user_id)
            return
        
        # Register the user
        connection.connection_type = "user"
        connection.user_id = user_id
        connection.identified = True
        
        # Maintain unified user table for presence tracking
        self.add_update_user(user_id, connection, "local")
        
        logger.info(f"User {user_id} connected with client {envelope.payload['client']}")
        
        # Add user to public channel per SOCP §9.3
        user_pubkey = envelope.payload.get("enc_pubkey", envelope.payload.get("pubkey"))
        logger.debug(f"User {user_id} connected with client {envelope.payload['client']} and pubkey {user_pubkey[:10] if user_pubkey else 'None'}...")
        if user_pubkey:
            try:
                self.public_channel.add_member(user_id, user_pubkey)
                
                # Broadcast PUBLIC_CHANNEL_ADD to network per SOCP §9.3
                # Per spec: "to":"*" broadcast only, no direct messages
                await self.broadcast_public_channel_add([user_id])
                
                # Broadcast PUBLIC_CHANNEL_UPDATED with all wraps per SOCP §9.3
                # Per spec: "to":"*" broadcast only, users extract their own wrap
                await self.broadcast_public_channel_updated()
                
            except Exception as e:
                logger.error(f"Failed to add user {user_id} to public channel: {e}")
        
        # Send existing users to new client (catch-up per SOCP §8.2)
        catchup_count = 0
        for existing_user_id, user_record in self.users.items():
            if existing_user_id != user_id:  # Don't send self
                # Get existing user's pubkey from public channel
                existing_pubkey = ""
                if hasattr(self, 'public_channel'):
                    member = self.public_channel.get_member(existing_user_id)
                    if member:
                        existing_pubkey = member.user_pubkey
                
                # Send USER_ADVERTISE for each existing user directly to new client
                catchup_env = create_envelope(
                    MessageType.USER_ADVERTISE.value,
                    self.local_server.id,
                    user_id,  # To the new user only
                    {
                        "user_id": existing_user_id,
                        "server_id": user_record.location,
                        "meta": {"pubkey": existing_pubkey} if existing_pubkey else {}
                    },
                )
                logger.info(f"Sending USER_ADVERTISE to {user_id[:8]} for existing user {existing_user_id[:8]}, has_pubkey={bool(existing_pubkey)}, pubkey_len={len(existing_pubkey) if existing_pubkey else 0}")
                await connection.send_message(catchup_env)
                catchup_count += 1
        
        if catchup_count > 0:
            logger.info(f"Sent {catchup_count} existing users to new user {user_id[:8]}")
        
        # Broadcast USER_ADVERTISE for new user to all servers and clients
        # Include pubkey in meta per SOCP §8.2
        meta_with_pubkey = envelope.payload.get("meta", {}).copy()
        meta_with_pubkey["pubkey"] = user_pubkey if user_pubkey else ""
        logger.info(f"Broadcasting USER_ADVERTISE for {user_id[:8]}, has_pubkey={bool(user_pubkey)}, pubkey_len={len(user_pubkey) if user_pubkey else 0}")
        await self.broadcast_user_advertise(user_id, meta_with_pubkey)
    
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
        if requested_server_id == self.local_server.id:
            logger.warning("Ignoring SERVER_HELLO_JOIN from self")
            return

        # Validate payload structure
        required_fields = {"host", "port", "pubkey"}
        if not all(field in envelope.payload for field in required_fields):
            missing = required_fields - set(envelope.payload.keys())
            await connection.on_error_bad_key(f"Missing required fields: {missing}", to_id=envelope.from_)
            return

        # Assign server ID ensuring uniqueness as per spec
        assigned_server_id = requested_server_id if is_uuid_v4(requested_server_id) else str(uuid.uuid4())
        if assigned_server_id in self.servers or assigned_server_id == self.local_server.id:
            logger.info(f"Reassigning duplicate server_id {requested_server_id}")
            assigned_server_id = str(uuid.uuid4())
            while assigned_server_id in self.servers or assigned_server_id == self.local_server.id:
                assigned_server_id = str(uuid.uuid4())

        # Register the server
        connection.connection_type = "server"
        connection.server_id = assigned_server_id
        connection.identified = True
        self.add_update_server(assigned_server_id, ServerEndpoint(host=envelope.payload["host"], port=envelope.payload["port"]), connection, envelope.payload.get("pubkey", ""))

        logger.info(f"Server {assigned_server_id} joined from {envelope.payload['host']}:{envelope.payload['port']}")

        # Send SERVER_WELCOME response (for now, simple acknowledgment)
        welcome_payload = {
            "assigned_id": assigned_server_id,  # Keep same ID if unique
            "servers": [
                {
                    "server_id": sid,
                    "host": server.endpoint.host,
                    "port": server.endpoint.port,
                    "pubkey": self.servers[sid].pubkey,
                }
                for sid, server in self.servers.items()
                if sid != assigned_server_id
            ],
            
        }
        welcome_envelope = create_envelope(
            MessageType.SERVER_WELCOME.value,
            self.local_server.id,
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
            await connection.on_error_bad_key(f"Missing required fields: {missing}", to_id=envelope.from_)
            return
        # Register/replace link
        connection.connection_type = "server"
        connection.server_id = server_id
        connection.identified = True
        
        self.add_update_server(server_id, ServerEndpoint(host=envelope.payload["host"], port=envelope.payload["port"]), connection, envelope.payload.get("pubkey", ""))
        #self.servers[server_id] = connection
        #self.server_addrs[server_id] = (envelope.payload["host"], envelope.payload["port"])
        #self.server_pubkeys[server_id] = envelope.payload.get("pubkey", "")
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
        except InvalidSigError as e:
            logger.error(f"Invalid signature: {e}")
            await connection.on_error_invalid_sig(str(e))
        except BadKeyError as e:
            logger.error(f"Bad key: {e}")
            await connection.on_error_bad_key(str(e))
        except TimeoutError as e:
            logger.error(f"Timeout: {e}")
            await connection.on_error_timeout(str(e))
        except UnknownTypeError as e:
            logger.error(f"Unknown type: {e}")
            await connection.on_error_unknown_type(str(e))
        except NameInUseError as e:
            logger.error(f"Name in use: {e}")
            await connection.on_error_name_in_use(str(e))
        except UserNotFoundError as e:
            logger.error(f"User not found: {e}")
            await connection.on_error_user_not_found(str(e))
        except ValueError as e:
            
            error_msg = str(e)
            logger.error(f"Value error: {error_msg}")
            
            await connection.on_error_unknown_type(str(e))

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Only use UNKNOWN_TYPE for truly unknown issues
            await connection.on_error_unknown_type(str(e))
    
    async def handle_user_message(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """
        Handle messages from user connections using registry dispatch.
        
        Routes the message to the appropriate handler based on message type.
        """
        # Validate and parse message type
        if not MessageType.is_valid(envelope.type):
            logger.warning("Unknown user message type: %s", envelope.type)
            await connection.on_error_unknown_type(f"Unknown message type: {envelope.type}")
            return
        
        msg_type = MessageType(envelope.type)
        
        # Look up handler in registry
        handler = USER_HANDLER_REGISTRY.get(msg_type)
        if handler is None:
            logger.warning("Unimplemented user message type: %s", msg_type.value)
            await connection.on_error_unknown_type(f"Message type {msg_type.value} not yet implemented")
            return
        
        # Dispatch to handler
        try:
            await handler(self, connection, envelope)
        except Exception as exc:
            logger.error("Error handling user message %s: %s", msg_type.value, exc, exc_info=True)
            await connection.on_error_bad_key(f"Failed to process {msg_type.value}")
    
    async def handle_server_message(self, connection: ConnectionLink, envelope: Envelope) -> None:
        """
        Handle messages from server connections using registry dispatch.
        
        Routes the message to the appropriate handler based on message type.
        """
        origin_server_id = envelope.from_
        logger.info(
            "Server message %s from %s via link %s",
            envelope.type,
            origin_server_id,
            connection.server_id,
        )
        
        # Validate and parse message type
        if not MessageType.is_valid(envelope.type):
            logger.warning("Unknown server message type: %s", envelope.type)
            await connection.on_error_unknown_type(f"Unknown message type: {envelope.type}", to_id=origin_server_id)
            return
        
        msg_type = MessageType(envelope.type)
        
        # Look up handler in registry
        handler = SERVER_HANDLER_REGISTRY.get(msg_type)
        if handler is None:
            logger.warning("Unimplemented server message type: %s", msg_type.value)
            await connection.on_error_unknown_type(
                f"Message type {msg_type.value} not yet implemented", to_id=origin_server_id
            )
            return
        
        # Dispatch to handler
        try:
            await handler(self, connection, envelope)
        except Exception as exc:
            logger.error("Error handling server message %s: %s", msg_type.value, exc, exc_info=True)
            await connection.on_error_bad_key(f"Failed to process {msg_type.value}", to_id=origin_server_id)
    
    async def _relay_broadcast_to_network(self, envelope: Envelope, exclude_server: Optional[str] = None) -> None:
        """
        Helper to relay a broadcast message to all servers and local clients.
        
        Args:
            envelope: The envelope to broadcast
            exclude_server: Optional server_id to exclude from forwarding (for gossip loop prevention)
        """
        # Send to all connected servers (with optional exclusion)
        for server_link in self.all_server_connection_links:
            if exclude_server and server_link.server_id == exclude_server:
                continue
            await server_link.send_message(envelope)
        
        # Relay to all local clients per SOCP §8.2: "which relays to all clients"
        for user_record in self.connected_local_users.values():
            if user_record.link is not None:
                await user_record.send_message(envelope)
        
        logger.debug(
            f"Relayed {envelope.type} to network (excluded server: {exclude_server})"
        )
    
    async def broadcast_user_advertise(self, user_id: str, meta: Dict[str, Any]) -> None:
        """Broadcast USER_ADVERTISE to all connected servers"""
        payload = {
            "user_id": user_id,
            "server_id": self.local_server.id,
            "meta": meta
        }
        envelope = create_envelope(
            MessageType.USER_ADVERTISE.value,
            self.local_server.id,
            "*",
            payload,
        )
        await self._relay_broadcast_to_network(envelope)
        logger.info(f"Broadcasted USER_ADVERTISE for {user_id}")
    
    async def broadcast_public_channel_add(self, user_ids: list[str]) -> None:
        """Broadcast PUBLIC_CHANNEL_ADD to all connected servers per SOCP §9.3"""
        if not user_ids:
            return
        
        payload = {
            "add": user_ids,
            "if_version": self.public_channel.channel.version - 1,  # Version before addition
        }
        envelope = create_envelope(
            MessageType.PUBLIC_CHANNEL_ADD.value,
            self.local_server.id,
            "*",
            payload,
        )
        await self._relay_broadcast_to_network(envelope)
        logger.info(f"Broadcasted PUBLIC_CHANNEL_ADD for {len(user_ids)} users")
    
    async def broadcast_public_channel_updated(self) -> None:
        """Broadcast PUBLIC_CHANNEL_UPDATED with current version and member list per SOCP §9.3"""
        members = self.public_channel.channel.get_member_info_list()
        
        payload = {
            "version": self.public_channel.channel.version,
            "members": members,
        }
        envelope = create_envelope(
            MessageType.PUBLIC_CHANNEL_UPDATED.value,
            self.local_server.id,
            "*",
            payload,
        )
        await self._relay_broadcast_to_network(envelope)
        logger.info(
            f"Broadcasted PUBLIC_CHANNEL_UPDATED version={self.public_channel.channel.version} with {len(members)} members"
        )
    
    async def broadcast_server_announce(self, server_id: str, server_info: Dict[str, Any]) -> None:
        """Broadcast SERVER_ANNOUNCE to all other servers"""
        payload = {
            "host": server_info["host"],
            "port": server_info["port"],
            "pubkey": server_info["pubkey"]
        }
        # TODO: Check for compliance with SOCP spec may not be needed as upstream already handle this
        #self.server_addrs[server_id] = (server_info["host"], server_info["port"])
        #self.server_pubkeys[server_id] = server_info.get("pubkey", "")

        envelope = create_envelope(MessageType.SERVER_ANNOUNCE.value, server_id, "*", payload)

        # Send to all other connected servers
        for other_server_id, value in self.servers.items():
            if other_server_id == server_id:
                continue
            # Resolve link
            link = value if isinstance(value, ConnectionLink) else getattr(value, "link", None)
            if link is not None:
                await link.send_message(envelope)

        logger.info(f"Broadcasted SERVER_ANNOUNCE for {server_id}")


    def get_status(self) -> Dict[str, Any]:
        """Expose internal status for health/diagnostics."""
        server_links: Dict[str, Dict[str, Any]] = {}
        for server_id, record in self.servers.items():
            link: Optional[ConnectionLink]
            if isinstance(record, ConnectionLink):
                link = record
            else:
                link = getattr(record, "link", None)
            server_links[server_id] = {
                "connected": link is not None,
                "last_seen": getattr(link, "last_seen", None) if link else None,
            }

        return {
            "server_id": self.local_server.id,
            "local_users": list(self.users.keys()),
            "known_servers": {sid: (rec.endpoint.host, rec.endpoint.port) for sid, rec in self.servers.items()},
            "server_links": server_links,
            "heartbeat_interval": self.heartbeat_interval,
            "heartbeat_timeout": self.heartbeat_timeout,
            "message_cache": self.message_cache.stats(),
            "public_channel": self.public_channel.get_channel_state(),
        }



    
    async def cleanup_connection(
        self,
        connection: ConnectionLink,
        *,
        close_code: int = 1000,
        close_reason: Optional[str] = None,
    ) -> None:
        """Clean up when connection closes"""
        if connection.user_id:
            await self.cleanup_user_connection(connection.user_id)
        elif connection.server_id:
            await self.cleanup_server_connection(connection.server_id)

        await connection.close(code=close_code, reason=close_reason)
    
    async def cleanup_user_connection(self, user_id: str) -> None:
        """Clean up user connection and broadcast USER_REMOVE"""
        if user_id in self.users:
            del self.users[user_id]
        
        # Remove from public channel
        self.public_channel.remove_member(user_id)
       
        # Broadcast USER_REMOVE
        payload = {"user_id": user_id, "server_id": self.local_server.id}
        envelope = create_envelope(
            MessageType.USER_REMOVE.value,
            self.local_server.id,
            "*",
            payload,
        )
        await self._relay_broadcast_to_network(envelope)
        logger.info(f"Cleaned up user {user_id}")
    
    async def cleanup_server_connection(self, server_id: str) -> None:
        """Clean up server connection and schedule reconnection"""
        # Remove any user_locations that point to this server as host
        stale_users = [user_id for user_id, record in self.users.items() if record.location == server_id]
        for u in stale_users:
            del self.users[u]
        
        # Clear the link but keep server record (endpoint + pubkey) for reconnection
        if server_id in self.servers:
            server_record = self.servers[server_id]
            # Update record to clear the link but preserve endpoint and pubkey
            self.add_update_server(server_id, server_record.endpoint, None, server_record.pubkey)
            
            # Schedule reconnection with exponential backoff (SOCP §11)
            # Only schedule if we have server info to reconnect to
            reconnect_task = asyncio.create_task(self._reconnect_to_server(server_id))
            self._track_background_task(reconnect_task)
            logger.info(f"Cleaned up server {server_id}, reconnection scheduled")
        else:
            logger.info(f"Cleaned up server {server_id}, no reconnection scheduled (not in registry)")
    
    async def _reconnect_to_server(self, server_id: str, max_attempts: int = 10) -> None:
        """
        Attempt to reconnect to a disconnected server with exponential backoff.

        Per SOCP §11: "try reconnecting (using server_addrs)"

        Args:
            server_id: The server to reconnect to
            max_attempts: Maximum number of reconnection attempts (default: 10)
        """
        # Prevent concurrent reconnections to the same server
        if server_id in self._reconnecting_servers:
            logger.debug(f"Already reconnecting to server {server_id}, skipping duplicate attempt")
            return

        self._reconnecting_servers.add(server_id)
        try:
            attempt = self._reconnection_attempts.get(server_id, 0)

            while attempt < max_attempts:
                # Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, max 60s
                delay = min(2 ** attempt, 60)
                logger.info(f"Reconnecting to server {server_id} in {delay}s (attempt {attempt + 1}/{max_attempts})")

                await asyncio.sleep(delay)

                # Check if server still exists and is disconnected
                server_record = self.servers.get(server_id)
                if not server_record:
                    logger.debug(f"Server {server_id} no longer in registry, stopping reconnection")
                    self._reconnection_attempts.pop(server_id, None)
                    return

                if server_record.link is not None:
                    logger.info(f"Server {server_id} already reconnected, stopping reconnection attempts")
                    self._reconnection_attempts.pop(server_id, None)
                    return

                # Attempt connection
                try:
                    h = server_record.endpoint.host
                    p = server_record.endpoint.port
                    url = f"ws://{h}:{p}"

                    logger.info(f"Attempting to reconnect to server {server_id} at {h}:{p}")
                    ws = await websockets.connect(url)
                    link = ConnectionLink(ws, self.signer, self.local_server, connection_type="server")
                    link.server_id = server_id
                    link.identified = True
                    link.last_seen = time.monotonic()

                    # Identify ourselves with SERVER_HELLO_LINK
                    link_env = create_envelope(
                        MessageType.SERVER_HELLO_LINK.value,
                        self.local_server.id,
                        server_id,
                        {
                            "host": self.local_server.endpoint.host,
                            "port": self.local_server.endpoint.port,
                            "pubkey": self.local_server.pubkey
                        },
                    )
                    await link.send_message(link_env)

                    # Optional: await welcome response
                    with suppress(asyncio.TimeoutError):
                        initial = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        link.last_seen = time.monotonic()
                        if isinstance(initial, bytes):
                            initial = initial.decode("utf-8")
                        await self.process_message(link, initial)

                    # Register link and start reader task
                    self.add_update_server(server_id, server_record.endpoint, link, server_record.pubkey)
                    reader = asyncio.create_task(self._run_server_link(link))
                    self._track_background_task(reader)

                    logger.info(f"Successfully reconnected to server {server_id} at {h}:{p}")

                    # Reset reconnection attempts on success
                    self._reconnection_attempts.pop(server_id, None)
                    return

                except Exception as e:
                    attempt += 1
                    self._reconnection_attempts[server_id] = attempt
                    logger.warning(f"Reconnection attempt {attempt} to {server_id} failed: {e}")

            logger.error(f"Failed to reconnect to server {server_id} after {max_attempts} attempts")
            self._reconnection_attempts.pop(server_id, None)
        finally:
            # Always remove from reconnecting set when done (success or failure)
            self._reconnecting_servers.discard(server_id)


async def main():
    """Main entry point"""
    configure_root_logging()
    server = SOCPServer(storage_path=Path("~/.server").expanduser(), host="localhost", port=8765)
    await server.start_server()
if __name__ == "__main__":
    asyncio.run(main())
