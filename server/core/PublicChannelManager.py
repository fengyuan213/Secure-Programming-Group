"""
Public Channel Manager for SOCP v1.3 Protocol.

Implements public channel per SOCP §9.3 and §15.
- RSA-4096 exclusively (no AES)
- Messages signed with RSASSA-PSS
- Auto-join on connect
- group_id="public", creator_id="system"
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, Optional, Set, Any
from dataclasses import dataclass, field

from shared.log import get_logger

if TYPE_CHECKING:
    from server.server import SOCPServer

logger = get_logger(__name__)


@dataclass
class ChannelMember:
    """
    Individual member record for public channel per SOCP §15.
    
    Attributes:
        user_id: UUIDv4 of the member
        user_pubkey: Member's RSA-4096 public key (base64url PEM)
        role: Always "member" for public channel (no admins/owners)
        added_at: Unix timestamp in milliseconds when member joined
    """
    user_id: str
    user_pubkey: str
    role: str = "member"
    added_at: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class PublicChannelState:
    """
    State container for the public channel per SOCP §15.
    
    SOCP v1.3 Requirements:
    - group_id MUST be "public"
    - creator_id MUST be "system"
    - version increments on member changes
    - RSA-4096 only (no AES group key)
    
    The public channel is network-wide and accessible to all users.
    Per SOCP §9.3: "Users are added to the public channel by default
    and cannot be removed."
    """
    
    group_id: str = "public"
    creator_id: str = "system"
    version: int = 0
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    
    # Optional metadata per SOCP §15.1
    meta: Dict[str, Any] = field(default_factory=lambda: {
        "title": "Public Channel",
        "description": "Global broadcast channel for all users on the network",
        "avatar_url": "",
        "extras": {}
    })
    
    # Member tracking: user_id (UUID) -> ChannelMember
    members: Dict[str, ChannelMember] = field(default_factory=dict)
    
    def get_member_ids(self) -> Set[str]:
        """Return set of all member UUIDs."""
        return set(self.members.keys())
    
    def get_member_count(self) -> int:
        """Return total number of members."""
        return len(self.members)
    
    def has_member(self, user_id: str) -> bool:
        """Check if user is a member of the channel."""
        return user_id in self.members
    
    def get_member_info_list(self) -> list[Dict[str, Any]]:
        """
        Get member info list for PUBLIC_CHANNEL_UPDATED message.
        
        Returns list of dicts with member_id only (SOCP v1.3).
        """
        return [
            {"member_id": user_id}
            for user_id in self.members.keys()
        ]


class PublicChannelManager:
    """
    Public Channel Manager - implements SOCP §9.3 and §15.
    
    SOCP v1.3 Protocol Requirements:
    ================================
    
    1. Cryptography (§4):
       - RSA-4096 ONLY (no AES-256-GCM)
       - Messages signed with RSASSA-PSS (SHA-256)
       - content_sig covers: SHA256(ciphertext || from || ts)
    
    2. Public Channel (§9.3):
       - Users join automatically on network connection
       - Cannot be removed (per spec: "cannot be removed")
       - Messages broadcast to all members via servers
       - Servers MUST NOT decrypt messages (end-to-end)
    
    3. Database Model (§15):
       - group_id: "public" (fixed)
       - creator_id: "system" (fixed)
       - version: increments on changes
       - No shared encryption key in v1.3
    
    4. Routing:
       - LOCAL_SERVER broadcasts PUBLIC_CHANNEL_ADD when user joins
       - LOCAL_SERVER broadcasts PUBLIC_CHANNEL_UPDATED with member list
       - Messages use to="*" for network-wide broadcast
       - Servers relay messages to their local members
    
    Implementation Details:
    ----------------------
    - Member tracking includes user_id and pubkey
    - No group key generation (removed in v1.3)
    - Version increments to track state changes
    - Thread-safe for async operations
    """
    
    def __init__(self, server: "SOCPServer") -> None:
        """
        Initialize public channel manager.
        
        Args:
            server: Parent SOCPServer instance
        """
        self.server = server
        self.channel = PublicChannelState()
        logger.info(
            "Initialized public channel manager (SOCP v1.3): group_id='%s', "
            "creator_id='%s', RSA-4096 only",
            self.channel.group_id,
            self.channel.creator_id,
        )
    
    def add_member(self, user_id: str, user_pubkey: str) -> ChannelMember:
        """
        Add a member to the public channel.
        
        Per SOCP §9.3: Users are added automatically when they connect.
        Per SOCP v1.3: No group key wrapping (RSA-4096 only).
        
        Args:
            user_id: UUIDv4 of the user
            user_pubkey: User's RSA-4096 public key (base64url PEM format)
                        May be empty string for remote users (when we don't have their key)
        
        Returns:
            ChannelMember object for the added user
        
        Note:
            - Idempotent: If user already exists, returns existing member
            - Increments channel version on new additions
            - No key wrapping in SOCP v1.3
        """
        # Check if already a member
        if user_id in self.channel.members:
            logger.debug(
                "User %s already in public channel (version=%d)",
                user_id[:8],
                self.channel.version,
            )
            return self.channel.members[user_id]
        
        # Create member record (RSA-4096 only, no key wrapping)
        member = ChannelMember(
            user_id=user_id,
            user_pubkey=user_pubkey,
            role="member",
            added_at=int(time.time() * 1000),
        )
        
        # Add to channel and increment version
        self.channel.members[user_id] = member
        self.channel.version += 1
        
        logger.info(
            "Added user %s to public channel: version=%d, total_members=%d",
            user_id[:8],
            self.channel.version,
            self.channel.get_member_count(),
        )
        
        return member
    
    def remove_member(self, user_id: str) -> bool:
        """
        Remove a member from the public channel.
        
        Note: Per SOCP §9.3, users "cannot be removed" from public channel,
        but this method handles cleanup on disconnect for practical reasons.
        
        Args:
            user_id: UUIDv4 of the user to remove
        
        Returns:
            True if user was removed, False if user was not found
        """
        if user_id not in self.channel.members:
            logger.debug("User %s not in public channel, nothing to remove", user_id[:8])
            return False
        
        del self.channel.members[user_id]
        # Note: Not incrementing version on removal (spec is ambiguous)
        
        logger.info(
            "Removed user %s from public channel: total_members=%d",
            user_id[:8],
            self.channel.get_member_count(),
        )
        return True
    
    def has_member(self, user_id: str) -> bool:
        """
        Check if user is a member of the channel.
        
        Args:
            user_id: UUIDv4 to check
        
        Returns:
            True if user is a member, False otherwise
        """
        return self.channel.has_member(user_id)
    
    def get_member(self, user_id: str) -> Optional[ChannelMember]:
        """
        Get full member record for a user.
        
        Args:
            user_id: UUIDv4 of the member
        
        Returns:
            ChannelMember object if found, None otherwise
        """
        return self.channel.members.get(user_id)
    
    def get_members(self) -> Set[str]:
        """
        Get set of all member UUIDs.
        
        Returns:
            Set of user_id strings (UUIDv4)
        """
        return self.channel.get_member_ids()
    
    def get_member_count(self) -> int:
        """
        Get total number of members.
        
        Returns:
            Integer count of members
        """
        return self.channel.get_member_count()
    
    def update_metadata(self, **kwargs: Any) -> None:
        """
        Update channel metadata fields.
        
        Supported fields per SOCP §15.1:
        - title: Channel title
        - description: Channel description
        - avatar_url: URL to channel avatar image
        - extras: Dict of additional custom metadata
        
        Args:
            **kwargs: Field names and values to update
        
        Note: Increments channel version after update
        """
        updated_fields = []
        for key, value in kwargs.items():
            if key in self.channel.meta:
                self.channel.meta[key] = value
                updated_fields.append(key)
            elif key == "extras" and isinstance(value, dict):
                self.channel.meta["extras"].update(value)
                updated_fields.append("extras")
        
        if updated_fields:
            self.channel.version += 1
            logger.info(
                "Updated public channel metadata (version=%d): %s",
                self.channel.version,
                ", ".join(updated_fields),
            )
    
    def get_channel_info(self) -> Dict[str, Any]:
        """
        Get channel information for client display.
        
        Returns dict with public channel metadata per SOCP §15.
        Suitable for sending in response to channel info requests.
        
        Returns:
            Dict containing: group_id, creator_id, version, created_at,
            meta, member_count
        """
        return {
            "group_id": self.channel.group_id,
            "creator_id": self.channel.creator_id,
            "version": self.channel.version,
            "created_at": self.channel.created_at,
            "meta": self.channel.meta.copy(),
            "member_count": self.channel.get_member_count(),
        }
    
    def get_channel_state(self) -> Dict[str, Any]:
        """
        Get current channel state for diagnostics and monitoring.
        
        Returns:
            Dict with full channel state including member list
        """
        return {
            "group_id": self.channel.group_id,
            "creator_id": self.channel.creator_id,
            "version": self.channel.version,
            "created_at": self.channel.created_at,
            "member_count": self.channel.get_member_count(),
            "members": list(self.channel.members.keys()),
            "meta": self.channel.meta.copy(),
        }
    
    def get_member_list(self) -> list[Dict[str, Any]]:
        """
        Get list of all members with their metadata.
        
        Returns:
            List of dicts, each containing user_id, role, and added_at
        
        Note: Useful for admin/diagnostic interfaces
        """
        return [
            {
                "user_id": member.user_id,
                "role": member.role,
                "added_at": member.added_at,
            }
            for member in self.channel.members.values()
        ]
    
    def clear_all_members(self) -> int:
        """
        Clear all members from the channel (for testing/reset only).
        
        Warning: This is NOT part of the SOCP protocol.
        Use only for testing or administrative purposes.
        
        Returns:
            Number of members that were removed
        """
        count = self.channel.get_member_count()
        self.channel.members.clear()
        self.channel.version += 1
        
        logger.warning(
            "Cleared all %d members from public channel (version=%d)",
            count,
            self.channel.version,
        )
        return count

