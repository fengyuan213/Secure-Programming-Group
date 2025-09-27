# SOCP Client (Milestone 1)

This milestone implements the initial client features per SOCP:

- WebSocket client (one JSON object per WS text frame)
- Envelope usage and validation
- Generate/cache `user_id` (UUIDv4)
- RSA-4096 keypair generation and storage (base64url pubkey used in USER_HELLO)
- USER_HELLO send
- Presence cache from USER_ADVERTISE/USER_REMOVE
- `/list` command rendering presence

## Run

```bash
python -m client.socp_cli run --server ws://localhost:8765
```

Options:

- `--user-id` (optional): pre-set UUIDv4; otherwise generated
- `--server-id` (optional): known local server UUID for USER_HELLO `to`

Available commands in this milestone:

- `/list` — shows cached presence
- `/help`, `/quit`

Later milestones introduce `/tell`, `/all`, and `/file` once interop testing with servers is ready.
