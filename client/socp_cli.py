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
from .pubdir import PubKeyDirectory
from .crypto import rsa_oaep_encrypt, rsassa_pss_sign
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
                if line.startswith("/pubkey set "):
                    try:
                        _, _, target, key = line.split(" ", 3)
                    except Exception:
                        console.print("Usage: /pubkey set <uid> <b64url>")
                        continue
                    PubKeyDirectory().set(target, key)
                    console.print("Saved pubkey")
                    continue
                if line == "/pubkey list":
                    for k, v in PubKeyDirectory().all().items():
                        console.print(f"{k[:8]}... {v[:16]}...")
                    continue
                if line.startswith("/tell "):
                    try:
                        _, dest, *msg_parts = line.split(" ")
                        msg = " ".join(msg_parts).strip()
                    except Exception:
                        console.print("Usage: /tell <user> <message>")
                        continue
                    recip = PubKeyDirectory().get(dest)
                    if not recip:
                        console.print("Recipient pubkey unknown. Use /pubkey set <uid> <b64url>")
                        continue
                    ts = _now_ms()
                    ciphertext = rsa_oaep_encrypt(recip, msg.encode())
                    canonical = (ciphertext + uid + dest + str(ts)).encode()
                    payload = {
                        "ciphertext": ciphertext,
                        "sender_pub": kp.public_b64url(),
                        "content_sig": rsassa_pss_sign(kp.private_pem, canonical),
                    }
                    env = create_envelope("MSG_DIRECT", uid, dest, payload, ts=ts)
                    await session.send(env)
                    continue
                if line.startswith("/all "):
                    msg = line[len("/all "):].strip()
                    ts = _now_ms()
                    # Placeholder ciphertext for public channel in this milestone
                    import base64
                    ciphertext = base64.urlsafe_b64encode(msg.encode()).decode().rstrip("=")
                    canonical = (ciphertext + uid + str(ts)).encode()
                    payload = {
                        "ciphertext": ciphertext,
                        "sender_pub": kp.public_b64url(),
                        "content_sig": rsassa_pss_sign(kp.private_pem, canonical),
                    }
                    env = create_envelope("MSG_PUBLIC_CHANNEL", uid, "public", payload, ts=ts)
                    await session.send(env)
                    continue
                if line.startswith("/file "):
                    parts = line.split(" ", 2)
                    if len(parts) < 3:
                        console.print("Usage: /file <user> <path>")
                        continue
                    dest, path = parts[1], parts[2]
                    from pathlib import Path
                    p = Path(path)
                    if not p.exists():
                        console.print("File not found")
                        continue
                    # Manifest
                    import hashlib, math
                    data = p.read_bytes()
                    file_id = str(uuid.uuid4())
                    manifest = {
                        "file_id": file_id,
                        "name": p.name,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "mode": "dm",
                    }
                    await session.send(create_envelope("FILE_START", uid, dest, manifest, ts=_now_ms()))
                    # Chunks (encrypt each with recip RSA)
                    recip = PubKeyDirectory().get(dest)
                    if not recip:
                        console.print("Recipient pubkey unknown for chunks. Aborting.")
                        continue
                    chunk_size = 190
                    total = math.ceil(len(data)/chunk_size)
                    for i in range(total):
                        chunk = data[i*chunk_size:(i+1)*chunk_size]
                        ct = rsa_oaep_encrypt(recip, chunk)
                        ch_payload = {"file_id": file_id, "index": i, "ciphertext": ct}
                        await session.send(create_envelope("FILE_CHUNK", uid, dest, ch_payload, ts=_now_ms()))
                    await session.send(create_envelope("FILE_END", uid, dest, {"file_id": file_id}, ts=_now_ms()))
                    console.print(f"Sent {total} chunks and FILE_END for {p.name}")
                    continue
                console.print("Unknown command. Try /list, /pubkey, /tell, /all, /file")
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


