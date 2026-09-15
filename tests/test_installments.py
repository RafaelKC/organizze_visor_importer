from visorsync.mapping.installments import (
    RawInstallmentOccurrence,
    resolve_installment_series,
)


def _occ(month: str, amount_cents: int = 52391, total: int = 12) -> RawInstallmentOccurrence:
    return RawInstallmentOccurrence(
        description="PC Nícolas",
        account="PicPay Cartão",
        amount_cents=amount_cents,
        installments_total=total,
        month=month,
    )


def test_pc_nicolas_fragmented_series_reconstructs_to_7_of_12():
    # Fragmentação real observada: um grupo isolado em maio e um grupo de 6
    # meses (mar, abr, jun, jul, ago, set) que pula maio.
    occurrences = [
        _occ("2026-05"),
        _occ("2026-03"),
        _occ("2026-04"),
        _occ("2026-06"),
        _occ("2026-07"),
        _occ("2026-08"),
        _occ("2026-09"),
    ]

    resolved, ambiguous = resolve_installment_series(
        occurrences, earliest_available_month="2026-03"
    )

    assert ambiguous == []
    assert len(resolved) == 1
    series = resolved[0]
    assert series.first_month == "2026-03"
    assert series.installment_number_for("2026-09") == 7
    assert series.installment_number_for("2026-03") == 1
    assert series.installment_number_for("2026-10") == 8
    assert series.installment_number_for("2027-02") == 12
    assert series.installment_number_for("2027-03") is None


def test_complete_series_is_resolved_regardless_of_earliest_available_month():
    occurrences = [
        RawInstallmentOccurrence("Geladeira", "Sicredi Cartão", 35815, 10, f"2025-{m:02d}")
        for m in range(12, 13)
    ] + [
        RawInstallmentOccurrence("Geladeira", "Sicredi Cartão", 35815, 10, f"2026-{m:02d}")
        for m in range(1, 9)
    ]

    resolved, ambiguous = resolve_installment_series(
        occurrences, earliest_available_month="2025-12"
    )

    assert ambiguous == []
    series = resolved[0]
    assert series.first_month == "2025-12"
    assert series.installment_number_for("2026-09") == 10


def test_incomplete_series_not_at_earliest_available_month_is_ambiguous():
    occurrences = [
        RawInstallmentOccurrence("Compra X", "Inter Cartão", 10000, 12, "2026-04"),
        RawInstallmentOccurrence("Compra X", "Inter Cartão", 10000, 12, "2026-05"),
    ]

    resolved, ambiguous = resolve_installment_series(
        occurrences, earliest_available_month="2026-01"
    )

    assert resolved == []
    assert len(ambiguous) == 1
    assert ambiguous[0].description == "Compra X"


def test_amounts_differing_by_a_few_cents_are_grouped_together():
    occurrences = [
        RawInstallmentOccurrence("Maquina Lava e Seca", "Inter Cartão", 32690, 10, "2026-01"),
        RawInstallmentOccurrence("Maquina Lava e Seca", "Inter Cartão", 32689, 10, "2026-02"),
    ]

    resolved, ambiguous = resolve_installment_series(
        occurrences, earliest_available_month="2026-01"
    )

    assert ambiguous == []
    assert len(resolved) == 1
