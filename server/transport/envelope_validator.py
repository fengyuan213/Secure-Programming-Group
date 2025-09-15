"""
Validate that every inbound frame uses the envelope:
{
  "type": "STRING",
  "from": "UUID",
  "to":   "UUID | \"*\" | host:port (bootstrap only)",
  "ts":   "INT (unix ms)",
  "payload": { ... },
  "sig": "BASE64URL (optional only for HELLO/BOOTSTRAP)"
}



Special to cases:
- "*" allowed for broadcasts (server gossip, public channel fan-out).
- host:port allowed only during SERVER_HELLO_JOIN (bootstrap)
"""