import asyncio
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.server import SOCPServer
from shared.envelope import create_envelope


class DummyLink:
    def __init__(self):
        self.sent_messages = []
        self.connection_type = None
        self.server_id = None
        self.identified = False

    async def send_message(self, envelope):
        self.sent_messages.append(envelope)

    async def close(self):
        pass


def test_server_welcome_contains_other_servers(monkeypatch):
    async def scenario():
        server = SOCPServer(host="10.10.10.1", port=9000)

        # Pretend the introducer already knows another server on the network.
        existing_server_id = str(uuid.uuid4())
        server.server_addrs[existing_server_id] = ("203.0.113.10", 9100)
        server.server_pubkeys[existing_server_id] = "EXISTING_PUBKEY"

        sent_announcements = {}

        async def fake_broadcast(server_id, server_info):
            sent_announcements["server_id"] = server_id
            sent_announcements["info"] = server_info

        monkeypatch.setattr(server, "broadcast_server_announce", fake_broadcast)

        connection = DummyLink()

        requested_id = str(uuid.uuid4())
        envelope = create_envelope(
            "SERVER_HELLO_JOIN",
            requested_id,
            f"{server.host}:{server.port}",
            {"host": "198.51.100.7", "port": 7777, "pubkey": "JOIN_KEY"},
        )

        await server.handle_server_hello_join(connection, envelope)

        assert connection.connection_type == "server"
        assert connection.identified is True
        assert connection.server_id == requested_id

        assert len(connection.sent_messages) == 1
        welcome = connection.sent_messages[0]
        assert welcome.type == "SERVER_WELCOME"
        assert welcome.payload["assigned_id"] == requested_id

        server_entries = welcome.payload["servers"]
        advertised_ids = {entry["server_id"] for entry in server_entries}
        assert server.current_server_id in advertised_ids
        assert existing_server_id in advertised_ids
        assert requested_id not in advertised_ids

        # Broadcast should include the joining server's details.
        assert sent_announcements["server_id"] == requested_id
        assert sent_announcements["info"]["host"] == "198.51.100.7"
        assert sent_announcements["info"]["port"] == 7777
        assert sent_announcements["info"]["pubkey"] == "JOIN_KEY"

    asyncio.run(scenario())


def test_duplicate_server_id_is_reassigned(monkeypatch):
    async def scenario():
        server = SOCPServer(host="10.10.10.1", port=9000)

        duplicate_id = str(uuid.uuid4())
        if duplicate_id == server.current_server_id:
            duplicate_id = str(uuid.uuid4())
        server.server_addrs[duplicate_id] = ("192.0.2.1", 9100)
        server.server_pubkeys[duplicate_id] = "INTRO_KEY"

        async def fake_broadcast(server_id, server_info):
            pass

        monkeypatch.setattr(server, "broadcast_server_announce", fake_broadcast)

        connection = DummyLink()

        envelope = create_envelope(
            "SERVER_HELLO_JOIN",
            duplicate_id,
            f"{server.host}:{server.port}",
            {"host": "198.51.100.8", "port": 8888, "pubkey": "JOIN_KEY"},
        )

        await server.handle_server_hello_join(connection, envelope)

        welcome = connection.sent_messages[0]
        assigned_id = welcome.payload["assigned_id"]

        assert assigned_id != duplicate_id
        assert is_uuid_v4(assigned_id)
        assert assigned_id == connection.server_id

        # Existing server entry must be preserved
        assert duplicate_id in server.server_addrs
        assert assigned_id in server.server_addrs

    asyncio.run(scenario())


def is_uuid_v4(value: str) -> bool:
    try:
        return str(uuid.UUID(value)).lower() == value.lower() and uuid.UUID(value).version == 4
    except Exception:
        return False
