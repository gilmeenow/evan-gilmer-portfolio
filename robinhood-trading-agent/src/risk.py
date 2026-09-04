"""Deterministic, code-enforced risk checks.

Nothing here is a suggestion to the model - a TradeProposal that fails any
check is rejected outright, regardless of the model's stated confidence or
rationale. This module has no dependency on the Anthropic SDK on purpose:
it must be testable (and auditable) without ever calling an LLM.
"""
from __future__ import annotations

from .config import TradingConfig
from .schemas import RiskVerdict, SymbolSnapshot, TradeProposal

# How far the model's self-reported price/position figures may drift from
# the independently-fetched snapshot before we treat it as a possible
# hallucination and reject the trade outright.
MAX_PRICE_DRIFT_PCT = 0.02  # 2%


def validate(
    proposal: TradeProposal,
    snapshot: SymbolSnapshot,
    config: TradingConfig,
    trades_today: int,
) -> RiskVerdict:
    if proposal.symbol != snapshot.symbol:
        return RiskVerdict(False, "proposal/snapshot symbol mismatch")

    if proposal.symbol not in config.watchlist:
        return RiskVerdict(False, f"{proposal.symbol} is not in the configured watchlist")

    if proposal.action not in ("buy", "sell"):
        return RiskVerdict(False, f"unsupported action '{proposal.action}'")

    if proposal.quantity <= 0:
        return RiskVerdict(False, "quantity must be positive")

    if proposal.order_type == "limit" and proposal.limit_price is None:
        return RiskVerdict(False, "limit order missing limit_price")

    if trades_today >= config.max_trades_per_day:
        return RiskVerdict(False, f"daily trade cap reached ({config.max_trades_per_day})")

    # Cross-check the model's self-reported price/holdings against the
    # independently-fetched snapshot. A large mismatch means the research
    # call and the verification call disagree about basic facts - treat
    # that as unsafe to act on rather than trusting either one.
    if snapshot.last_price > 0:
        drift = abs(proposal.estimated_price - snapshot.last_price) / snapshot.last_price
        if drift > MAX_PRICE_DRIFT_PCT:
            return RiskVerdict(
                False,
                f"price drift {drift:.1%} between proposal (${proposal.estimated_price}) "
                f"and verified snapshot (${snapshot.last_price}) exceeds {MAX_PRICE_DRIFT_PCT:.0%}",
            )

    if proposal.current_shares_held != snapshot.shares_held:
        return RiskVerdict(
            False,
            f"reported shares held ({proposal.current_shares_held}) does not match "
            f"verified position ({snapshot.shares_held})",
        )

    price = proposal.limit_price or snapshot.last_price
    estimated_cost = proposal.quantity * price

    if proposal.action == "buy":
        if estimated_cost > config.max_position_usd:
            return RiskVerdict(
                False,
                f"estimated cost ${estimated_cost:.2f} exceeds max position size "
                f"${config.max_position_usd:.2f}",
            )
        if estimated_cost > snapshot.cash_available:
            return RiskVerdict(
                False,
                f"estimated cost ${estimated_cost:.2f} exceeds available cash "
                f"${snapshot.cash_available:.2f}",
            )

    if proposal.action == "sell":
        if not config.allow_short_selling and proposal.quantity > snapshot.shares_held:
            return RiskVerdict(
                False,
                f"sell quantity {proposal.quantity} exceeds shares held "
                f"{snapshot.shares_held} and short selling is disabled",
            )

    return RiskVerdict(True, "passed all risk checks")
