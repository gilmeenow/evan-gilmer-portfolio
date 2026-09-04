"""Configuration and hard risk limits for the trading agent.

All limits here are enforced in code (src/risk.py), not just in the model's
system prompt - the prompt tells Claude the rules, but a proposal that
violates them is rejected before any order is ever placed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TradingConfig:
    # Universe: the agent may only propose trades in these symbols. Keeping
    # this explicit (rather than "whatever Claude finds") is itself a risk
    # control - it bounds the blast radius of a bad research call.
    watchlist: tuple[str, ...] = ("AAPL", "MSFT", "GOOGL", "AMZN", "VOO")

    # Position sizing: hard dollar cap per trade. This repo defaults to a
    # small size on purpose - it's a showcase agent, not a sizing engine.
    max_position_usd: float = 250.0

    # Pacing: caps how many *orders* (not proposals) can execute per UTC day.
    max_trades_per_day: int = 3

    # Only market and limit orders on long equity positions are ever
    # constructed - no margin, no options, no crypto, no shorting. This is
    # also enforced structurally: the MCP toolset only ever exposes
    # equity read tools plus place_equity_order (never options/crypto tools).
    allow_short_selling: bool = False

    # Safety default: the agent always runs in dry-run (research +
    # recommendation only, no orders placed) unless the operator explicitly
    # passes --live on the CLI. This field exists so callers can also flip
    # it in a config file for automated dry-run smoke tests.
    dry_run: bool = True

    model: str = os.environ.get("TRADING_AGENT_MODEL", "claude-opus-5")
    mcp_server_url: str = os.environ.get(
        "ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading"
    )
    mcp_server_name: str = "robinhood-trading"

    state_dir: str = os.environ.get("TRADING_AGENT_STATE_DIR", ".state")
    log_path: str = field(default_factory=lambda: os.path.join(
        os.environ.get("TRADING_AGENT_STATE_DIR", ".state"), "decisions.jsonl"
    ))
    trade_count_path: str = field(default_factory=lambda: os.path.join(
        os.environ.get("TRADING_AGENT_STATE_DIR", ".state"), "trade_count.json"
    ))

    @staticmethod
    def load(path: str | None) -> "TradingConfig":
        """Load overrides from a JSON file on top of the defaults."""
        if not path:
            return TradingConfig()
        with open(path, "r", encoding="utf-8") as fh:
            overrides = json.load(fh)
        if "watchlist" in overrides:
            overrides["watchlist"] = tuple(overrides["watchlist"])
        return TradingConfig(**overrides)
