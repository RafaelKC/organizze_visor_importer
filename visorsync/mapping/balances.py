"""Cálculo de saldo retroativo na data `--since`.

Organizze só dá saldo atual (`get_balances`), não saldo histórico por data:

    saldo_na_data(conta, data) = saldo_atual(conta)
        - soma(transações da conta entre data e hoje, respeitando sinal)

Para cartões de crédito o "saldo" no Visor é a fatura em aberto atual, não o
saldo de uma conta corrente — retroagir isso depende de qual fatura estava
fechada/aberta na data de corte, então usamos `invoice_due_date` de cada
transação em vez de apenas somar valores por mês corrido.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionForBalance:
    account: str
    amount_cents: int  # positivo = entrada/crédito, negativo = saída/débito
    date: str  # "YYYY-MM-DD"
    invoice_due_date: str | None = None  # só para transações de cartão


def bank_balance_at_date(
    *, current_balance_cents: int, transactions: list[TransactionForBalance], since_date: str
) -> int:
    """Saldo de uma conta corrente/poupança na data `since_date` (inclusive)."""
    delta = sum(
        t.amount_cents for t in transactions if since_date <= t.date
    )
    return current_balance_cents - delta


def credit_card_balance_at_date(
    *,
    current_open_invoice_cents: int,
    transactions: list[TransactionForBalance],
    since_date: str,
) -> int:
    """Fatura em aberto de um cartão na data `since_date`.

    Reconstrói a partir de `invoice_due_date`: soma os lançamentos cuja
    fatura ainda estaria em aberto (vencimento após `since_date`) e ainda não
    apareceriam na fatura já paga naquele momento. Transações sem
    `invoice_due_date` são tratadas como pertencentes à fatura corrente.
    """
    # Lançamentos com vencimento de fatura estritamente após `since_date` ainda
    # não tinham entrado na fatura que estava aberta naquela data — subtrai-os
    # do valor atual para "voltar no tempo".
    future_invoice_amount = sum(
        t.amount_cents for t in transactions if (t.invoice_due_date or since_date) > since_date
    )
    return current_open_invoice_cents - future_invoice_amount
