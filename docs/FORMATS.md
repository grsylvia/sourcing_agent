# Formats

```
bom.csv / bom.xlsx + suppliers.toml ──▶ sourcing agent ──▶ cbom.csv
```

## BOM input (`bom.csv` or `bom.xlsx`)

| File | Read from |
| --- | --- |
| `.csv` | Header row, then one row per part |
| `.xlsx` / `.xlsm` | Sheet `BOM` (else first sheet); row 1 = headers; blank rows skipped |

Template: [`templates/bom_template.xlsx`](../templates/bom_template.xlsx) — category dropdown, quantity check, instructions sheet. Rebuild after changing categories: `python templates/make_bom_template.py`.

| Column | Required | Example |
| --- | --- | --- |
| `part_id` | ✅ unique | `F-001` |
| `description` | ✅ | `M3x8 socket head cap screw` |
| `category` | ✅ one of `suppliers.toml` categories | `fasteners` |
| `quantity` | ✅ integer > 0 | `40` |
| `spec` | | `ISO 4762, A2 stainless` |
| `mfr_part_number` | | `608-2Z` |
| `notes` | | |

CSV example: [`examples/bom.csv`](../examples/bom.csv)

## Approved suppliers (`suppliers.toml`)

| Key | Meaning |
| --- | --- |
| `currency` | Currency for all quotes |
| `categories` | Allowed BOM categories; one worker each |
| `[[suppliers]] name` | CBOM vendor name |
| `[[suppliers]] domains` | Only hosts the worker may search/fetch |
| `[[suppliers]] categories` | Categories this supplier is searched for |

| Supplier | fasteners | bearings | motion | motors | electronics | other |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| McMaster-Carr | ✅ | ✅ | ✅ | | | ✅ |
| Misumi | ✅ | ✅ | ✅ | | | |
| Bolt Depot | ✅ | | | | | |
| Digi-Key | | | | ✅ | ✅ | |
| Mouser | | | | ✅ | ✅ | |
| Amazon | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| AliExpress | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Price rule

Lowest `extended_price` wins, where:

| Term | Definition |
| --- | --- |
| `order_qty` | `quantity` rounded up to whole packs and to the vendor MOQ |
| `pack_price` | Price per pack at the break matching `order_packs` |
| `extended_price` | `order_packs × pack_price` (shipping excluded) |

## CBOM output (`cbom.csv`)

All BOM columns, then:

| Column | Example |
| --- | --- |
| `status` | `sourced` / `not_found` / `error` |
| `vendor` | `Bolt Depot` |
| `vendor_part_number` | `5937` |
| `url` | Product page |
| `pack_size` | `100` |
| `order_packs` | `1` |
| `order_qty` | `100` |
| `pack_price` | `9.50` |
| `extended_price` | `9.50` |
| `effective_unit_price` | `0.095` (`extended_price / order_qty`) |
| `currency` | `USD` |
| `quotes_compared` | `4` |
| `quoted_at` | `2026-09-25` (original date when reused from cache) |
| `sourcing_notes` | Spec match notes, not-found reason, or error; `retried on claude-opus-5-5` when escalated |

Example values are illustrative, not real quotes.
