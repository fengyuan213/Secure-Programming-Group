from __future__ import annotations

from shared.envelope import Envelope
from shared.utils import is_uuid_v4
'''
All type annotations in that file are stored as strings internally.

Python does not evaluate them immediately when the class/function is defined.

Type checkers (mypy, Pyright, PyCharm, etc.) and runtime helpers (typing.get_type_hints) will resolve them later, so they still behave like normal types.
'''
from dataclasses import dataclass
from typing import Optional, Dict, Literal
from server.core.ConnectionLink import ConnectionLink

# Required in-memory tables (Section 5.2)
Location = Literal["local", str]  # "local" or a server_id like "server_123"
        
@dataclass(frozen=True)
class ServerEndpoint:
   
    host: str
    port: int

@dataclass
class ServerRecord: 
    id: str
    link: ConnectionLink | None
    endpoint: ServerEndpoint
    pubkey: str
    def is_connected(self) -> bool:
        return self.link is not None
    async def send_message(self, envelope: Envelope) -> None:
        if self.link is None:
            raise ValueError("Server is not connected")
        await self.link.send_message(envelope)


@dataclass
class UserRecord:
    id: str
    link: Optional[ConnectionLink]   # None if remote
    location: Location                 # "local" or server_id  # pyright: ignore[reportInvalidTypeForm]
 
    def is_local(self) -> bool:
        return self.location == "local"
    def is_remote(self) -> bool:
        return self.location != "local" and is_uuid_v4(self.location)
    def is_connected(self) -> bool:
        """Check if user is connected (either locally or on a remote server)."""
        # Local users: must have a link
        if self.location == "local":
            return self.link is not None
        # Remote users: must have a valid server location
        return is_uuid_v4(self.location)   
    async def send_message(self, envelope: Envelope) -> None:
        if self.link is None:
            raise ValueError("User is not connected")
        await self.link.send_message(envelope)
    # Helper for convenience (doesn't store object refs)
    def resolve_server(self, servers: Dict[str, ServerRecord]) -> Optional[ServerRecord]:
        if self.location == "local":
            return None
        return servers.get(self.location)