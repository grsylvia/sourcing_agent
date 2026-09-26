---
name: source-bom
description: Price a BOM from approved suppliers and produce a CBOM (BOM + vendor + pricing) with the sourcing agent CLI. Use when the user asks to source, price, quote, or cost a BOM, or to make a CBOM.
---

# Source a BOM

Runs `~/sourcing_agent` (Claude Messages API). Each run spends API credits.

| Need | Detail |
| --- | --- |
| Credentials | `ANTHROPIC_API_KEY` set in the environment |
| BOM format | `.xlsx` or CSV per `~/sourcing_agent/docs/FORMATS.md` |
| BOM template | `~/sourcing_agent/templates/bom_template.xlsx` (fill the `BOM` sheet) |
| Suppliers | `~/sourcing_agent/suppliers.toml` |

## Steps

1. **Check the BOM.** A filled-in `bom_template.xlsx` (or any `.xlsx` with a `BOM` sheet in the FORMATS.md columns) runs as-is. If the user has no BOM, copy the template for them to fill (Windows: into OneDrive Documents). If the file is not in the FORMATS.md columns, offer to convert it into a new file (never overwrite the original). Categories must match `suppliers.toml`.
2. **Estimate.** Run `~/sourcing_agent/.venv/bin/sourcing estimate <bom>` (free, no API calls). If it prints a `Calibrated from logged runs` block, that figure (fitted to past actual costs) is the best estimate; the ranges are the uncalibrated bounds.
3. **Ask the critical questions** in one AskUserQuestion call (plain questions if the tool is unavailable). Put the estimate figures in the options, leading with the calibrated figure when there is one:

   | Question | Options | Flag |
   | --- | --- | --- |
   | Run mode? | Batch (Recommended): Sonnet range, under 1 h (up to 24 h) · Live: Sonnet range, minutes | Live → `--live` |
   | Retry failed rows on Opus 5.5? | Allow (Recommended): + per-row range, only rows Sonnet cannot source · Skip | Skip → `--no-escalate` |
   | Quote freshness? (only if cached rows > 0) | Reuse quotes up to 7 days old (free) · Re-source everything | Re-source → `--max-age 0`, then re-run step 2 |
   | CBOM location? | OneDrive Documents (Windows) · Next to the BOM | OneDrive → `--out /mnt/c/Users/grsga/OneDrive/Documents/<bom>_cbom.csv` |

4. **Confirm the run.** Show a summary, then ask "Run now?" (Run / Cancel). Run only on an explicit Run; on Cancel or any change, go back to step 3.

   | Item | Value |
   | --- | --- |
   | BOM | Path, rows (new / cached) |
   | Mode | Batch or live, expected time |
   | Opus 5.5 retries | Allowed or skipped |
   | Estimated cost | Calibrated figure (if any) and range for the chosen options |
   | Output | CBOM path |

5. **Run** in the background with the chosen flags:

   ```
   ~/sourcing_agent/.venv/bin/sourcing run <bom.xlsx|bom.csv> --out <cbom.csv> [--live] [--no-escalate] [--max-age 0]
   ```

6. **Report** from the CLI summary:

| Report | Source |
| --- | --- |
| CBOM path | `CBOM:` line |
| Sourced / not found / errors / reused | `Rows:` line |
| Parts total (no shipping) | `Parts total:` line |
| Rows retried on Opus 5.5 | `Usage (claude-opus-5-5, …)` line |
| Run cost vs estimate | `Actual vs estimate` lines (per model: actual, assumed range, calibrated figure) |
| Calibration | Pass logged to `run_log.jsonl`; the next estimate refits on it |
| Learning | `sourcing learn` (free): relay its recommendations |
| Rows to review | `not_found` / `error` rows and their `sourcing_notes` |

7. **Prune suppliers.** Run `~/sourcing_agent/.venv/bin/sourcing suppliers <cbom.csv> [older CBOMs…]` (free). If it lists drop candidates, ask in one AskUserQuestion call (multiSelect) which to drop, noting that each saves ~1 search per future row and re-sources that category's cached quotes. For each chosen drop, remove the category from that supplier's `categories` in `~/sourcing_agent/suppliers.toml`; drop the whole `[[suppliers]]` entry only if no categories remain. If rows came back `not_found`, offer `/find-suppliers` for those categories.

## Rules

- Never start `sourcing run` without the step 4 confirmation, even if the user asked to source the BOM.
- Never edit prices in the CBOM by hand; re-run rows instead.
- Never edit or delete `run_log.jsonl`; it is the calibration data.
- Never drop a supplier without the step 7 answer, and never leave a category with no supplier.
- Exit code 2 means bad input or credentials; show the message and stop.
