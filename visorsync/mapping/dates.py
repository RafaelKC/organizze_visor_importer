"""Normalizes Organizze's date strings to YYYY-MM-DD.

Organizze's list_transactions (and likely other transaction-shaped
responses) return dates as "DD/MM/YYYY" -- confirmed by running against
the real server, where create_manual_transaction rejected every date with
"Data inválida. Use YYYY-MM-DD ou ISO 8601." Also matters internally:
mapping/balances.py compares dates as plain strings (`since_date <= t.date`),
which only sorts correctly in YYYY-MM-DD form.
"""
from __future__ import annotations

import re

_BR_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def to_iso_date(value: str | None) -> str | None:
    if not value:
        return value
    match = _BR_DATE.match(value.strip())
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return value  # already ISO (or something else -- leave it, don't guess further
