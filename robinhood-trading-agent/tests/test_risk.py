"""Unit tests for the deterministic risk checks. These never touch the
network or the Anthropic SDK - src/risk.py is pure Python by design so it
can be tested (and trusted) in isolation from the LLM."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import TradingConfig
from src.risk import validate
from src.schemas import SymbolSnapshot, TradeProposal

CONFIG = TradingConfig(watchlist=("AAPL",), max_position_usd=250.0, max_trades_per_day=3)


def make_proposal(**overrides) -> TradeProposal:
    defaults = dict(
        symbol="AAPL", action="buy", quantity=1, order_type="market",
        estimated_price=200.0, current_shares_held=0, rationale="test", confidence="high",
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def make_snapshot(**overrides) -> SymbolSnapshot:
    defaults = dict(symbol="AAPL", last_price=200.0, shares_held=0, cash_available=1000.0)
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


def test_valid_buy_is_approved():
    verdict = validate(make_proposal(), make_snapshot(), CONFIG, trades_today=0)
    assert verdict.approved, verdict.reason


def test_symbol_outside_watchlist_rejected():
    verdict = validate(make_proposal(symbol="TSLA"), make_snapshot(symbol="TSLA"), CONFIG, trades_today=0)
    assert not verdict.approved


def test_buy_over_position_cap_rejected():
    verdict = validate(make_proposal(quantity=10, estimated_price=200.0), make_snapshot(), CONFIG, trades_today=0)
    assert not verdict.approved
    assert "max position size" in verdict.reason


def test_buy_over_cash_available_rejected():
    verdict = validate(make_proposal(quantity=1, estimated_price=200.0), make_snapshot(cash_available=50.0), CONFIG, trades_today=0)
    assert not verdict.approved
    assert "available cash" in verdict.reason


def test_daily_trade_cap_enforced():
    verdict = validate(make_proposal(), make_snapshot(), CONFIG, trades_today=3)
    assert not verdict.approved
    assert "daily trade cap" in verdict.reason


def test_price_drift_rejected():
    proposal = make_proposal(estimated_price=200.0)
    snapshot = make_snapshot(last_price=250.0)  # >2% drift
    verdict = validate(proposal, snapshot, CONFIG, trades_today=0)
    assert not verdict.approved
    assert "price drift" in verdict.reason


def test_shares_held_mismatch_rejected():
    proposal = make_proposal(current_shares_held=5)
    snapshot = make_snapshot(shares_held=0)
    verdict = validate(proposal, snapshot, CONFIG, trades_today=0)
    assert not verdict.approved
    assert "does not match" in verdict.reason


def test_sell_more_than_held_rejected():
    proposal = make_proposal(action="sell", quantity=5, current_shares_held=2)
    snapshot = make_snapshot(shares_held=2)
    verdict = validate(proposal, snapshot, CONFIG, trades_today=0)
    assert not verdict.approved
    assert "shares held" in verdict.reason


def test_sell_within_held_approved():
    proposal = make_proposal(action="sell", quantity=2, current_shares_held=2)
    snapshot = make_snapshot(shares_held=2)
    verdict = validate(proposal, snapshot, CONFIG, trades_today=0)
    assert verdict.approved, verdict.reason


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
