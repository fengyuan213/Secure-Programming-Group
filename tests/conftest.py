import asyncio
import json
import time
import uuid
import websockets


def ms() -> int:
    return int(time.time() * 1000)


def default_server_id() -> str:
    # Placeholder server ID used in manual testing helpers.
    return "4071c714-bd14-4ac5-bc75-b2212669881b"


def default_server_ws() -> str:
    return "ws://127.0.0.1:8765"


async def user_hello(server_ws: str | None = None, current_server_id: str | None = None) -> None:
    env = {
        "type": "USER_HELLO",
        "from": str(uuid.uuid4()),
        "to": (current_server_id or default_server_id()),
        "ts": ms(),
        "payload": {
            "client": "cli-v1",
            "pubkey": "AA",
            "enc_pubkey": "AA",
        },
        "sig": "AA",
    }
    async with websockets.connect(server_ws or default_server_ws()) as ws:
        await ws.send(json.dumps(env, separators=(",", ":"), sort_keys=True))
        await asyncio.sleep(0.5)


async def server_hello_join(server_ws: str | None = None) -> None:
    other_server_id = str(uuid.uuid4())
    env = {
        "type": "SERVER_HELLO_JOIN",
        "from": other_server_id,
        "to": "127.0.0.1:8765",
        "ts": ms(),
        "payload": {"host": "127.0.0.1", "port": 7777, "pubkey": "AA"},
        "sig": "AA",
    }
    async with websockets.connect(server_ws or default_server_ws()) as ws:
        await ws.send(json.dumps(env, separators=(",", ":"), sort_keys=True))
        await ws.recv()
