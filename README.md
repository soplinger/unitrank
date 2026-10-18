# Unitrank

**Offline unit-price comparer for plain-text grocery price lists.**

Write down what each store charges for each pack size, and Unitrank tells you which pack is really cheapest per 100 g, 100 ml or item, works out the cheapest way to buy a shopping list, and flags mistakes by line number. Everything lives in plain text files you can edit anywhere. No accounts, no network, no dependencies beyond the Python 3 standard library.

## Features

- **Plain-text price list**: one line per store and pack, `item | store | pack | price [| date]`, with `#` comments.
- **Pack parsing** for grams, kilograms, ounces, pounds, millilitres, litres, fluid ounces and counts, including multipacks such as `6 x 330 ml`.
- **Exact money**: all arithmetic uses `Decimal`. Prices must be whole cents, and discounts and tax round half-up to the cent at each step.
- **Discounts** written inline, e.g. `6.99 -10%`.
- **Sales tax** on any command with `--tax 8.25`.
- **Unit prices** per 100 g, per 100 ml, or per item, with a "vs best" percentage for every store.
- **Newest price wins**: dated lines let you keep a history, and only the latest price per store and pack counts.
- **Price history**: unit-price change between each dated price for the same store and pack.
- **Shopping plan**: give a list of needs such as `oats | 2.5 kg`. Unitrank buys whole packs at the cheapest store and shows leftovers.
- **One-store check**: the plan also reports the cheapest single store for the whole list and how much more it costs than splitting the shop.
- **Lint**: lists every problem by line number, including bad syntax, mixed units (grams compared with items), conflicting dated prices, duplicate lines and unit prices that look like typos.
- **JSON output** for `report`, `plan` and `history`, for scripts and spreadsheets.
- **Clear exit codes**: `0` for success, `1` for problems in your data, `2` for files that cannot be read.

## Quick start

The repository includes a small example price list and shopping list in `examples/`.

`examples/prices.txt`:

```text
oats | Aldi | 1 kg | 1.89
oats | Kroger | 750 g | 1.49
oats | Costco | 2 x 1.5 lb | 6.99 -10%

milk | Aldi | 4 x 1 L | 4.20
milk | Kroger | 2 L | 2.19 | 2026-01-05
milk | Kroger | 2 L | 2.35 | 2026-09-02
milk | Co-op | 1 L | 1.25

eggs | Aldi | 12 ct | 3.10
eggs | Kroger | 18 each | 4.79
```

`examples/shopping.txt`:

```text
oats | 2.5 kg
milk | 3 L
eggs | 12 ct
```

Compare unit prices, then plan the shop:

```sh
python3 unitrank.py report examples/prices.txt
python3 unitrank.py plan examples/prices.txt examples/shopping.txt
```

## How to run

Requires Python 3. There is nothing to install.

```sh
python3 unitrank.py report  PRICES [--item NAME] [--tax PERCENT] [--json]
python3 unitrank.py plan    PRICES NEEDS [--tax PERCENT] [--json]
python3 unitrank.py history PRICES [--tax PERCENT] [--json]
python3 unitrank.py lint    PRICES
```

Pack sizes accept `g`, `kg`, `mg`, `oz`, `lb`, `ml`, `cl`, `l`, `fl oz` and `ct` (also `each`, `ea`, `pc`, `pcs`, `count`). Discounts are whole percentages from 0 to 99.99.

Prices are shown in whatever currency your file uses. Unitrank does not add a currency symbol.

## How to test

Tests use pytest:

```sh
python3 -m pip install pytest
python3 -m pytest tests
```

The tests cover pack and price parsing, discount and tax rounding, unit prices, line-numbered parse errors, lint checks (mixed units, conflicts, duplicates, outliers), ranking, shopping plans with whole-pack rounding, history, the CLI exit codes and JSON output, and a check that the example price list is clean.

## Project layout

```text
unitrank.py          the whole tool (single module, standard library only)
examples/            sample price list and shopping list
tests/test_unitrank.py
```

## Notes

- A price file with errors is not ranked. `report`, `plan` and `history` print the errors and stop, so a typo cannot quietly change which store wins.
- Lines with the same item, store and pack size are compared by date. Undated lines are treated as older than any dated line.
- Everything is local. Unitrank never reads the network and writes nothing but to standard output.
