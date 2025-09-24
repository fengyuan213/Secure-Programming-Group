

from __future__ import annotations
import uuid
from shared.utils import is_uuid_v4


def generate_server_id() -> str:
    """Generate a new UUID v4 for server identification"""
    return str(uuid.uuid4())


def generate_user_id() -> str:
    """Generate a new UUID v4 for user identification"""
    return str(uuid.uuid4())


def validate_server_id(server_id: str) -> bool:
    """Validate that a server ID is a proper UUID v4"""
    return is_uuid_v4(server_id)


def validate_user_id(user_id: str) -> bool:
    """Validate that a user ID is a proper UUID v4"""
    return is_uuid_v4(user_id)