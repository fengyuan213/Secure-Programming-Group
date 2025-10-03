from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Dict, Optional, Set, Any
from dataclasses import dataclass, field

from shared.crypto.crypto import rsa_oaep_encrypt
from shared.log import get_logger

if TYPE_CHECKING:
    from server.server import SOCPServer

logger = get_logger(__name__)


@dataclass
class ChannelMember:
    """Individual member record with metadata per SOCP §15."""
    user_id: str
    wrapped_key: str  # base64url RSA-OAEP wrapped channel key
    role: str = "member"  # Always "member" for public channel
    added_at: int = field(default_factory=lambda: int(time.time() * 1000))  # Unix ms
    user_pubkey: Optional[str] = None  # Cached for re-wrapping


@dataclass
class PublicChannelState:
    """State for the public channel per SOCP §15."""
    
    group_id: str = "public"
    creator_id: str = "system"
    version: int = 0
    channel_key: Optional[bytes] = None  # AES-256 key (32 bytes)
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))  # Unix ms
    
    # Optional metadata per SOCP §15.1
    meta: Dict[str, Any] = field(default_factory=lambda: {
        "title": "Public Channel",
        "description": "Global broadcast channel for all users",
        "avatar_url": "",
        "extras": {}
    })
    
    # Member tracking
    members: Dict[str, ChannelMember] = field(default_factory=dict)  # user_id -> ChannelMember
    
    def get_member_ids(self) -> Set[str]:
        """Get set of member user IDs."""
        return set(self.members.keys())
    
    def get_wrapped_keys_list(self) -> list[Dict[str, str]]:
        """Get list of wrapped keys for PUBLIC_CHANNEL_UPDATED."""
        return [
            {"member_id": user_id, "wrapped_key": member.wrapped_key}
            for user_id, member in self.members.items()
        ]


class PublicChannelManager:
    """
    Manages the public channel per SOCP §9.3 and §15.
    
    - Users join automatically when connecting
    - Channel key is generated and distributed to all members
    - Handles key rotation when new members join
    """
    
    def __init__(self, server: "SOCPServer"):
        self.server = server
        self.channel = PublicChannelState()
        self._initialize_channel()
    
    def _initialize_channel(self) -> None:
        """Initialize or regenerate the channel key."""
        self.channel.channel_key = secrets.token_bytes(32)  # AES-256 key
        logger.info("Initialized public channel with group_id='public'")
    
    def _wrap_key_for_user(self, user_id: str, user_pubkey: str) -> str:
        """
        Wrap the channel key with user's public key using RSA-OAEP.
        
        Args:
            user_id: UUID of the user
            user_pubkey: User's RSA-4096 public key (base64url PEM)
            
        Returns:
            Base64url encoded wrapped key
        """
        if not self.channel.channel_key:
            raise ValueError("Channel key not initialized")
        
        try:
            wrapped = rsa_oaep_encrypt(user_pubkey, self.channel.channel_key)
            return wrapped
        except Exception as exc:
            logger.error("Failed to wrap channel key for user %s: %s", user_id, exc)
            raise
    
    def add_member(self, user_id: str, user_pubkey: str) -> ChannelMember:
        """
        Add a user to the public channel and wrap key for them.
        
        Args:
            user_id: UUID of the user
            user_pubkey: User's RSA-4096 public key (base64url PEM)
            
        Returns:
            ChannelMember object for the added user
        """
        if user_id in self.channel.members:
            logger.debug("User %s already in public channel", user_id)
            return self.channel.members[user_id]
        
        # Wrap the channel key for this user
        wrapped_key = self._wrap_key_for_user(user_id, user_pubkey)
        
        # Create member record
        member = ChannelMember(
            user_id=user_id,
            wrapped_key=wrapped_key,
            role="member",
            user_pubkey=user_pubkey,  # Cache for potential re-wrapping
        )
        
        # Add to members
        self.channel.members[user_id] = member
        self.channel.version += 1
        
        logger.info(
            "Added user %s to public channel (version %d, %d members)",
            user_id,
            self.channel.version,
            len(self.channel.members),
        )
        
        return member
    
    def remove_member(self, user_id: str) -> bool:
        """
        Remove a user from the public channel.
        
        Per spec, users can't actually be removed from public channel,
        but this handles cleanup on disconnect.
        
        Returns:
            True if user was removed, False if not found
        """
        if user_id in self.channel.members:
            del self.channel.members[user_id]
            logger.info("Removed user %s from public channel", user_id)
            return True
        return False
    
    def get_wrapped_key(self, user_id: str) -> Optional[str]:
        """Get the wrapped channel key for a specific user."""
        member = self.channel.members.get(user_id)
        return member.wrapped_key if member else None
    
    def get_member(self, user_id: str) -> Optional[ChannelMember]:
        """Get full member record for a user."""
        return self.channel.members.get(user_id)
    
    def get_all_wrapped_keys(self) -> Dict[str, str]:
        """Get all wrapped keys for distribution."""
        return {
            user_id: member.wrapped_key 
            for user_id, member in self.channel.members.items()
        }
    
    def get_members(self) -> Set[str]:
        """Get current channel member IDs."""
        return self.channel.get_member_ids()
    
    def update_metadata(self, **kwargs) -> None:
        """
        Update channel metadata.
        
        Supported fields: title, description, avatar_url, extras
        """
        for key, value in kwargs.items():
            if key in self.channel.meta:
                self.channel.meta[key] = value
                logger.info("Updated public channel metadata: %s = %s", key, value)
            elif key == "extras":
                self.channel.meta["extras"].update(value)
        
        self.channel.version += 1
    
    def rotate_key(self) -> None:
        """
        Rotate the channel key (e.g., after member changes).
        
        Note: Per SOCP spec, public channel doesn't typically rotate,
        but this is here for completeness. Re-wraps key for all members.
        """
        old_version = self.channel.version
        self._initialize_channel()
        self.channel.version = old_version + 1
        
        # Re-wrap for all current members
        for user_id, member in list(self.channel.members.items()):
            if member.user_pubkey:
                try:
                    wrapped_key = self._wrap_key_for_user(user_id, member.user_pubkey)
                    member.wrapped_key = wrapped_key
                except Exception as exc:
                    logger.error("Failed to re-wrap key for %s during rotation: %s", user_id, exc)
        
        logger.info("Rotated public channel key to version %d", self.channel.version)
    
    def get_channel_info(self) -> Dict[str, Any]:
        """
        Get full channel information including metadata.
        
        Returns dict suitable for sending to clients.
        """
        return {
            "group_id": self.channel.group_id,
            "creator_id": self.channel.creator_id,
            "version": self.channel.version,
            "created_at": self.channel.created_at,
            "meta": self.channel.meta.copy(),
            "member_count": len(self.channel.members),
        }
    
    def get_channel_state(self) -> Dict[str, Any]:
        """Get current channel state for diagnostics."""
        return {
            "group_id": self.channel.group_id,
            "creator_id": self.channel.creator_id,
            "version": self.channel.version,
            "created_at": self.channel.created_at,
            "member_count": len(self.channel.members),
            "members": list(self.channel.members.keys()),
            "meta": self.channel.meta.copy(),
        }
    
    def get_member_list(self) -> list[Dict[str, Any]]:
        """
        Get list of all members with their metadata.
        
        Useful for diagnostics and admin views.
        """
        return [
            {
                "user_id": member.user_id,
                "role": member.role,
                "added_at": member.added_at,
            }
            for member in self.channel.members.values()
        ]

