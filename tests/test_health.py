import asyncio
import json
import time
import uuid
from contextlib import suppress

import pytest


class DummyWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send(self, data: str) -> None:
        self.sent_messages.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.mark.asyncio
async def test_health_loop_sends_heartbeat():
    from server.server import SOCPServer
    from server.core.ConnectionLink import ConnectionLink

    server = SOCPServer(heartbeat_interval=0.05, heartbeat_timeout=5.0)
    dummy = DummyWebSocket()
    link = ConnectionLink(dummy, connection_type="server")
    remote_id = str(uuid.uuid4())
    link.server_id = remote_id
    link.identified = True
    link.last_seen = time.monotonic()
    server.servers[remote_id] = link
    server.all_connections.add(link)

    task = asyncio.create_task(server._health_loop())
    await asyncio.sleep(0.12)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert any(json.loads(msg)["type"] == "HEARTBEAT" for msg in dummy.sent_messages)


@pytest.mark.asyncio
async def test_health_loop_closes_stale_links():
    from server.server import SOCPServer
    from server.core.ConnectionLink import ConnectionLink

    server = SOCPServer(heartbeat_interval=0.05, heartbeat_timeout=0.05)
    dummy = DummyWebSocket()
    link = ConnectionLink(dummy, connection_type="server")
    remote_id = str(uuid.uuid4())
    link.server_id = remote_id
    link.identified = True
    link.last_seen = time.monotonic() - 0.2
    server.servers[remote_id] = link
    server.all_connections.add(link)

    task = asyncio.create_task(server._health_loop())
    await asyncio.sleep(0.08)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert dummy.closed is True
    assert dummy.close_code == 1011
    assert remote_id not in server.servers


def test_get_status_reports_server_links():
    from server.server import SOCPServer
    from server.core.ConnectionLink import ConnectionLink

    server = SOCPServer()
    dummy = DummyWebSocket()
    link = ConnectionLink(dummy, connection_type="server")
    remote_id = str(uuid.uuid4())
    link.server_id = remote_id
    link.identified = True
    link.last_seen = 123.456
    server.servers[remote_id] = link
    server.server_addrs[remote_id] = ("127.0.0.1", 9000)

    status = server.get_status()

    assert status["server_id"] == server.current_server_id
    assert remote_id in status["server_links"]
    assert status["server_links"][remote_id]["connected"] is True
    assert status["server_links"][remote_id]["last_seen"] == pytest.approx(123.456)
    assert status["known_servers"][remote_id] == ("127.0.0.1", 9000)
