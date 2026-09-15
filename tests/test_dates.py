from visorsync.mapping.dates import to_iso_date


def test_converts_brazilian_date_format():
    assert to_iso_date("03/09/2026") == "2026-09-03"


def test_leaves_already_iso_date_unchanged():
    assert to_iso_date("2026-09-03") == "2026-09-03"


def test_handles_none_and_empty():
    assert to_iso_date(None) is None
    assert to_iso_date("") == ""
