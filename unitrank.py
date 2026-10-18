#!/usr/bin/env python3
"""Unitrank: offline unit-price comparer for plain-text grocery price lists."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

CENT = Decimal("0.01")
UNIT_DP = Decimal("0.0001")
TENTH = Decimal("0.1")
HUNDRED = Decimal(100)

# unit -> (family, size of one unit in the family's base: grams, millilitres or items)
UNITS = {
    "mg": ("mass", Decimal("0.001")),
    "g": ("mass", Decimal(1)),
    "kg": ("mass", Decimal(1000)),
    "oz": ("mass", Decimal("28.349523125")),
    "lb": ("mass", Decimal("453.59237")),
    "ml": ("volume", Decimal(1)),
    "cl": ("volume", Decimal(10)),
    "l": ("volume", Decimal(1000)),
    "floz": ("volume", Decimal("29.5735295625")),
    "ct": ("count", Decimal(1)),
}
ALIASES = {"each": "ct", "ea": "ct", "pc": "ct", "pcs": "ct", "count": "ct", "litre": "l", "liter": "l"}

# family -> (scale for the unit price, label shown next to it)
PER = {
    "mass": (HUNDRED, "per 100 g"),
    "volume": (HUNDRED, "per 100 ml"),
    "count": (Decimal(1), "per ct"),
}

PACK_RE = re.compile(r"^(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*([a-z]+)$")
PRICE_RE = re.compile(r"^\$?(\d+(?:\.\d{1,2})?)(?:\s*-\s*(\d{1,2}(?:\.\d+)?)%)?$")


def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_pack(text: str) -> tuple[Decimal, str]:
    """Return (base quantity, family) for '1 kg', '6 x 330 ml' or '12 ct'."""
    norm = _clean(text).lower().replace("fl oz", "floz")
    match = PACK_RE.match(norm)
    if not match:
        raise ValueError(f"cannot read pack size {text!r}; try '1 kg', '6 x 330 ml' or '12 ct'")
    count = Decimal(match.group(1) or 1)
    size = Decimal(match.group(2))
    unit = ALIASES.get(match.group(3), match.group(3))
    if unit not in UNITS:
        raise ValueError(f"unknown unit {match.group(3)!r} in {text!r}")
    family, factor = UNITS[unit]
    base = count * size * factor
    if base <= 0:
        raise ValueError(f"pack size must be above zero: {text!r}")
    return base, family


def parse_price(text: str) -> tuple[Decimal, Decimal]:
    """Return (list price, discount percent) for '3.10' or '6.99 -10%'."""
    match = PRICE_RE.match(text.strip())
    if not match:
        raise ValueError(f"cannot read price {text!r}; use exact cents such as 3.10 or 6.99 -10%")
    price = Decimal(match.group(1))
    off = Decimal(match.group(2) or 0)
    if price <= 0:
        raise ValueError("price must be above zero")
    if off >= 100:
        raise ValueError("discount must be below 100%")
    return price, off


def human(base: Decimal, family: str) -> str:
    """Format a base quantity back into friendly units."""
    if family == "mass":
        return f"{base / 1000:.3f} kg" if base >= 1000 else f"{base:.0f} g"
    if family == "volume":
        return f"{base / 1000:.3f} L" if base >= 1000 else f"{base:.0f} ml"
    return f"{base:.0f} ct"


def money(amount: Decimal) -> str:
    return f"{amount.quantize(CENT, ROUND_HALF_UP):.2f}"


def percent_over(value: Decimal, base: Decimal) -> Decimal:
    return ((value - base) / base * 100).quantize(TENTH, ROUND_HALF_UP)


@dataclass(frozen=True)
class Entry:
    line: int
    item: str
    store: str
    pack: str
    base: Decimal
    family: str
    price: Decimal
    off: Decimal
    day: date | None

    @property
    def key(self) -> str:
        return self.item.lower()

    def paid(self, tax: Decimal = Decimal(0)) -> Decimal:
        """Shelf price after discount and tax, each step rounded to the cent."""
        net = (self.price * (100 - self.off) / 100).quantize(CENT, ROUND_HALF_UP)
        return (net * (100 + tax) / 100).quantize(CENT, ROUND_HALF_UP)

    def unit_price(self, tax: Decimal = Decimal(0)) -> Decimal:
        scale, _ = PER[self.family]
        return (self.paid(tax) * scale / self.base).quantize(UNIT_DP, ROUND_HALF_UP)


@dataclass(frozen=True)
class Need:
    line: int
    item: str
    qty: str
    base: Decimal
    family: str

    @property
    def key(self) -> str:
        return self.item.lower()


def parse_line(number: int, line: str) -> Entry:
    fields = [field.strip() for field in line.split("|")]
    if len(fields) not in (4, 5):
        raise ValueError("expected 'item | store | pack | price [| date]'")
    item, store, pack, price_text = fields[:4]
    if not item or not store:
        raise ValueError("item and store cannot be empty")
    base, family = parse_pack(pack)
    price, off = parse_price(price_text)
    day = None
    if len(fields) == 5 and fields[4]:
        try:
            day = date.fromisoformat(fields[4])
        except ValueError:
            raise ValueError(f"bad date {fields[4]!r}, use YYYY-MM-DD") from None
    return Entry(number, _clean(item), _clean(store), _clean(pack), base, family, price, off, day)


def parse_need(number: int, line: str) -> Need:
    fields = [field.strip() for field in line.split("|")]
    if len(fields) != 2 or not fields[0]:
        raise ValueError("expected 'item | quantity', e.g. 'oats | 2.5 kg'")
    base, family = parse_pack(fields[1])
    return Need(number, _clean(fields[0]), _clean(fields[1]), base, family)


def _parse_lines(text: str, parse) -> tuple[list, list[tuple[int, str]]]:
    items, errors = [], []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            items.append(parse(number, line))
        except ValueError as exc:
            errors.append((number, str(exc)))
    return items, errors


def parse_text(text: str) -> tuple[list[Entry], list[tuple[int, str]]]:
    return _parse_lines(text, parse_line)


def group_by_item(entries: list[Entry]) -> dict[str, list[Entry]]:
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        groups.setdefault(entry.key, []).append(entry)
    return groups


def latest(entries: list[Entry]) -> list[Entry]:
    """Keep only the newest price for each item, store and pack size."""
    newest: dict[tuple, Entry] = {}
    for entry in entries:
        k = (entry.key, entry.family, entry.store.lower(), entry.base)
        if k not in newest or (entry.day or date.min) >= (newest[k].day or date.min):
            newest[k] = entry
    return list(newest.values())


def check(entries: list[Entry]) -> list[tuple[int, str, str]]:
    """Cross-line checks. Returns (line, 'error' or 'warning', message) tuples."""
    findings = []
    for members in group_by_item(entries).values():
        first = members[0]
        mixed = [e for e in members if e.family != first.family]
        for e in mixed:
            findings.append((e.line, "error", f"'{e.item}' mixes {first.family} and {e.family} "
                                              f"(first priced on line {first.line}); compare one family at a time"))
        if not mixed and len(members) >= 3:
            prices = [e.unit_price() for e in members]
            median = statistics.median(prices)
            for e, price in zip(members, prices):
                if price > 3 * median or price * 3 < median:
                    findings.append((e.line, "warning", f"unit price {price} is far from the median {median}; "
                                                        "check the pack size"))
    seen: dict[tuple, Entry] = {}
    for e in entries:
        k = (e.key, e.family, e.store.lower(), e.base, e.day)
        prior = seen.get(k)
        if prior is None:
            seen[k] = e
        elif e.day is not None and (prior.price, prior.off) != (e.price, e.off):
            findings.append((e.line, "error", f"conflicting prices for '{e.item}' at {e.store} on {e.day} "
                                              f"(line {prior.line} says {prior.price})"))
        else:
            findings.append((e.line, "warning", f"duplicate of line {prior.line}; remove one of them"))
    return findings


def report_data(entries: list[Entry], tax: Decimal = Decimal(0), only: str | None = None) -> list[dict]:
    blocks = []
    for key, members in sorted(group_by_item(latest(entries)).items()):
        if only and key != only.lower():
            continue
        ordered = sorted(members, key=lambda e: (e.unit_price(tax), e.store.lower()))
        best = ordered[0].unit_price(tax)
        rows = []
        for e in ordered:
            unit = e.unit_price(tax)
            rows.append({"store": e.store, "pack": e.pack, "line": e.line, "paid": e.paid(tax),
                         "unit": unit, "label": PER[e.family][1], "vs_best": percent_over(unit, best)})
        blocks.append({"item": ordered[0].item, "rows": rows})
    return blocks


def history_data(entries: list[Entry], tax: Decimal = Decimal(0)) -> list[dict]:
    buckets: dict[tuple, list[Entry]] = {}
    for e in entries:
        if e.day is not None:
            buckets.setdefault((e.key, e.store.lower(), e.family, e.base), []).append(e)
    rows = []
    for _, members in sorted(buckets.items()):
        members.sort(key=lambda e: e.day)
        for old, new in zip(members, members[1:]):
            before, after = old.unit_price(tax), new.unit_price(tax)
            rows.append({"item": new.item, "store": new.store, "from": old.day, "to": new.day,
                         "unit_from": before, "unit_to": after, "label": PER[new.family][1],
                         "change": percent_over(after, before)})
    return rows


def plan_data(entries: list[Entry], needs: list[Need], tax: Decimal = Decimal(0)) -> dict:
    lines, missing, problems, options_by_need = [], [], [], []
    for need in needs:
        options = [e for e in latest(entries) if e.key == need.key]
        if not options:
            missing.append(need.item)
            continue
        mixed = [e for e in options if e.family != need.family]
        if mixed:
            problems.append(f"line {need.line}: '{need.item}' is a {need.family} quantity "
                            f"but line {mixed[0].line} prices it by {mixed[0].family}")
            continue
        choices = []
        for e in options:
            packs = int((need.base / e.base).to_integral_value(rounding=ROUND_CEILING))
            choices.append({"store": e.store, "pack": e.pack, "line": e.line, "packs": packs,
                            "cost": e.paid(tax) * packs, "extra": e.base * packs - need.base})
        choices.sort(key=lambda c: (c["cost"], c["store"].lower()))
        options_by_need.append(choices)
        lines.append({"item": need.item, "qty": need.qty, "family": need.family, **choices[0]})

    split_total = sum((line["cost"] for line in lines), Decimal(0))
    single = None
    if options_by_need and not missing and not problems:
        names: set[str] | None = None
        display: dict[str, str] = {}
        for choices in options_by_need:
            for c in choices:
                display.setdefault(c["store"].lower(), c["store"])
            here = {c["store"].lower() for c in choices}
            names = here if names is None else names & here
        for name in sorted(names or []):
            total = sum((min(c["cost"] for c in choices if c["store"].lower() == name)
                         for choices in options_by_need), Decimal(0))
            if single is None or total < single["total"]:
                single = {"store": display[name], "total": total}
    return {"lines": lines, "split_total": split_total, "single_store": single,
            "missing": missing, "problems": problems}


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]

    def fmt(cells):
        return "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths)).rstrip()

    return "\n".join([fmt(headers), *(fmt(row) for row in rows)])


def render_report(blocks: list[dict]) -> str:
    if not blocks:
        return "no items match"
    sections = []
    for block in blocks:
        rows = [[r["store"], r["pack"], money(r["paid"]), f"{r['unit']} {r['label']}",
                 "best" if r["vs_best"] == 0 else f"+{r['vs_best']}%"] for r in block["rows"]]
        sections.append(block["item"] + "\n" + _table(["store", "pack", "price", "unit price", "vs best"], rows))
    return "\n\n".join(sections)


def render_history(rows: list[dict]) -> str:
    if not rows:
        return "no dated prices to compare"
    body = [[r["item"], r["store"], str(r["from"]), str(r["to"]), f"{r['unit_from']} {r['label']}",
             f"{r['unit_to']} {r['label']}", f"{r['change']:+}%"] for r in rows]
    return _table(["item", "store", "from", "to", "was", "now", "change"], body)


def render_plan(plan: dict) -> str:
    out = []
    if plan["lines"]:
        rows = [[line["item"], line["qty"], line["store"], f"{line['packs']} x {line['pack']}",
                 money(line["cost"]), human(line["extra"], line["family"])] for line in plan["lines"]]
        out.append(_table(["item", "need", "buy at", "buy", "cost", "left over"], rows))
        out.append(f"Split across cheapest stores: {money(plan['split_total'])}")
    single = plan["single_store"]
    if single:
        extra = single["total"] - plan["split_total"]
        note = "no extra cost" if extra == 0 else f"{money(extra)} more than split"
        out.append(f"One store for everything: {single['store']} {money(single['total'])} ({note})")
    for item in plan["missing"]:
        out.append(f"not in price list: {item}")
    for problem in plan["problems"]:
        out.append(f"problem: {problem}")
    return "\n".join(out) if out else "nothing to buy"


def _percent(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("tax cannot be negative")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="unitrank", description="Offline unit-price comparer for plain-text price lists.")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="rank stores by unit price for each item")
    report.add_argument("prices")
    report.add_argument("--item", help="show only this item")
    report.add_argument("--tax", type=_percent, default=Decimal(0), help="sales tax percent")
    report.add_argument("--json", action="store_true")

    plan = sub.add_parser("plan", help="cheapest way to buy a shopping list")
    plan.add_argument("prices")
    plan.add_argument("needs", help="file of 'item | quantity' lines")
    plan.add_argument("--tax", type=_percent, default=Decimal(0), help="sales tax percent")
    plan.add_argument("--json", action="store_true")

    history = sub.add_parser("history", help="show unit-price changes between dated prices")
    history.add_argument("prices")
    history.add_argument("--tax", type=_percent, default=Decimal(0), help="sales tax percent")
    history.add_argument("--json", action="store_true")

    lint = sub.add_parser("lint", help="check a price list and list every problem by line")
    lint.add_argument("prices")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        entries, errors = _parse_lines(Path(args.prices).read_text(encoding="utf-8"), parse_line)
    except OSError as exc:
        print(f"unitrank: {exc.strerror or exc}", file=sys.stderr)
        return 2

    if args.command == "lint":
        findings = [(n, "error", m) for n, m in errors] + check(entries)
        if not findings:
            print("no problems found")
            return 0
        for line, severity, message in sorted(findings):
            print(f"line {line}: {severity}: {message}")
        return 1 if any(severity == "error" for _, severity, _ in findings) else 0

    findings = check(entries)
    for line, severity, message in findings:
        if severity == "warning":
            print(f"warning: line {line}: {message}", file=sys.stderr)
    problems = [f"line {n}: {m}" for n, m in errors] + [
        f"line {n}: {m}" for n, severity, m in findings if severity == "error"]
    if problems:
        for problem in sorted(problems):
            print(f"error: {problem}", file=sys.stderr)
        return 1

    if args.command == "report":
        blocks = report_data(entries, args.tax, args.item)
        print(json.dumps(blocks, indent=2, default=str) if args.json else render_report(blocks))
        return 0

    if args.command == "history":
        rows = history_data(entries, args.tax)
        print(json.dumps(rows, indent=2, default=str) if args.json else render_history(rows))
        return 0

    try:
        needs, need_errors = _parse_lines(Path(args.needs).read_text(encoding="utf-8"), parse_need)
    except OSError as exc:
        print(f"unitrank: {exc.strerror or exc}", file=sys.stderr)
        return 2
    if need_errors:
        for n, message in need_errors:
            print(f"error: needs file line {n}: {message}", file=sys.stderr)
        return 1
    result = plan_data(entries, needs, args.tax)
    print(json.dumps(result, indent=2, default=str) if args.json else render_plan(result))
    return 1 if result["missing"] or result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
