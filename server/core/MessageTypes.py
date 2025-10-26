from __future__ import annotations

from enum import Enum, auto
from typing import Set


class MessageType(str, Enum):
    """SOCP Protocol Message Types per specification sections 8 & 9."""
    
    # Server-to-Server Protocol (Section 8)
    SERVER_HELLO_JOIN = "SERVER_HELLO_JOIN"      # §8.1 Bootstrap - new server joins
    SERVER_HELLO_LINK = "SERVER_HELLO_LINK"      # Direct server linking after welcome
    SERVER_WELCOME = "SERVER_WELCOME"            # §8.1 Introducer response
    SERVER_ANNOUNCE = "SERVER_ANNOUNCE"          # §8.1 Broadcast server presence
    USER_ADVERTISE = "USER_ADVERTISE"            # §8.2 Broadcast user presence
    USER_REMOVE = "USER_REMOVE"                  # §8.2 Broadcast user disconnect
    SERVER_DELIVER = "SERVER_DELIVER"            # §8.3 Forward message to remote user
    HEARTBEAT = "HEARTBEAT"                      # §8.4 Keep-alive ping
    
    # User-to-Server Protocol (Section 9)
    USER_HELLO = "USER_HELLO"                    # §9.1 User connects to server
    MSG_DIRECT = "MSG_DIRECT"                    # §9.2 User sends DM
    USER_DELIVER = "USER_DELIVER"                # §9.2 Server delivers DM to user
    
    # Public Channel (Section 9.3) - Not yet implemented
    PUBLIC_CHANNEL_ADD = "PUBLIC_CHANNEL_ADD"
    PUBLIC_CHANNEL_UPDATED = "PUBLIC_CHANNEL_UPDATED"
    PUBLIC_CHANNEL_KEY_SHARE = "PUBLIC_CHANNEL_KEY_SHARE"
    MSG_PUBLIC_CHANNEL = "MSG_PUBLIC_CHANNEL"
    
    # File Transfer (Section 9.4) - Not yet implemented
    FILE_START = "FILE_START"
    FILE_CHUNK = "FILE_CHUNK"
    FILE_END = "FILE_END"
    
    # Control & Error (Section 9.5)
    ACK = "ACK"                                  # Transport acknowledgment (optional)
    ERROR = "ERROR"                              # Error responses
    CTRL_CLOSE = "CTRL_CLOSE"                    # Graceful close (optional)
    
    @classmethod
    def from_string(cls, value: str) -> MessageType:
        """Convert string to MessageType enum, raise ValueError if unknown."""
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"Unknown message type: {value}")
    
    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Check if string is a valid message type."""
        try:
            cls(value)
            return True
        except ValueError:
            return False


class ConnectionType(str, Enum):
    """Connection type classification."""
    USER = "user"
    SERVER = "server"
    UNIDENTIFIED = "unidentified"


# Message types that MAY be sent unsigned (first-contact flows)
UNSIGNED_ALLOWED: Set[MessageType] = {
    MessageType.USER_HELLO,
    MessageType.SERVER_HELLO_JOIN,
    MessageType.SERVER_HELLO_LINK,
    MessageType.SERVER_WELCOME,
    MessageType.SERVER_ANNOUNCE,
}

# Message types that are first messages for identification
IDENTIFICATION_MESSAGES: Set[MessageType] = {
    MessageType.USER_HELLO,
    MessageType.SERVER_HELLO_JOIN,
    MessageType.SERVER_HELLO_LINK,
}

# Message types sent from users
USER_MESSAGES: Set[MessageType] = {
    MessageType.USER_HELLO,
    MessageType.MSG_DIRECT,
    MessageType.MSG_PUBLIC_CHANNEL,
    MessageType.FILE_START,
    MessageType.FILE_CHUNK,
    MessageType.FILE_END,
}

# Message types sent from servers
SERVER_MESSAGES: Set[MessageType] = {
    MessageType.SERVER_HELLO_JOIN,
    MessageType.SERVER_HELLO_LINK,
    MessageType.SERVER_WELCOME,
    MessageType.SERVER_ANNOUNCE,
    MessageType.USER_ADVERTISE,
    MessageType.USER_REMOVE,
    MessageType.SERVER_DELIVER,
    MessageType.HEARTBEAT,
    MessageType.PUBLIC_CHANNEL_ADD,
    MessageType.PUBLIC_CHANNEL_UPDATED,
    MessageType.PUBLIC_CHANNEL_KEY_SHARE,
}

# Message types that should be gossiped/forwarded
GOSSIP_MESSAGES: Set[MessageType] = {
    MessageType.USER_ADVERTISE,
    MessageType.USER_REMOVE,
    MessageType.PUBLIC_CHANNEL_ADD,
    MessageType.PUBLIC_CHANNEL_UPDATED,
}

