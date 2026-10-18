import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unitrank  # noqa: E402
from unitrank import (  # noqa: E402
    check,
    history_data,
    latest,
    main,
    parse_need,
    parse_pack,
    parse_price,
    parse_text,
    plan_data,
    report_data,
)


def entries_from(text):
    entries, errors = parse_text(text)
    assert errors == []
    return entries


def test_parse_pack_single_and_multipack():
    assert parse_pack("1 kg") == (Decimal(1000), "mass")
    assert parse_pack("6 x 330 ml") == (Decimal(1980), "volume")
    assert parse_pack("2 x 1.5 lb")[0] == Decimal("2") * Decimal("1.5") * Decimal("453.59237")


def test_parse_pack_fluid_ounces_and_count_aliases():
    base, family = parse_pack("12 fl oz")
    assert family == "volume"
    assert base == Decimal("12") * Decimal("29.5735295625")
    assert parse_pack("18 each") == (Decimal(18), "count")


def test_parse_pack_rejects_unknown_unit():
    with pytest.raises(ValueError, match="unknown unit 'furlongs'"):
        parse_pack("3 furlongs")


def test_parse_price_discount_and_exact_cents():
    assert parse_price("3.10") == (Decimal("3.10"), Decimal(0))
    assert parse_price("6.99 -10%") == (Decimal("6.99"), Decimal("10"))
    with pytest.raises(ValueError, match="exact cents"):
        parse_price("3.105")


def test_paid_applies_discount_then_tax_with_rounding():
    entry = entries_from("oats | Aldi | 1 kg | 6.99 -10%")[0]
    assert entry.paid() == Decimal("6.29")
    plain = entries_from("eggs | Aldi | 12 ct | 3.10")[0]
    assert plain.paid(Decimal("8.25")) == Decimal("3.36")


def test_unit_price_per_100g_and_per_count():
    oats = entries_from("oats | Aldi | 1 kg | 1.89")[0]
    assert oats.unit_price() == Decimal("0.1890")
    eggs = entries_from("eggs | Aldi | 12 ct | 3.10")[0]
    assert eggs.unit_price() == Decimal("0.2583")


def test_parse_text_skips_comments_and_reports_line_numbers():
    text = "# header\n\noats | Aldi | 1 kg | 1.89\noats | Aldi | 1 kg 1.89\n"
    entries, errors = parse_text(text)
    assert [e.line for e in entries] == [3]
    assert errors == [(4, "expected 'item | store | pack | price [| date]'")]


def test_bad_date_is_reported():
    _, errors = parse_text("milk | Kroger | 2 L | 2.35 | 2026-13-01")
    assert errors and errors[0][0] == 1 and "bad date" in errors[0][1]


def test_latest_keeps_newest_price_per_store_and_pack():
    entries = entries_from(
        "milk | Kroger | 2 L | 2.19 | 2026-01-05\n"
        "milk | Kroger | 2 L | 2.35 | 2026-09-02\n"
        "milk | Aldi | 4 x 1 L | 4.20\n"
    )
    kept = {(e.store, e.price) for e in latest(entries)}
    assert kept == {("Kroger", Decimal("2.35")), ("Aldi", Decimal("4.20"))}


def test_check_flags_mixed_families():
    findings = check(entries_from("oats | Aldi | 1 kg | 1.89\noats | Co | 12 ct | 2.00"))
    assert (2, "error", findings[0][2]) in findings
    assert findings[0][0] == 2 and findings[0][1] == "error"


def test_check_flags_conflicting_dated_prices_and_duplicates():
    findings = check(entries_from(
        "milk | Kroger | 2 L | 2.19 | 2026-01-05\n"
        "milk | Kroger | 2 L | 2.25 | 2026-01-05\n"
        "eggs | Aldi | 12 ct | 3.10\n"
        "eggs | Aldi | 12 ct | 3.10\n"
    ))
    severities = {(line, sev) for line, sev, _ in findings}
    assert (2, "error") in severities
    assert (4, "warning") in severities


def test_check_flags_unit_price_outlier():
    findings = check(entries_from(
        "eggs | Aldi | 12 ct | 3.10\n"
        "eggs | Co-op | 12 ct | 3.00\n"
        "eggs | Market | 12 ct | 30.00\n"
    ))
    assert [(line, sev) for line, sev, _ in findings] == [(3, "warning")]


def test_report_ranks_stores_and_shows_percent_over_best():
    blocks = report_data(entries_from("oats | Kroger | 750 g | 1.49\noats | Aldi | 1 kg | 1.89"))
    rows = blocks[0]["rows"]
    assert blocks[0]["item"] == "oats"
    assert [r["store"] for r in rows] == ["Aldi", "Kroger"]
    assert rows[0]["vs_best"] == 0
    assert rows[1]["vs_best"] == Decimal("5.1")


def test_plan_uses_whole_packs_and_picks_cheapest_store():
    entries = entries_from("oats | Aldi | 1 kg | 1.89\noats | Kroger | 750 g | 1.49")
    needs = [parse_need(1, "oats | 2.5 kg")]
    result = plan_data(entries, needs)
    line = result["lines"][0]
    assert line["store"] == "Aldi" and line["packs"] == 3 and line["cost"] == Decimal("5.67")
    assert line["extra"] == Decimal(500)


def test_plan_single_store_total_versus_split():
    entries = entries_from(
        "oats | Aldi | 1 kg | 1.89\n"
        "oats | Kroger | 750 g | 1.49\n"
        "milk | Aldi | 4 x 1 L | 4.20\n"
        "milk | Kroger | 1 L | 1.00\n"
    )
    needs = [parse_need(1, "oats | 2.5 kg"), parse_need(2, "milk | 3 L")]
    result = plan_data(entries, needs)
    assert result["split_total"] == Decimal("8.67")
    assert result["single_store"] == {"store": "Kroger", "total": Decimal("8.96")}


def test_plan_reports_items_missing_from_price_list():
    result = plan_data(entries_from("oats | Aldi | 1 kg | 1.89"), [parse_need(1, "tofu | 1 kg")])
    assert result["missing"] == ["tofu"]
    assert result["lines"] == []


def test_history_reports_unit_price_change_between_dates():
    rows = history_data(entries_from(
        "milk | Kroger | 2 L | 2.19 | 2026-01-05\n"
        "milk | Kroger | 2 L | 2.35 | 2026-09-02\n"
    ))
    assert len(rows) == 1
    assert rows[0]["from"] == date(2026, 1, 5)
    assert rows[0]["unit_from"] == Decimal("0.1095")
    assert rows[0]["change"] == Decimal("7.3")


def test_cli_lint_exits_nonzero_on_error(tmp_path, capsys):
    prices = tmp_path / "prices.txt"
    prices.write_text("oats | Aldi | 1 kg | 1.89\noats | Co | 12 ct | 2.00\n", encoding="utf-8")
    assert main(["lint", str(prices)]) == 1
    assert "line 2: error:" in capsys.readouterr().out


def test_cli_lint_clean_file_exits_zero(tmp_path, capsys):
    prices = tmp_path / "prices.txt"
    prices.write_text("oats | Aldi | 1 kg | 1.89\noats | Kroger | 750 g | 1.49\n", encoding="utf-8")
    assert main(["lint", str(prices)]) == 0
    assert capsys.readouterr().out.strip() == "no problems found"


def test_cli_report_json_output(tmp_path, capsys):
    prices = tmp_path / "prices.txt"
    prices.write_text("oats | Aldi | 1 kg | 1.89\noats | Kroger | 750 g | 1.49\n", encoding="utf-8")
    assert main(["report", str(prices), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["item"] == "oats"
    assert data[0]["rows"][0]["unit"] == "0.1890"


def test_cli_refuses_report_when_prices_have_errors(tmp_path, capsys):
    prices = tmp_path / "prices.txt"
    prices.write_text("oats | Aldi | 1 kg\n", encoding="utf-8")
    assert main(["report", str(prices)]) == 1
    assert "line 1" in capsys.readouterr().err


def test_cli_plan_returns_one_when_item_missing(tmp_path, capsys):
    prices = tmp_path / "prices.txt"
    prices.write_text("oats | Aldi | 1 kg | 1.89\n", encoding="utf-8")
    needs = tmp_path / "needs.txt"
    needs.write_text("tofu | 1 kg\n", encoding="utf-8")
    assert main(["plan", str(prices), str(needs)]) == 1
    assert "not in price list: tofu" in capsys.readouterr().out


def test_cli_missing_file_exits_two(tmp_path, capsys):
    assert main(["lint", str(tmp_path / "nope.txt")]) == 2


def test_example_files_are_clean():
    root = Path(unitrank.__file__).resolve().parent / "examples"
    assert main(["lint", str(root / "prices.txt")]) == 0
