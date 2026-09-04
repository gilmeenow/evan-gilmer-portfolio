"""Append-only JSONL audit log. Every proposal, risk verdict, and order
this agent ever produces is written here - the point of an "automated"
trading agent is that a human can reconstruct exactly why it did what it
did, after the fact."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def log_event(path: str, event_type: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **payload,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
