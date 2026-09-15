import unicodedata

from visorsync.mapping.accounts import resolve_accounts


def test_matches_names_across_different_unicode_normal_forms():
    # "ã" as one precomposed codepoint (NFC) vs. "a" + combining tilde (NFD).
    # Organizze and Visor can independently pick either form for the same
    # visible text; a naive strip().casefold() compares them as different.
    nfc_name = unicodedata.normalize("NFC", "Inter Cartão")
    nfd_name = unicodedata.normalize("NFD", "Inter Cartão")
    assert nfc_name != nfd_name  # sanity check that the test setup is meaningful

    resolved, unresolved = resolve_accounts(
        [], [nfd_name], [{"id": "card-1", "name": nfc_name}]
    )

    assert unresolved == []
    assert len(resolved) == 1
    assert resolved[0].visor_id == "card-1"


def test_name_overrides_bridge_a_real_naming_mismatch():
    resolved, unresolved = resolve_accounts(
        ["Conta Inter"], [], [{"id": "acc-1", "name": "Inter"}],
        name_overrides={"Conta Inter": "Inter"},
    )

    assert unresolved == []
    assert resolved[0].visor_id == "acc-1"


def test_unmapped_account_is_reported_not_guessed():
    resolved, unresolved = resolve_accounts(
        ["Conta Inter"], [], [{"id": "acc-1", "name": "Inter"}]
    )

    assert resolved == []
    assert unresolved == ["Conta Inter"]
