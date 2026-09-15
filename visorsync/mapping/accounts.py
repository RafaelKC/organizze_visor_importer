"""Resolves Organizze accounts/cards to existing Visor accounts.

No personal account IDs, credit limits or balances are hardcoded here, in
code or in any config file. `sync_structure` fetches the live account list
from both MCP servers every run, matches them by name, and caches the
resolved `organizze_name <-> visor_id` pair in state.db -- which is local
and already gitignored. This module only knows *how to match*, never *what
the values are*.

Note: Visor's `get_cards()` tool is about named cardholders' physical card
numbers (for "how much did person X spend"), not credit-card accounts --
a credit card is just a regular account from `get_accounts()` with
`type: "CREDIT"`. Only `get_accounts()` is used here.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from visorsync.mapping.loader import ensure_config_dir, load_yaml


def _normalize(name: str) -> str:
    # NFKC first: Organizze and Visor can encode the same accented text with
    # different Unicode normal forms (e.g. precomposed "ã" vs "a" + combining
    # tilde) -- those look identical printed but compare unequal otherwise,
    # which silently failed every match on a name containing "Cartão".
    return unicodedata.normalize("NFKC", name).strip().casefold()


@dataclass(frozen=True)
class ResolvedAccount:
    organizze_name: str
    kind: str  # "bank" | "credit"
    visor_id: str
    visor_name: str
    closing_day: Optional[int] = None
    due_day: Optional[int] = None
    limit_cents: Optional[int] = None


def resolve_accounts(
    organizze_account_names: list[str],
    organizze_card_names: list[str],
    visor_accounts: list[dict],
    name_overrides: Optional[dict[str, str]] = None,
) -> tuple[list[ResolvedAccount], list[str]]:
    """Match each Organizze account/card to a Visor account by name.

    `name_overrides` optionally maps an Organizze name to the Visor name to
    match against, for cases where the same account was named differently
    in each app (e.g. "Conta Inter" in Organizze vs. "Inter" in Visor).
    This holds only text labels -- no IDs or amounts -- so it's safe to keep
    as a small local override file if it's ever needed; most setups won't.

    Returns (resolved, unresolved_names). Unresolved names are reported by
    the caller, which decides whether to create them or just warn.
    """
    # Normalize override keys too, so a whitespace/case difference in the
    # yaml doesn't have to exactly match the raw Organizze name byte-for-byte.
    overrides = {_normalize(k): v for k, v in (name_overrides or {}).items()}
    visor_by_name: dict[str, dict] = {_normalize(a["name"]): a for a in visor_accounts}

    resolved: list[ResolvedAccount] = []
    unresolved: list[str] = []

    for name in organizze_account_names:
        target = overrides.get(_normalize(name), name)
        match = visor_by_name.get(_normalize(target))
        if match is None:
            unresolved.append(name)
            continue
        resolved.append(
            ResolvedAccount(
                organizze_name=name, kind="bank", visor_id=match["id"], visor_name=match["name"]
            )
        )

    for name in organizze_card_names:
        target = overrides.get(_normalize(name), name)
        match = visor_by_name.get(_normalize(target))
        if match is None:
            unresolved.append(name)
            continue
        resolved.append(
            ResolvedAccount(
                organizze_name=name,
                kind="credit",
                visor_id=match["id"],
                visor_name=match["name"],
                closing_day=match.get("billing_cycle_close_day", match.get("closing_day")),
                due_day=match.get("billing_cycle_due_day", match.get("due_day")),
                limit_cents=match.get("credit_limit", match.get("limit_cents")),
            )
        )

    return resolved, unresolved


def load_name_overrides(config_dir: Path) -> dict[str, str]:
    """Loads the optional Organizze-name -> Visor-name override table.

    Only needed when the same account/card was named differently in each
    app. Seeded on first run with a placeholder example; an empty/default
    file means no overrides, which is the common case.
    """
    ensure_config_dir(config_dir)
    data = load_yaml(config_dir, "account_name_overrides.yaml")
    return dict(data.get("overrides", {}))
