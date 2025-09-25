#!/usr/bin/env python3

from __future__ import annotations
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from shared.envelope import create_envelope
from shared.log import get_logger
from .keys import RSAKeypair, load_keypair, save_keypair
from .state import Presence
from .ws_client import ClientSession

app = typer.Typer(help="SOCP v1.3 Client CLI")
console = Console()
logger = get_logger(__name__)


def _default_server() -> str:
    return os.getenv("SOCP_SERVER", "ws://localhost:8765")


@app.command()
def hello(
    user_id: Optional[str] = typer.Option(None, help="UUIDv4 user id; generated if omitted"),
    server: str = typer.Option(_default_server(), help="WebSocket URL of local server"),
    server_id: Optional[str] = typer.Option(None, help="UUIDv4 of local server if known"),
    display_name: Optional[str] = typer.Option(None, help="Optional display name"),
):
    """Send USER_HELLO to local server and exit."""
    uid = user_id or str(uuid.uuid4())
    payload = {
        "client": "cli-v1",
        "pubkey": "",
        "enc_pubkey": "",
        "meta": {"display_name": display_name} if display_name else {},
    }
    env = create_envelope("USER_HELLO", uid, (server_id or uid), payload)
    console.print(json.dumps(env.to_dict(), indent=2))


@app.command()
def run(
    server: str = typer.Option(_default_server(), help="WebSocket URL of local server"),
    user_id: Optional[str] = typer.Option(None, help="UUIDv4 user id; generated if omitted"),
    server_id: Optional[str] = typer.Option(None, help="UUIDv4 of local server if known"),
):
    """Start interactive client loop (placeholder)."""
    uid = user_id or str(uuid.uuid4())
    console.print(f"[bold green]SOCP client starting[/] as {uid[:8]} on {server}")
    key_path = Path.home() / ".socp" / f"{uid}"
    kp = load_keypair(key_path)
    if kp is None:
        kp = RSAKeypair.generate()
        save_keypair(key_path, kp)
        console.print("Generated new RSA-4096 keypair")

    async def main_loop() -> None:
        session = ClientSession(user_id=uid, server_ws_url=server)
        await session.connect()
        hello_ts = _now_ms()
        hello_env = create_envelope(
            "USER_HELLO",
            uid,
            (server_id or uid),
            {
                "client": "cli-v1",
                # base64url (no padding) per SOCP
                "pubkey": kp.public_b64url(),
                "enc_pubkey": kp.public_b64url(),
                "meta": {},
            },
            ts=hello_ts,
        )
        await session.send(hello_env)

        presence = Presence()

        async def default_handler(env):
            if env.type == "USER_ADVERTISE":
                uid2 = env.payload.get("user_id")
                meta = env.payload.get("meta", {})
                presence.add(uid2, meta)
            elif env.type == "USER_REMOVE":
                uid2 = env.payload.get("user_id")
                presence.remove(uid2)
            elif env.type == "ERROR":
                console.print(f"[red]ERROR {env.payload.get('code')}[/]: {env.payload.get('detail')}")
            elif env.type == "ACK":
                console.print(f"[dim]ACK {env.payload.get('msg_ref')}[/]")
            else:
                console.print(f"[dim]recv {env.type}[/]")

        recv_task = asyncio.create_task(session.recv_loop(default_handler))

        try:
            while True:
                line = input(": ").strip()
                if not line:
                    continue
                if line in {"/quit", "/exit"}:
                    break
                if line == "/help":
                    console.print("/list, /tell <user> <msg>, /all <msg>, /file <user> <path>, /quit")
                    continue
                if line == "/list":
                    users = presence.list_sorted()
                    table = Table(title="Online Users")
                    table.add_column("User ID")
                    for u in users:
                        table.add_row(u)
                    console.print(table)
                    continue
                # Other commands are not available in Milestone 1
                console.print("Unknown or unavailable command. Try /list, /help, /quit.")
                continue
                console.print("Unknown command. /help")
        finally:
            recv_task.cancel()
            await session.close()

    def _now_ms() -> int:
        import time
        return int(time.time() * 1000)

    asyncio.run(main_loop())


if __name__ == "__main__":
    app()


