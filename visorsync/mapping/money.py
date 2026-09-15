"""Shared parser for Organizze's formatted BRL money strings.

Both `get_balances`/`list_transactions`/`list_credit_cards` (Organizze) return
amounts as formatted strings like "R$ 12.200,00" or "R$ -964,17", not bare
numbers -- confirmed by running against the real servers, not assumed.
"""
from __future__ import annotations

import re


def parse_brl(value: object) -> float:
    """"R$ 12.200,00" -> 12200.0, "R$ -964,17" -> -964.17.

    Handles an already-numeric value too, in case a field is a plain number
    instead of a formatted string.
    """
    if isinstance(value, (int, float)):
        return float(value)
    digits = re.sub(r"[^\d,-]", "", str(value)).replace(".", "").replace(",", ".")
    return float(digits) if digits not in ("", "-") else 0.0
