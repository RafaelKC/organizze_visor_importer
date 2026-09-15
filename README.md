# visorsync

A CLI that migrates financial data from **Organizze** (read, source of truth)
to **Visor** (write, destination) by talking directly to both MCP servers —
no chat session in the loop. Built to run unattended on a homelab
(Oracle Cloud + Docker/n8n), idempotent and resumable.

## Why

Organizze → Visor migration was mostly done manually through a chat session
with two MCP connections. Structure (accounts, cards, categories, recurring
patterns) is already in place. What's left — the full day-to-day transaction
history and installment plans — hits token/call limits per turn in chat, so
it needs a standalone process that can run to completion, checkpoint its
progress, and be safely re-run.

## How it works

| | Organizze | Visor |
|---|---|---|
| URL | `https://mcp.organizze.com.br/mcp` | `https://mcp.visorfinance.app` |
| Transport | Streamable HTTP | Streamable HTTP, OAuth2 |
| Role | Read (source of truth) | Write (destination) |

Both servers require an interactive browser login on first use (standard
OAuth2). Tokens are cached under `$VISORSYNC_HOME` (default `~/.visorsync`)
and refreshed automatically — you shouldn't need to log in again unless a
token is revoked.

Every write to Visor is tracked in a local SQLite state store
(`state.db`), mapping `organizze_id <-> visor_id`. This is what makes the
tool:

- **Idempotent** — re-running never duplicates data; existing entities are
  detected and skipped or updated.
- **Resumable** — if a run dies partway through, re-running it picks up from
  the last checkpoint instead of starting over.
- **Safe to reset** — `reset-visor` only ever deletes what's recorded in
  `state.db` as created by this tool; anything created manually in the Visor
  app afterwards is never touched.

## No personal data lives in this repo

Account IDs, card limits, balances and recurring-pattern amounts (salary,
rent, etc.) are never hardcoded in code or committed to git:

- **Accounts/cards** are resolved by *name*, at runtime, against the live
  Visor account list (`get_accounts` -- Visor's `get_cards` is a different
  concept, named cardholders' physical card numbers, not credit-card
  accounts). `sync-structure` matches them and caches the resolved
  `organizze_name <-> visor_id` pair in the local, gitignored `state.db`.
  If an account/card doesn't exist yet in Visor -- never created, or
  removed by `reset-visor --include-manual` -- it's created via
  `create_manual_account` using Organizze's own billing days/limit for
  credit cards. There's no account-ID config file at all.
- **Recurring pattern amounts** are fetched live from Visor
  (`get_recurring_expenses`/`get_recurring_incomes`) every run, never
  stored.
- **Category name -> slug mapping** is the one thing that's a genuine
  personal *decision* rather than a secret (there's no way to auto-derive
  it), so it lives in a small local YAML file under
  `$VISORSYNC_HOME/config/categories.yaml`, seeded on first run from
  `visorsync/mapping/examples/categories.example.yaml`. It holds category
  names and slugs only -- no IDs, no money.

This means the repository itself is generic and shareable; only
`$VISORSYNC_HOME` (tokens, `state.db`, `config/categories.yaml`) is
personal, and it lives outside the repo entirely.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in MCP URLs / client IDs if needed
```

On first run, `visorsync sync-structure` seeds
`$VISORSYNC_HOME/config/categories.yaml` with placeholder examples --
edit it to match your own Organizze category names and desired Visor slugs
before relying on the category mapping.

## Usage

```bash
visorsync auth organizze          # interactive login, caches the token
visorsync auth visor               # interactive login, caches the token

visorsync sync-structure           # phase 1: accounts, cards, categories (idempotent)
visorsync sync-structure --dry-run

visorsync migrate --since 2026-07-01 --until 2026-12-31
visorsync migrate --since 2026-07-01 --dry-run   # show what would happen, write nothing
visorsync migrate                                # prompts for --since interactively

visorsync reset-visor --dry-run
visorsync reset-visor --confirm                  # wipes everything this tool created
visorsync reset-visor --confirm --only transactions

# Cleaning up a botched *manual* migration (done through the app/chat,
# before this tool tracked anything): lists accounts/cards, recurring
# patterns and custom categories that exist in Visor but aren't in
# state.db, and lets you pick exactly which ones to remove.
visorsync reset-visor --confirm --include-manual
visorsync reset-visor --confirm --include-manual --manual-since 2026-07-01 --manual-until 2026-09-14
```

Run `visorsync --help` or `visorsync COMMAND --help` for full option
documentation (in English) at any time.

**Note on `--include-manual`:** the normal reset only ever deletes rows
tracked in `state.db`, since that's the only reliable record of "this tool
created it." Entities created directly through the Visor app or a chat
session are invisible to that mechanism. `--include-manual` closes that gap
by listing everything of each type (accounts/cards, recurring patterns,
custom categories, and transactions within a date range) that exists in
Visor but isn't tracked, and asking you to pick which ones to delete --
one confirmed selection per group. It never deletes anything automatically
in bulk, precisely because "untracked" doesn't mean "safe to assume it's
ours."

**Note:** `--since` is always chosen explicitly — either as a flag or via an
interactive prompt. It is never hardcoded or silently defaulted, because it
drives both which transactions/installments get migrated and how the
retroactive account/card balances are computed.

## Project layout

```
visorsync/
├── cli.py                  # entrypoint (Typer)
├── auth/
│   ├── oauth_flow.py        # shared OAuth2 + PKCE + loopback callback + token cache
│   ├── organizze_auth.py
│   └── visor_auth.py
├── mcp_clients/
│   ├── base.py               # Streamable HTTP session, retry/backoff
│   ├── organizze_client.py
│   └── visor_client.py
├── state/
│   └── store.py              # SQLite: organizze_id <-> visor_id, content hash
├── mapping/
│   ├── accounts.py            # resolves Organizze <-> Visor accounts/cards by name
│   ├── categories.py          # loads the local category name -> slug config
│   ├── recurring.py           # matches existing Visor recurring patterns by name
│   ├── balances.py            # retroactive balance reconstruction
│   ├── installments.py        # fragmented installment-series reconstruction
│   ├── loader.py               # generic YAML loader/seeder for $VISORSYNC_HOME/config
│   └── examples/                # generic config templates (safe to commit)
└── commands/
    ├── sync_structure.py       # phase 1
    ├── reset_visor.py
    └── migrate.py              # phase 2
```

## The tricky part: installment reconstruction

Organizze's `find_installments` heuristic fragments the same purchase into
several groups when there are gaps in the queried window (e.g. a purchase's
occurrences split across "months 1-2" and "months 4-9" if month 3 falls
outside the query). `mapping/installments.py` groups occurrences by
`(description, account, amount ± a few cents, installments_total)`, merges
all months across groups, and:

- numbers the series with confidence when the merged months are complete and
  consecutive, or when the earliest merged month coincides with the earliest
  month Organizze has data for (so there's no possible earlier installment);
- otherwise leaves the series out and reports it in
  `ambiguous_installments.json` for manual review, rather than guessing.

Covered by `tests/test_installments.py`, including the real fragmented
"PC Nícolas" case (splits across May and Mar/Apr/Jun-Sep, reconstructs to
7 of 12).

## Testing

```bash
pip install pytest
pytest -v
```

## Known open items

- **Visor enforces a hard cap of 200 write operations per hour** ("You've
  reached the limit of 200 changes per hour through the assistant"),
  confirmed by running a real migration. This comes back as ordinary
  (non-error) text content rather than a tool error, so `mcp_clients/base.py`
  detects the message and raises `RateLimitError` immediately instead of
  burning through the retry budget. `migrate` catches it and stops cleanly:
  nothing that failed is written to `state.db`, so re-running the same
  command later resumes exactly where it left off once the quota resets.
  A full year of transaction history will need several separate runs
  spread across multiple hourly windows.
- Organizze accounts with no matching Visor account (by name) are created
  via `create_manual_account` if they don't exist yet; there's no config
  needed for accounts that already exist under the same name.
- `known_miscategorized_patterns` in `categories.yaml` exists because
  `update_recurring_pattern` has been observed to intermittently reject
  `confirmed: true`; this is handled with retry/backoff, falling back to a
  manual-review log entry if it keeps failing.
- The retroactive-balance sign convention for credit cards (whether Visor's
  `balance` field wants the amount owed as positive or negative) was set
  based on how it's normally displayed, not confirmed against a real
  populated account -- check a card's balance in the app after the first
  `migrate` run.
