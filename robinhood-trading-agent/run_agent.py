#!/usr/bin/env python3
"""CLI entrypoint for the automated Robinhood trading agent.

Usage:
    python run_agent.py                 # dry run against the default watchlist
    python run_agent.py --config my.json
    python run_agent.py --live          # allow real orders (still asks per-trade)
    python run_agent.py --live --yes    # allow real orders, no interactive prompt

Dry run is the default in every path through this script. --live only
*permits* real orders; each individual order still needs its own risk pass
and (unless --yes) an explicit "y" at the confirmation prompt.
"""
from __future__ import annotations

import argparse
import sys

from src.config import TradingConfig
from src.logging_utils import log_event
from src.mcp_client import build_client, execute_trade, fetch_symbol_snapshot, research_and_decide
from src.risk import validate
from src.schemas import TradeProposal
from src.state import get_trades_today, record_trade


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="Path to a JSON file overriding config defaults")
    parser.add_argument("--live", action="store_true", help="Allow risk-approved orders to actually be placed")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation before each live order")
    return parser.parse_args()


def confirm(proposal: TradeProposal) -> bool:
    prompt = (
        f"\nPlace order? {proposal.action.upper()} {proposal.quantity} {proposal.symbol} "
        f"({proposal.order_type}"
        + (f" @ ${proposal.limit_price}" if proposal.limit_price else "")
        + f") - rationale: {proposal.rationale}\n[y/N]: "
    )
    return input(prompt).strip().lower() == "y"


def main() -> int:
    args = parse_args()
    config = TradingConfig.load(args.config)
    live = args.live

    client = build_client()

    print(f"Researching watchlist: {', '.join(config.watchlist)}")
    proposals = research_and_decide(client, config)
    log_event(config.log_path, "research_complete", {"proposal_count": len(proposals)})

    for item in proposals:
        if isinstance(item, dict):
            print(f"  {item['symbol']}: no action - {item['rationale']}")
            log_event(config.log_path, "no_action", item)
            continue

        proposal: TradeProposal = item
        try:
            snapshot = fetch_symbol_snapshot(client, config, proposal.symbol)
        except RuntimeError as exc:
            print(f"  {proposal.symbol}: could not verify snapshot ({exc}), rejecting proposal")
            log_event(config.log_path, "proposal_rejected", {
                "symbol": proposal.symbol, "reason": f"snapshot fetch failed: {exc}",
            })
            continue

        trades_today = get_trades_today(config.trade_count_path)
        verdict = validate(proposal, snapshot, config, trades_today)
        log_event(config.log_path, "proposal_reviewed", {
            "proposal": vars(proposal), "snapshot": vars(snapshot),
            "approved": verdict.approved, "reason": verdict.reason,
        })

        if not verdict.approved:
            print(f"  {proposal.symbol}: REJECTED - {verdict.reason}")
            continue

        print(f"  {proposal.symbol}: approved - {proposal.action} {proposal.quantity} shares ({verdict.reason})")

        if not live:
            print("    (dry run - no order placed; pass --live to enable real execution)")
            continue

        if not args.yes and not confirm(proposal):
            print("    skipped by operator")
            log_event(config.log_path, "order_skipped_by_operator", {"symbol": proposal.symbol})
            continue

        result = execute_trade(client, config, proposal)
        record_trade(config.trade_count_path)
        log_event(config.log_path, "order_executed", {"symbol": proposal.symbol, "raw_response": result})
        print(f"    order submitted for {proposal.symbol}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
