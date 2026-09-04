"""Tiny on-disk state: today's executed-order count, used to enforce the
daily trade cap across separate runs of the agent (e.g. one run per hour
via cron)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_trades_today(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("date") != _today():
        return 0
    return int(data.get("count", 0))


def record_trade(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = get_trades_today(path) + 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": _today(), "count": count}, fh)
