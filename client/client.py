#!/usr/bin/env python3
"""
SOCP v1.3 Client Implementation

Simple client to test the WebSocket server with first-message identification.
Demonstrates both USER_HELLO and SERVER_HELLO_JOIN flows.
"""

from __future__ import annotations
import asyncio
import json
import logging
import uuid
from typing import Optional
import websockets

from shared.envelope import create_envelope

from shared.log import get_logger
# Confiure Logging
logger = get_logger(__name__)
