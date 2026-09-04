"""Tool schemas and small data containers shared across the agent.

Two different kinds of "tool" appear in this project:

- MCP tools (get_equity_quotes, place_equity_order, ...) are hosted on
  Robinhood's remote MCP server and executed server-side by Anthropic via
  the MCP connector. We never see their raw wire format - only the
  resolved content blocks in the model's response.
- The tools defined below (propose_trade, no_action, report_snapshot) are
  ordinary client-side tools. Claude "calls" them, our code executes them
  locally, and we feed the result back in the next turn. They exist purely
  so the model's output is structured data we can validate, instead of
  free text we'd have to parse.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tools the research/decision call is allowed to use on the remote MCP
# server. Deliberately read-only - this call can look, but cannot place,
# cancel, or modify anything.
RESEARCH_MCP_TOOLS = (
    "get_accounts",
    "get_equity_positions",
    "get_equity_quotes",
    "get_equity_fundamentals",
    "get_equity_historicals",
    "get_equity_technical_indicators",
    "get_equity_news",
    "get_equity_orders",
)

# Tools the independent verification snapshot is allowed to use. A strict
# subset of the research tools - just enough to answer "what is the price,
# what do we hold, what cash do we have", nothing else.
SNAPSHOT_MCP_TOOLS = (
    "get_accounts",
    "get_equity_positions",
    "get_equity_quotes",
)

# Tools the execution call is allowed to use. Only ever enabled for a
# single, already risk-approved order, with the exact parameters embedded
# in the prompt - this toolset is never combined with research tools.
EXECUTION_MCP_TOOLS = ("place_equity_order",)


PROPOSE_TRADE_TOOL = {
    "name": "propose_trade",
    "description": (
        "Propose a single equity trade for automated risk review. This does "
        "NOT place an order - it only records a recommendation. Call this "
        "once per symbol you want to act on."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Equity ticker, e.g. AAPL"},
            "action": {"type": "string", "enum": ["buy", "sell"]},
            "quantity": {"type": "number", "description": "Shares to trade (fractional allowed), must be > 0"},
            "order_type": {"type": "string", "enum": ["market", "limit"]},
            "limit_price": {"type": "number", "description": "Required when order_type is 'limit'; omit for market orders"},
            "estimated_price": {"type": "number", "description": "The last quoted price you observed via get_equity_quotes for this symbol"},
            "current_shares_held": {"type": "number", "description": "Shares of this symbol you observed via get_equity_positions (0 if none)"},
            "rationale": {"type": "string", "description": "Concise reasoning citing the specific data that supports this trade"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": [
            "symbol", "action", "quantity", "order_type",
            "estimated_price", "current_shares_held", "rationale", "confidence",
        ],
        "additionalProperties": False,
    },
}

NO_ACTION_TOOL = {
    "name": "no_action",
    "description": "Call this when, after research, no trade is warranted right now for a given symbol.",
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["symbol", "rationale"],
        "additionalProperties": False,
    },
}

REPORT_SNAPSHOT_TOOL = {
    "name": "report_snapshot",
    "description": (
        "Report the facts you just looked up for one symbol. This is a "
        "pure data-reporting call - do not add opinions or recommendations."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "last_price": {"type": "number"},
            "shares_held": {"type": "number"},
            "cash_available": {"type": "number"},
        },
        "required": ["symbol", "last_price", "shares_held", "cash_available"],
        "additionalProperties": False,
    },
}


@dataclass
class SymbolSnapshot:
    symbol: str
    last_price: float
    shares_held: float
    cash_available: float


@dataclass
class TradeProposal:
    symbol: str
    action: str  # "buy" | "sell"
    quantity: float
    order_type: str  # "market" | "limit"
    estimated_price: float
    current_shares_held: float
    rationale: str
    confidence: str
    limit_price: float | None = None


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
