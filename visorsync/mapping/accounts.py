"""Resolves Organizze accounts/cards to existing Visor accounts/cards.

No personal account IDs, credit limits or balances are hardcoded here, in
code or in any config file. `sync_structure` fetches the live account/card
lists from both MCP servers every run, matches them by name, and caches the
resolved `organizze_name <-> visor_id` pair in state.db -- which is local
and already gitignored. This module only knows *how to match*, never *what
the values are*.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _normalize(name: str) -> str:
    return name.strip().casefold()


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
    visor_cards: list[dict],
    name_overrides: Optional[dict[str, str]] = None,
) -> tuple[list[ResolvedAccount], list[str]]:
    """Match each Organizze account/card to a Visor account/card by name.

    `name_overrides` optionally maps an Organizze name to the Visor name to
    match against, for cases where the same account was named differently
    in each app (e.g. "Conta Inter" in Organizze vs. "Inter" in Visor).
    This holds only text labels -- no IDs or amounts -- so it's safe to keep
    as a small local override file if it's ever needed; most setups won't.

    Returns (resolved, unresolved_names). Unresolved accounts/cards are
    reported by the caller (never auto-created or silently skipped), since
    accounts/cards are expected to already exist in Visor before this runs.
    """
    overrides = name_overrides or {}
    visor_by_name: dict[str, dict] = {_normalize(a["name"]): a for a in visor_accounts}
    visor_by_name.update({_normalize(c["name"]): c for c in visor_cards})

    resolved: list[ResolvedAccount] = []
    unresolved: list[str] = []

    for name in organizze_account_names:
        match = visor_by_name.get(_normalize(overrides.get(name, name)))
        if match is None:
            unresolved.append(name)
            continue
        resolved.append(
            ResolvedAccount(
                organizze_name=name, kind="bank", visor_id=match["id"], visor_name=match["name"]
            )
        )

    for name in organizze_card_names:
        match = visor_by_name.get(_normalize(overrides.get(name, name)))
        if match is None:
            unresolved.append(name)
            continue
        resolved.append(
            ResolvedAccount(
                organizze_name=name,
                kind="credit",
                visor_id=match["id"],
                visor_name=match["name"],
                closing_day=match.get("closing_day"),
                due_day=match.get("due_day"),
                limit_cents=match.get("limit_cents"),
            )
        )

    return resolved, unresolved
