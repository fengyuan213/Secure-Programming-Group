
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple
import json
import time

from shared.utils import is_uuid_v4, is_base64url, is_ipv4_hostport, is_hostport

# message types where 'sig' MAY be omitted (first-contact flows) when update this, update connectionlink.py as well
_ALLOW_UNSIGNED_TYPES: Set[str] = {
    "USER_HELLO",
    "SERVER_HELLO_JOIN",
    "SERVER_HELLO_LINK",
    "SERVER_WELCOME",
    "SERVER_ANNOUNCE",
}
class InvalidSigError(Exception):
    """Raised when a specific condition in my app fails."""
    pass
class BadKeyError(Exception):
    """Raised when a specific condition in my app fails."""
    pass
class TimeoutError(Exception):
    """Raised when a specific condition in my app fails."""
    pass
class UnknownTypeError(Exception):
    """Raised when a specific condition in my app fails."""
    pass
class NameInUseError(Exception):
    """Raised when a specific condition in my app fails."""
    pass
class UserNotFoundError(Exception):
    """Raised when a specific condition in my app fails."""
    pass

@dataclass
class Envelope:
    """
    Validate that every inbound frame uses the envelope:
    {
    "type": "STRING",
    "from": "UUID",
    "to":   "UUID | \"*\" | host:port (bootstrap only)",
    "ts":   "INT (unix ms)",
    "payload": { ... },
    "sig": "BASE64URL (optional only for HELLO/BOOTSTRAP)"
    }



    Special to cases:
    - "*" allowed for broadcasts (server gossip, public channel fan-out).
    - host:port allowed only during SERVER_HELLO_JOIN (bootstrap)
    """
    type: str           # Payload type, case-sensitive
    from_: str          # "server_id" or "user_id" (renamed to avoid keyword collision)
    to: str             # "server_id", "user_id", or "*"
    ts: int             # Unix timestamp in milliseconds
    payload: Dict[str, Any]  # JSON object, payload-specific
    sig: Optional[str] = None  # BASE64URL signature (optional for HELLO/BOOTSTRAP)

    @classmethod
    def from_json(cls, json_str: str) -> 'Envelope':
        """Parse JSON string into Envelope, validating structure"""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise BadKeyError(f"Invalid JSON: {e}")
        
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Envelope':
        """Create Envelope from dictionary, validating required fields"""
        # Check required fields
        required_fields = {'type', 'from', 'to', 'ts', 'payload'}
        missing = required_fields - set(data.keys())
        if missing:
            raise BadKeyError(f"Missing required fields: {missing}")
        
        # Validate types
        if not isinstance(data['type'], str):
            raise BadKeyError("'type' must be a string")
        if not isinstance(data['from'], str):
            raise BadKeyError("'from' must be a string")
        if not isinstance(data['to'], str):
            raise BadKeyError("'to' must be a string")
        if not isinstance(data['ts'], int):
            raise BadKeyError("'ts' must be an integer")
        if not isinstance(data['payload'], dict):
            raise BadKeyError("'payload' must be a dictionary")
        
    
        # Validate UUID format for from/to (except special cases)
        if not _validate_from_field(data['from']):
            raise BadKeyError(f"Invalid 'from' field: {data['from']}")
        if not _validate_to_field(data['to'], data['type']):
            raise BadKeyError(f"Invalid 'to' field: {data['to']} for type {data['type']}")
        
        # Validate signature if present
        sig = data.get('sig')
        if sig is not None and not isinstance(sig, str):
            raise BadKeyError("'sig' must be a string")
        
        # Validate signature format if present
        if sig is not None and not is_base64url(sig):
            raise BadKeyError("'sig' must be valid base64url")

        # Check if signature is required but missing
        if data['type'] not in _ALLOW_UNSIGNED_TYPES and sig is None:
            raise InvalidSigError(f"Message type '{data['type']}' requires signature")
        
        return cls(
            type=data['type'],
            from_=data['from'],
            to=data['to'],
            ts=data['ts'],
            payload=data['payload'],
            sig=sig
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert Envelope back to dictionary"""
        result = {
            'type': self.type,
            'from': self.from_,
            'to': self.to,
            'ts': self.ts,
            'payload': self.payload,
        }
        if self.sig is not None:
            result['sig'] = self.sig
        return result
    
    def to_json(self) -> str:
        """Convert Envelope to JSON string"""
        return json.dumps(self.to_dict(), separators=(',', ':'), sort_keys=True)

def _validate_from_field(from_field: str) -> bool:
    """Validate the 'from' field - must be a valid UUID v4"""
    return is_uuid_v4(from_field)

def _validate_to_field(to_field: str, msg_type: str) -> bool:
    """Validate the 'to' field based on message type"""
    # Special cases
    if to_field == "*":  # Broadcast
        return True
    if to_field == "public":  # Public channel (for MSG_PUBLIC_CHANNEL, FILE_* to public channel)
        return True
    if msg_type == "SERVER_HELLO_JOIN" and is_hostport(to_field):  # Bootstrap only (accepts hostnames and IPs)
        return True
    
    # Regular case: must be UUID v4
    return is_uuid_v4(to_field)

def create_envelope(msg_type: str, from_id: str, to_id: str, payload: Dict[str, Any], 
                   signature: Optional[str] = None, ts: Optional[int] = None) -> Envelope:
    """Helper to create a new envelope with timestamp (now if not provided)"""
    return Envelope(
        type=msg_type,
        from_=from_id,
        to=to_id,
        ts=int(time.time() * 1000) if ts is None else ts,
        payload=payload,
        sig=signature
    )
def verify_transport_envelope(envelope: Envelope, pubkey_b64url: str) -> bool:
    """Verify the envelope signature"""
    from shared.crypto.crypto import rsassa_pss_verify
    if envelope.sig is None:
        return False
    ok = rsassa_pss_verify(pubkey_b64url, json.dumps(envelope.payload, separators=(',', ':'), sort_keys=True).encode(), envelope.sig)
    return ok
         