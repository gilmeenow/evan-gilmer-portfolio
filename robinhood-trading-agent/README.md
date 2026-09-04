# Robinhood Trading Agent (portfolio project)

An LLM-driven equity trading agent built on Claude and Robinhood's remote
trading MCP server (`https://agent.robinhood.com/mcp/trading`). This is a
**portfolio/showcase project**, not a production trading system: it
defaults to dry-run (research + recommendations only), trades equities
only in small, fixed position sizes, and treats every model output as
untrusted until it clears a deterministic, code-enforced risk check.

## Why it's built this way

Letting an LLM research the market is low-risk. Letting an LLM directly
place orders on a live brokerage account is not. This project's structure
is the answer to that gap - it is not one agent with tools, it's three
narrowly-scoped calls that only combine into a trade through code, never
through model judgment alone:

| Call | Tools it can use | What it can do |
|---|---|---|
| `fetch_symbol_snapshot` | `get_accounts`, `get_equity_positions`, `get_equity_quotes` | Report price/position/cash facts. Cannot recommend or trade. |
| `research_and_decide` | quotes, fundamentals, historicals, technicals, news, positions, accounts | Research and *propose* a trade. Cannot place one - `place_equity_order` is never in this call's toolset. |
| `execute_trade` | `place_equity_order` only | Place exactly one order whose parameters were already risk-approved. Has no research tools, so it cannot second-guess or resize the trade. |

Between the second and third call sits `src/risk.py` - plain, dependency-free
Python that rejects a proposal outright if it:

- names a symbol outside the configured watchlist,
- would cost more than the configured max position size,
- would exceed available cash,
- would sell more shares than are actually held (no shorting),
- disagrees with the independently-fetched snapshot on price or share count
  by more than 2% (a cheap check against a hallucinated or stale number), or
- would exceed the day's trade cap.

Every proposal, verdict, and order is appended to `.state/decisions.jsonl`
so the full reasoning trail is auditable after the fact.

**This still isn't a substitute for judgment.** Read `src/risk.py` and
`src/mcp_client.py` before pointing this at a real account, and start with
`--live` off.

## Setup

```bash
cd robinhood-trading-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the values below
```

### Credentials

This project talks to Claude and to Robinhood's MCP server directly from a
standalone script, via the [Anthropic API's MCP
connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector) -
that's a different connection path from an interactive Claude Code
session, so it needs its own credentials:

- **Anthropic**: set `ANTHROPIC_API_KEY`, or run `ant auth login` once and
  leave it unset.
- **Robinhood MCP server**: set `ROBINHOOD_MCP_TOKEN` to an OAuth bearer
  token for `https://agent.robinhood.com/mcp/trading`, obtained per
  Robinhood's agent-trading platform documentation.

If you only want to explore the Robinhood tools interactively (no
standalone script, no separate token to manage), Claude Code has its own,
simpler path - run these in an interactive terminal session:

```
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
/mcp
```

then select `robinhood-trading` and follow the authentication prompt.
That flow authorizes *that Claude Code session* and is a good way to poke
at the available tools by hand; it does not produce a token this script
can reuse, which is why the two paths are documented separately.

## Running it

```bash
python run_agent.py                  # dry run: research + recommendations only
python run_agent.py --config my.json # override watchlist / limits, see below
python run_agent.py --live           # allow real orders (confirms each one)
python run_agent.py --live --yes     # allow real orders, no interactive prompt
```

Dry run is always the default - `--live` only *permits* execution; every
individual trade still has to clear the risk check and, unless `--yes` is
also passed, an explicit confirmation prompt.

### Config overrides

Pass `--config path/to/file.json` with any subset of these fields (see
`src/config.py` for the full list and defaults):

```json
{
  "watchlist": ["AAPL", "MSFT"],
  "max_position_usd": 100.0,
  "max_trades_per_day": 1
}
```

### Scheduling

This script makes one pass over the watchlist and exits - point cron (or
any scheduler) at it for recurring runs, e.g. hourly during market hours:

```
0 9-15 * * 1-5 cd /path/to/robinhood-trading-agent && .venv/bin/python run_agent.py --live --yes >> cron.log 2>&1
```

`src/state.py` tracks the daily trade count in `.state/trade_count.json`
so the cap in `max_trades_per_day` holds across separate cron invocations.

## Tests

`src/risk.py` has no dependency on the Anthropic SDK or the network - it's
tested in isolation:

```bash
python -m pytest tests/  # or: python tests/test_risk.py
```

## Project layout

```
run_agent.py         CLI entrypoint - orchestrates one full agent cycle
src/config.py         TradingConfig: watchlist, position/day limits, model
src/schemas.py        Tool schemas + TradeProposal/SymbolSnapshot/RiskVerdict
src/mcp_client.py      The three Claude + MCP connector calls
src/risk.py           Deterministic, code-enforced trade validation
src/state.py           Daily trade-count tracking across runs
src/logging_utils.py  Append-only JSONL audit log
tests/test_risk.py    Risk-check unit tests
```
