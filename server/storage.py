"""
Persistent JSON-based storage for SOCP Server.

Handles atomic saving/loading of:
- Server records (known servers in the network)
- User records (connected and known users)
- Public channel state (members, metadata, version)
- Server identity and keys

All writes are atomic to prevent corruption.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

from shared.log import get_logger

if TYPE_CHECKING:
    from server.core.MemoryTable import ServerRecord, UserRecord
    from server.core.PublicChannelManager import PublicChannelState

logger = get_logger(__name__)


class SOCPStorage:
    """
    JSON-based persistent storage for SOCP server state.
    
    Storage structure:
    <storage_path>/
        server_id.json          - Server identity (UUID)
        server_keys/            - RSA keypair
        servers.json            - Known servers in network
        users.json              - Known users (local + remote)
        public_channel.json     - Public channel state
    """
    
    def __init__(self, storage_path: Path):
        """
        Initialize storage manager.
        
        Args:
            storage_path: Base directory for all persistent data
        """
        self.storage_path = Path(storage_path).expanduser()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # File paths
        self.server_id_file = self.storage_path / "server_id.json"
        self.servers_file = self.storage_path / "servers.json"
        self.users_file = self.storage_path / "users.json"
        self.public_channel_file = self.storage_path / "public_channel.json"
        
        logger.info(f"Initialized SOCP storage at {self.storage_path}")
    
    def _atomic_write(self, file_path: Path, data: Dict[str, Any]) -> None:
        """
        Atomically write JSON data to file.
        
        Uses temp file + rename for atomicity to prevent corruption.
        
        Args:
            file_path: Target file path
            data: Dictionary to serialize as JSON
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temp file first
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{file_path.stem}_",
            suffix=".json.tmp",
            dir=file_path.parent
        )
        
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(data, tmp, indent=2, sort_keys=True)
                tmp.flush()
                os.fsync(tmp.fileno())
            
            # Atomic rename
            os.replace(tmp_path, file_path)
            logger.debug(f"Saved {file_path.name}")
            
        except Exception as e:
            logger.error(f"Failed to write {file_path}: {e}")
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise
    
    def _atomic_read(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Read JSON data from file.
        
        Args:
            file_path: File to read
            
        Returns:
            Parsed JSON dict, or None if file doesn't exist or is invalid
        """
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.debug(f"Loaded {file_path.name}")
            return data
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
            return None
    
    # ========== Server Records ==========
    
    def save_servers(self, servers: Dict[str, "ServerRecord"]) -> None:
        """
        Save known servers to disk.
        
        Args:
            servers: Dictionary of server_id -> ServerRecord
        """
        from server.core.MemoryTable import ServerRecord
        
        data = {
            "servers": [
                {
                    "server_id": server.id,
                    "host": server.endpoint.host,
                    "port": server.endpoint.port,
                    "pubkey": server.pubkey,
                }
                for server in servers.values()
            ]
        }
        
        self._atomic_write(self.servers_file, data)
        logger.info(f"Saved {len(servers)} servers to disk")
    
    def load_servers(self) -> Dict[str, Dict[str, Any]]:
        """
        Load known servers from disk.
        
        Returns:
            Dictionary of server_id -> {host, port, pubkey}
            Empty dict if file doesn't exist
        """
        data = self._atomic_read(self.servers_file)
        if not data or "servers" not in data:
            return {}
        
        servers = {}
        for server_data in data["servers"]:
            if "server_id" in server_data:
                servers[server_data["server_id"]] = {
                    "host": server_data.get("host", ""),
                    "port": server_data.get("port", 0),
                    "pubkey": server_data.get("pubkey", ""),
                }
        
        logger.info(f"Loaded {len(servers)} servers from disk")
        return servers
    
    # ========== User Records ==========
    
    def save_users(self, users: Dict[str, "UserRecord"]) -> None:
        """
        Save known users to disk.
        
        Note: Only saves user_id and location (server_id).
        Link (connection) is transient and not persisted.
        
        Args:
            users: Dictionary of user_id -> UserRecord
        """
        data = {
            "users": [
                {
                    "user_id": user.id,
                    "location": user.location,
                }
                for user in users.values()
            ]
        }
        
        self._atomic_write(self.users_file, data)
        logger.info(f"Saved {len(users)} users to disk")
    
    def load_users(self) -> Dict[str, Dict[str, Any]]:
        """
        Load known users from disk.
        
        Returns:
            Dictionary of user_id -> {location}
            Empty dict if file doesn't exist
        """
        data = self._atomic_read(self.users_file)
        if not data or "users" not in data:
            return {}
        
        users = {}
        for user_data in data["users"]:
            if "user_id" in user_data:
                users[user_data["user_id"]] = {
                    "location": user_data.get("location", ""),
                }
        
        logger.info(f"Loaded {len(users)} users from disk")
        return users
    
    # ========== Public Channel State ==========
    
    def save_public_channel(self, channel_state: "PublicChannelState") -> None:
        """
        Save public channel state to disk.
        
        Args:
            channel_state: PublicChannelState object from PublicChannelManager
        """
        data = {
            "group_id": channel_state.group_id,
            "creator_id": channel_state.creator_id,
            "version": channel_state.version,
            "created_at": channel_state.created_at,
            "meta": channel_state.meta,
            "members": [
                {
                    "user_id": member.user_id,
                    "user_pubkey": member.user_pubkey,
                    "role": member.role,
                    "added_at": member.added_at,
                }
                for member in channel_state.members.values()
            ]
        }
        
        self._atomic_write(self.public_channel_file, data)
        logger.info(
            f"Saved public channel (version={channel_state.version}, "
            f"{len(channel_state.members)} members)"
        )
    
    def load_public_channel(self) -> Optional[Dict[str, Any]]:
        """
        Load public channel state from disk.
        
        Returns:
            Dictionary with channel state, or None if file doesn't exist
        """
        data = self._atomic_read(self.public_channel_file)
        if not data:
            return None
        
        logger.info(
            f"Loaded public channel (version={data.get('version', 0)}, "
            f"{len(data.get('members', []))} members)"
        )
        return data
    
    # ========== Periodic Save ==========
    
    def save_all(
        self,
        servers: Dict[str, "ServerRecord"],
        users: Dict[str, "UserRecord"],
        public_channel_state: "PublicChannelState"
    ) -> None:
        """
        Save all state to disk (convenience method).
        
        Args:
            servers: Server records
            users: User records
            public_channel_state: Public channel state
        """
        try:
            self.save_servers(servers)
            self.save_users(users)
            self.save_public_channel(public_channel_state)
            logger.info("Saved all server state to disk")
        except Exception as e:
            logger.error(f"Failed to save server state: {e}")
    
    # ========== Cleanup ==========
    
    def clear_transient_data(self) -> None:
        """
        Clear transient data (users, connections).
        
        Keeps: server_id, known servers (with pubkeys)
        Clears: users, public channel members
        
        Useful for clean server restarts.
        """
        if self.users_file.exists():
            self.users_file.unlink()
            logger.info("Cleared user data")
        
        if self.public_channel_file.exists():
            self.public_channel_file.unlink()
            logger.info("Cleared public channel data")
    
    def get_storage_stats(self) -> Dict[str, Any]:
        """
        Get storage statistics for monitoring.
        
        Returns:
            Dictionary with file sizes and counts
        """
        stats = {
            "storage_path": str(self.storage_path),
            "files": {}
        }
        
        for file_path in [
            self.server_id_file,
            self.servers_file,
            self.users_file,
            self.public_channel_file
        ]:
            if file_path.exists():
                stats["files"][file_path.name] = {
                    "exists": True,
                    "size_bytes": file_path.stat().st_size
                }
            else:
                stats["files"][file_path.name] = {"exists": False}
        
        return stats
