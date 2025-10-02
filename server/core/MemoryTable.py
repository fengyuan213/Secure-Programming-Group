from __future__ import annotations
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
    link: Optional[ConnectionLink]
    endpoint: ServerEndpoint
    pubkey: str

@dataclass
class UserRecord:
    id: str
    link: Optional[ConnectionLink]   # None if remote
    location: Location                 # "local" or server_id  # pyright: ignore[reportInvalidTypeForm]

    # Helper for convenience (doesn't store object refs)
    def resolve_server(self, servers: Dict[str, ServerRecord]) -> Optional[ServerRecord]:
        if self.location == "local":
            return None
        return servers.get(self.location)