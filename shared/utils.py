from __future__ import annotations
import re
import uuid
from typing import Optional

# ========================================
#           INPUT VALIDATION HELPERS
# ========================================
"""
This section contains helper functions
that the envelope checker will call to decide if an incoming JSON message
is properly formatted and safe to process.
"""

# Spec for Encodings: Binary values (keys, ciphertexts, IVs, tags, signatures) MUST be base64url (no ppadding) in JSON.
_B64URL_RE = re.compile(r'^[A-Za-z0-9_-]+$')  # no '=' padding allowed

def is_uuid_v4(s: str) -> bool:
    """
    enforces that IDs like user_id or server_id are valid UUIDv4s in canonical string form
    """
    try:
        u = uuid.UUID(s)
        return u.version == 4 and str(u) == s.lower()
    except Exception:
        return False

def is_base64url(s: str) -> bool: 
    """
    returns True if the string is only base64url-safe characters, otherwise False.
    """
    return bool(_B64URL_RE.fullmatch(s))

def is_ipv4_hostport(s: str) -> bool:
    """
    Accepts 'A.B.C.D:port'.
    
    - Split the string into host and port.
    - host must be 4 dot-separated numbers (like 192.168.1.5).
    - Each number must be between 0 and 255.
    - Port must be an integer between 1 and 65535.

    - If everything checks out → returns True.
    """
    try:
        host, port_s = s.split(':', 1)
        parts = host.split('.')
        if len(parts) != 4:
            return False
        if not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return False
        port = int(port_s)
        return 0 < port <= 65535
    except Exception:
        return False

def is_hostport(s: str) -> bool:
    """
    Accepts 'hostname:port' or 'A.B.C.D:port'.
    
    For SERVER_HELLO_JOIN bootstrap where remote server ID is unknown.
    Validates that:
    - String contains exactly one colon
    - Port is a valid integer between 1 and 65535
    - Hostname is non-empty
    
    Examples: "localhost:8765", "192.168.1.5:8080", "example.com:443"
    """
    try:
        if ':' not in s:
            return False
        host, port_s = s.rsplit(':', 1)  # rsplit to handle IPv6 future-proofing
        if not host:  # Empty hostname
            return False
        port = int(port_s)
        return 0 < port <= 65535
    except Exception:
        return False


# ========================================
#           SECTION NAME 
# ========================================