
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple

from .utils import is_uuid_v4, is_base64url, is_ipv4_hostport

# message types where 'sig' MAY be omitted (first-contact flows)
_ALLOW_UNSIGNED_TYPES: Set[str] = {
    "USER_HELLO",
    "SERVER_HELLO_JOIN",
    "SERVER_WELCOME",
    "SERVER_ANNOUNCE",
}
