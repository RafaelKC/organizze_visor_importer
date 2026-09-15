"""Resolves recurring patterns already created manually in Visor, by name.

No amounts are hardcoded here -- salary, rent, subscription prices, etc.
are personal financial data and are fetched live from Visor
(`get_recurring_expenses` / `get_recurring_incomes`) every run instead of
being stored anywhere. `sync_structure`/`migrate` use this to recognize
that a pattern already exists (by name) and skip recreating it -- never to
create one from scratch.
"""
from __future__ import annotations


def index_by_name(expenses: list[dict], incomes: list[dict]) -> dict[str, dict]:
    return {p["name"]: p for p in [*expenses, *incomes] if isinstance(p, dict) and "name" in p}
