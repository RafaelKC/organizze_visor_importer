"""Reconstrução de séries de parcelamento a partir do `find_installments` do
Organizze.

O Organizze não expõe "isto é a parcela N de M" de forma limpa em cartões
conectados: a tool usa heurística (mesma descrição + valor repetido) e
fragmenta a mesma compra em várias entradas quando há gaps no período
consultado. Este módulo junta os fragmentos e decide, com o máximo de
certeza possível, qual mês corresponde a qual número de parcela — sem
nunca inventar um início que não pode ser confirmado (esses casos vão pro
relatório de ambiguidade para revisão manual).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawInstallmentOccurrence:
    """Uma linha crua devolvida por `find_installments`."""

    description: str
    account: str
    amount_cents: int
    installments_total: int
    month: str  # "YYYY-MM"


@dataclass(frozen=True)
class ResolvedInstallmentSeries:
    description: str
    account: str
    amount_cents: int
    installments_total: int
    first_month: str  # "YYYY-MM" da parcela 1
    months_seen: tuple[str, ...]

    def installment_number_for(self, month: str) -> int | None:
        """Número da parcela (1-based) para um mês dentro da série, ou None se fora dela."""
        start = _month_to_index(self.first_month)
        target = _month_to_index(month)
        n = target - start + 1
        if 1 <= n <= self.installments_total:
            return n
        return None

    def last_month(self) -> str:
        return _index_to_month(_month_to_index(self.first_month) + self.installments_total - 1)


@dataclass(frozen=True)
class AmbiguousInstallment:
    description: str
    account: str
    amount_cents: int
    installments_total: int
    months_seen: tuple[str, ...]
    reason: str


def _month_to_index(month: str) -> int:
    y, m = (int(part) for part in month.split("-"))
    return y * 12 + (m - 1)


def _index_to_month(index: int) -> str:
    y, m = divmod(index, 12)
    return f"{y:04d}-{m + 1:02d}"


def _normalize_description(description: str) -> str:
    return " ".join(description.strip().casefold().split())


AMOUNT_BUCKET_CENTS = 10  # agrupa valores que diferem só por poucos centavos


def _group_key(occ: RawInstallmentOccurrence) -> tuple[str, str, int, int]:
    amount_bucket = round(occ.amount_cents / AMOUNT_BUCKET_CENTS)
    return (
        _normalize_description(occ.description),
        occ.account,
        amount_bucket,
        occ.installments_total,
    )


def _consecutive(months: list[str]) -> bool:
    indices = sorted(_month_to_index(m) for m in months)
    return all(b - a == 1 for a, b in zip(indices, indices[1:]))


def resolve_installment_series(
    occurrences: list[RawInstallmentOccurrence],
    *,
    earliest_available_month: str,
) -> tuple[list[ResolvedInstallmentSeries], list[AmbiguousInstallment]]:
    """Agrupa fragmentos de `find_installments` em séries resolvidas.

    `earliest_available_month` é o mês mais antigo com transações disponíveis
    na janela consultada ao Organizze — usado para inferir que uma série
    incompleta começa mesmo assim na primeira ocorrência vista, quando não há
    como ter havido parcela anterior a essa data.
    """
    groups: dict[tuple[str, str, int, int], list[RawInstallmentOccurrence]] = {}
    for occ in occurrences:
        groups.setdefault(_group_key(occ), []).append(occ)

    resolved: list[ResolvedInstallmentSeries] = []
    ambiguous: list[AmbiguousInstallment] = []

    for (_, account, _, total), items in groups.items():
        description = items[0].description
        amount_cents = _representative_amount(items)
        months = sorted({item.month for item in items}, key=_month_to_index)

        if not _consecutive(months):
            ambiguous.append(
                AmbiguousInstallment(
                    description=description,
                    account=account,
                    amount_cents=amount_cents,
                    installments_total=total,
                    months_seen=tuple(months),
                    reason="meses não consecutivos após unir todos os fragmentos",
                )
            )
            continue

        if len(months) == total:
            resolved.append(
                ResolvedInstallmentSeries(
                    description=description,
                    account=account,
                    amount_cents=amount_cents,
                    installments_total=total,
                    first_month=months[0],
                    months_seen=tuple(months),
                )
            )
            continue

        # série incompleta: só dá pra numerar com certeza se o primeiro mês
        # visto é também o mês mais antigo disponível na janela consultada
        # (ou seja, não pode haver parcela anterior a essa data).
        if months[0] == earliest_available_month:
            resolved.append(
                ResolvedInstallmentSeries(
                    description=description,
                    account=account,
                    amount_cents=amount_cents,
                    installments_total=total,
                    first_month=months[0],
                    months_seen=tuple(months),
                )
            )
            continue

        ambiguous.append(
            AmbiguousInstallment(
                description=description,
                account=account,
                amount_cents=amount_cents,
                installments_total=total,
                months_seen=tuple(months),
                reason=(
                    f"série incompleta ({len(months)}/{total} meses) e o primeiro mês visto "
                    f"({months[0]}) não é o mês mais antigo disponível "
                    f"({earliest_available_month}) — não dá pra confirmar o início"
                ),
            )
        )

    resolved.sort(key=lambda s: (s.account, s.description))
    ambiguous.sort(key=lambda a: (a.account, a.description))
    return resolved, ambiguous


def _representative_amount(items: list[RawInstallmentOccurrence]) -> int:
    """Usa o valor mais frequente do grupo (ou o maior, em empate) como referência."""
    counts: dict[int, int] = {}
    for item in items:
        counts[item.amount_cents] = counts.get(item.amount_cents, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return best[0]


def series_active_in_range(
    series: ResolvedInstallmentSeries, since_month: str, until_month: str
) -> bool:
    """True se alguma parcela da série cai dentro de [since_month, until_month]."""
    last = series.last_month()
    return not (last < since_month or series.first_month > until_month)
