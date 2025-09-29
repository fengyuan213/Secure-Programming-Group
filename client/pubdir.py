from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Optional


class PubKeyDirectory:
    def __init__(self, base: Optional[Path] = None) -> None:
        self.base = base or (Path.home() / ".socp" / "pubdir.json")
        self._data: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.base.exists():
            try:
                self._data = json.loads(self.base.read_text())
            except Exception:
                self._data = {}

    def save(self) -> None:
        self.base.parent.mkdir(parents=True, exist_ok=True)
        self.base.write_text(json.dumps(self._data, indent=2))

    def set(self, user_id: str, pubkey_b64url: str) -> None:
        self._data[user_id] = pubkey_b64url
        self.save()

    def get(self, user_id: str) -> Optional[str]:
        return self._data.get(user_id)

    def all(self) -> Dict[str, str]:
        return dict(self._data)

 