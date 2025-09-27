from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Presence:
    users: Dict[str, Dict] = field(default_factory=dict)

    def add(self, user_id: str, meta: Dict) -> None:
        self.users[user_id] = {"meta": meta}

    def remove(self, user_id: str) -> None:
        self.users.pop(user_id, None)

    def list_sorted(self) -> List[str]:
        return sorted(self.users.keys())


@dataclass
class InboundFile:
    file_id: str
    name: str
    size: int
    sha256: str
    chunks: Dict[int, bytes] = field(default_factory=dict)

    def write(self, index: int, data: bytes) -> None:
        self.chunks[index] = data


