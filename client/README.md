# SOCP Client (Complete Implementation)

A full-featured WebSocket client for the Secure Overlay Chat Protocol (SOCP) v1.3.

## Features

- **WebSocket Transport**: One JSON object per WebSocket text frame
- **Identity & Keys**: UUIDv4 user_id, RSA-4096 keypair generation/storage
- **E2EE Messaging**: RSA-OAEP encryption + RSASSA-PSS signatures
- **File Transfer**: Chunked transfer with SHA-256 verification
- **Presence Management**: Real-time user presence via gossip
- **Resilience**: Auto-reconnect with exponential backoff

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic Run

```bash
python -m client.socp_cli run --server ws://localhost:8765
```

### Options

- `--user-id` (optional): pre-set UUIDv4; otherwise auto-generated
- `--server-id` (optional): known local server UUID for USER_HELLO

### Commands

- `/list` — show online users (from presence cache)
- `/pubkey set <uid> <b64url>` — store recipient's public key
- `/pubkey list` — show stored public keys
- `/tell <user> <message>` — send E2EE direct message
- `/all <message>` — send to public channel
- `/file <user> <path>` — send file (chunked, encrypted)
- `/help`, `/quit`

### Examples

```bash
# Start client
python -m client.socp_cli run

# Set recipient's pubkey (get from server or out-of-band)
/pubkey set alice-uuid MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...

# Send encrypted message
/tell alice-uuid Hello Alice!

# Send to public channel
/all Hello everyone!

# Send file
/file alice-uuid /path/to/document.pdf
```

## Testing

Run the golden test:

```bash
python -m client.test_golden
```

## Protocol Compliance

- ✅ C0.1: WebSocket client (one JSON per frame)
- ✅ C0.2: Envelope codec usage and validation
- ✅ C1.2: Generate/cache user_id (UUIDv4)
- ✅ C1.3: RSA-4096 keypair generation and base64url export
- ✅ C2.4: USER_HELLO send with proper keys
- ✅ C3.5: /list command with presence rendering
- ✅ C3.6: /tell command (E2EE)
- ✅ C3.7: /all command (public channel)
- ✅ C3.8: ACK/ERROR frame handling
- ✅ C4.9: RSA-OAEP and RSASSA-PSS crypto helpers
- ✅ C4.10: E2EE DM send/receive with signature verification
- ✅ C4.11: Content signature canonicalization
- ✅ C5.11: File manifest and chunking
- ✅ C5.12: File send/receive with reassembly and verification
- ✅ C6.12: Golden Bob→Alice DM test

## Security Notes

- All direct messages use RSA-OAEP encryption
- Content signatures use RSASSA-PSS over canonical payloads
- File transfers verify SHA-256 integrity
- Unsigned or invalid messages are rejected
- Private keys stored unencrypted in `~/.socp/` (consider password protection for production)
