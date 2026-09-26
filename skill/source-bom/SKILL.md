---
name: source-bom
description: Price a BOM from approved suppliers and produce a CBOM (BOM + vendor + pricing) with the sourcing agent CLI. Use when the user asks to source, price, quote, or cost a BOM, or to make a CBOM.
---

# Source a BOM

Runs `~/sourcing_agent` (Claude Messages API). Each run spends API credits.

| Need | Detail |
| --- | --- |
| Credentials | `ANTHROPIC_API_KEY` set in the environment |
| BOM format | CSV per `~/sourcing_agent/docs/FORMATS.md` |
| Suppliers | `~/sourcing_agent/suppliers.toml` |

## Steps

1. **Check the BOM.** If it is not in the FORMATS.md columns, offer to convert it into a new CSV (never overwrite the original). Categories must match `suppliers.toml`.
2. **Confirm cost.** Tell the user the row count and estimate (~$0.08–0.29 per new row in batch mode, ~$0.13–0.53 with `--live`; cached rows are free), then wait for a go-ahead.
3. **Run** in the background (batch mode usually takes under 1 h, up to 24 h):

   ```
   ~/sourcing_agent/.venv/bin/sourcing run <bom.csv> --out <cbom.csv>
   ```

   Add `--live` only if the user needs results fast (full price). Add `--max-age 0` to force fresh quotes.

   For a Windows export, set `--out` under `/mnt/c/Users/grsga/OneDrive/Documents/`.
4. **Report** from the CLI summary:

| Report | Source |
| --- | --- |
| CBOM path | `CBOM:` line |
| Sourced / not found / errors / reused | `Rows:` line |
| Parts total (no shipping) | `Parts total:` line |
| Run cost | `Estimated API cost:` line |
| Rows to review | `not_found` / `error` rows and their `sourcing_notes` |

## Rules

- Never edit prices in the CBOM by hand; re-run rows instead.
- Exit code 2 means bad input or credentials; show the message and stop.
