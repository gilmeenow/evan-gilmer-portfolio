"""All interaction with Claude + the remote Robinhood MCP server.

Three separate, narrowly-scoped calls make up one agent cycle, and each
gets its own toolset - none of them can do more than its name says:

  1. fetch_symbol_snapshot  - read-only, reports facts, recommends nothing.
  2. research_and_decide    - read-only research, may *propose* trades but
                               can never place one.
  3. execute_trade          - can place exactly one already risk-approved
                               order; has no research tools at all.

This separation means the only call in the whole system that can ever move
money is also the one that cannot browse, research, or "decide" anything -
it is handed already-validated numbers and told to place that exact order.

The MCP connector executes remote tool calls server-side (Anthropic talks
to Robinhood's MCP server directly), so this code never sees a raw
place_equity_order payload in flight - only the resolved content blocks in
Claude's response. See: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

import anthropic

from .config import TradingConfig
from .schemas import (
    EXECUTION_MCP_TOOLS,
    NO_ACTION_TOOL,
    PROPOSE_TRADE_TOOL,
    REPORT_SNAPSHOT_TOOL,
    RESEARCH_MCP_TOOLS,
    SNAPSHOT_MCP_TOOLS,
    SymbolSnapshot,
    TradeProposal,
)

MCP_BETA = "mcp-client-2025-11-20"
MAX_TOKENS = 4096


def build_client() -> anthropic.Anthropic:
    """Uses the standard Anthropic credential resolution (ANTHROPIC_API_KEY,
    ANTHROPIC_AUTH_TOKEN, or a logged-in `ant auth login` profile)."""
    return anthropic.Anthropic()


def _mcp_server(config: TradingConfig) -> dict[str, Any]:
    server: dict[str, Any] = {
        "type": "url",
        "url": config.mcp_server_url,
        "name": config.mcp_server_name,
    }
    token = os.environ.get("ROBINHOOD_MCP_TOKEN")
    if token:
        server["authorization_token"] = token
    return server


def _mcp_toolset(config: TradingConfig, allowed_tools: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "mcp_toolset",
        "mcp_server_name": config.mcp_server_name,
        "default_config": {"enabled": False},
        "configs": {name: {"enabled": True} for name in allowed_tools},
    }


def _run_agentic_turn(
    client: anthropic.Anthropic,
    config: TradingConfig,
    system: str,
    user_content: str,
    allowed_mcp_tools: tuple[str, ...],
    extra_tools: list[dict[str, Any]],
    local_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    max_iterations: int = 12,
) -> anthropic.types.beta.BetaMessage:
    """Manual agentic loop. We own it end-to-end (rather than the SDK's tool
    runner) because this is exactly the kind of control flow where silent
    surprises are unacceptable: every local tool call is logged by its
    handler as a side effect, and we bound the number of turns so a
    confused model can't loop indefinitely and rack up cost.
    """
    tools = [_mcp_toolset(config, allowed_mcp_tools), *extra_tools]
    mcp_servers = [_mcp_server(config)]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

    response = None
    for _ in range(max_iterations):
        response = client.beta.messages.create(
            model=config.model,
            max_tokens=MAX_TOKENS,
            betas=[MCP_BETA],
            system=system,
            mcp_servers=mcp_servers,
            tools=tools,
            messages=messages,
            output_config={"effort": "high"},
        )

        if response.stop_reason == "pause_turn":
            # Long server-side (MCP) tool chain - the API resumes
            # automatically once we send the assistant turn back.
            messages.append({"role": "assistant", "content": response.content})
            continue

        local_tool_uses = [
            block for block in response.content
            if getattr(block, "type", None) == "tool_use" and block.name in local_handlers
        ]

        if response.stop_reason != "tool_use" or not local_tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in local_tool_uses:
            result = local_handlers[block.name](block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })
        messages.append({"role": "user", "content": tool_results})

    return response


def fetch_symbol_snapshot(client: anthropic.Anthropic, config: TradingConfig, symbol: str) -> SymbolSnapshot:
    """Independent, deterministic-intent fact check: price, position, cash.
    Used both to ground the research call and, later, to cross-check its
    proposals in src/risk.py - a proposal whose self-reported numbers don't
    match this snapshot is rejected outright."""
    captured: list[SymbolSnapshot] = []

    def handle_report_snapshot(input_: dict[str, Any]) -> dict[str, Any]:
        captured.append(SymbolSnapshot(
            symbol=input_["symbol"],
            last_price=float(input_["last_price"]),
            shares_held=float(input_["shares_held"]),
            cash_available=float(input_["cash_available"]),
        ))
        return {"status": "recorded"}

    system = (
        "You are a data-reporting assistant with read-only access to a brokerage "
        "account via tools. You never recommend trades. Use get_equity_quotes to "
        f"find the last traded price of {symbol}, get_equity_positions to find how "
        f"many shares of {symbol} are currently held (0 if none), and get_accounts "
        "to find total cash available for trading. Then call report_snapshot exactly "
        "once with those three numbers. Do not call any other tool afterward."
    )

    _run_agentic_turn(
        client, config, system,
        user_content=f"Report a snapshot for {symbol}.",
        allowed_mcp_tools=SNAPSHOT_MCP_TOOLS,
        extra_tools=[REPORT_SNAPSHOT_TOOL],
        local_handlers={"report_snapshot": handle_report_snapshot},
    )

    if not captured:
        raise RuntimeError(f"agent never reported a snapshot for {symbol}")
    return captured[-1]


def research_and_decide(
    client: anthropic.Anthropic,
    config: TradingConfig,
) -> list[TradeProposal | dict[str, str]]:
    """One research pass over the whole watchlist. Returns a mix of
    TradeProposal objects and {"symbol", "rationale"} no-action records -
    every symbol in the watchlist should produce exactly one of either."""
    proposals: list[TradeProposal | dict[str, str]] = []

    def handle_propose_trade(input_: dict[str, Any]) -> dict[str, Any]:
        proposals.append(TradeProposal(
            symbol=input_["symbol"],
            action=input_["action"],
            quantity=float(input_["quantity"]),
            order_type=input_["order_type"],
            estimated_price=float(input_["estimated_price"]),
            current_shares_held=float(input_["current_shares_held"]),
            rationale=input_["rationale"],
            confidence=input_["confidence"],
            limit_price=float(input_["limit_price"]) if input_.get("limit_price") is not None else None,
        ))
        return {"status": "recorded for risk review"}

    def handle_no_action(input_: dict[str, Any]) -> dict[str, Any]:
        proposals.append({"symbol": input_["symbol"], "rationale": input_["rationale"]})
        return {"status": "recorded"}

    system = (
        "You are an equity research assistant driving an automated trading agent. "
        "Hard rules, enforced independently in code after you respond - breaking "
        "them just means your proposal gets rejected:\n"
        f"- You may only consider these symbols: {', '.join(config.watchlist)}.\n"
        "- Long equity positions only - never short, never options, never crypto, "
        "never margin.\n"
        f"- A single buy must cost no more than ${config.max_position_usd:.2f} "
        "at the price you observed.\n"
        "- A sell can never exceed the shares currently held.\n"
        "- Use get_equity_quotes, get_equity_fundamentals, get_equity_historicals, "
        "get_equity_technical_indicators, and get_equity_news as needed to research "
        "each symbol. Use get_equity_positions and get_accounts to check current "
        "holdings and cash before proposing a buy or sell.\n"
        "- For every symbol in the watchlist, call exactly one of propose_trade or "
        "no_action. Ground estimated_price and current_shares_held in the tool "
        "results you just fetched, not assumptions.\n"
        "- Prefer no_action when evidence is mixed or thin - a missed trade costs "
        "nothing; a bad one costs real money."
    )
    user_content = (
        f"Research these symbols and decide on each: {', '.join(config.watchlist)}."
    )

    _run_agentic_turn(
        client, config, system, user_content,
        allowed_mcp_tools=RESEARCH_MCP_TOOLS,
        extra_tools=[PROPOSE_TRADE_TOOL, NO_ACTION_TOOL],
        local_handlers={"propose_trade": handle_propose_trade, "no_action": handle_no_action},
        max_iterations=20,
    )
    return proposals


def execute_trade(client: anthropic.Anthropic, config: TradingConfig, proposal: TradeProposal) -> str:
    """Places exactly one already risk-approved order. This call has no
    research tools and no ability to reconsider the trade - it either
    places the specified order or reports that it couldn't."""
    system = (
        "You are an order execution assistant. Place exactly one order using "
        "place_equity_order with the parameters given in the user message, then "
        "reply with a one-sentence confirmation. Do not place any other order, "
        "do not modify the parameters, and do not call any tool more than once."
    )
    order_desc = {
        "symbol": proposal.symbol,
        "action": proposal.action,
        "quantity": proposal.quantity,
        "order_type": proposal.order_type,
        "limit_price": proposal.limit_price,
    }
    user_content = f"Place this order: {json.dumps(order_desc)}"

    response = _run_agentic_turn(
        client, config, system, user_content,
        allowed_mcp_tools=EXECUTION_MCP_TOOLS,
        extra_tools=[],
        local_handlers={},
        max_iterations=4,
    )
    # Best-effort human-readable transcript of the whole response for the
    # audit log - block shapes for MCP tool results are intentionally not
    # over-parsed here so logging never breaks on an SDK version bump.
    return json.dumps([block.model_dump() for block in response.content], default=str)
