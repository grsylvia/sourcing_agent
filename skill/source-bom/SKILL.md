---
name: source-bom
description: Initialize personal sourcing preferences and review default sources, or price a BOM from approved suppliers and produce a CBOM. Use for sourcing setup, personal sourcing memory, pricing, quoting, or costing a BOM.
---

# Source a BOM

Runs `<repo>` (Claude Messages API). Setup and estimates are offline; fresh sourcing spends API credits, while cache-only runs do not.

| Need | Detail |
| --- | --- |
| Credentials | `ANTHROPIC_API_KEY` set in the environment |
| BOM format | `.xlsx` or CSV per `<repo>/docs/FORMATS.md` |
| BOM template | `<repo>/templates/bom_template.xlsx` (fill the `BOM` sheet) |
| Suppliers | `<repo>/suppliers.toml` |

## Startup and memory

Follow [PERSONAL_SETUP.md](../../docs/PERSONAL_SETUP.md) before sourcing or when asked to initialize personal memory or review defaults. It defines folder initialization, explicit source review, and the boundary between personal preferences and shared rules. For a setup-only request, finish after startup; do not begin sourcing.

## Steps

1. **Check the BOM.** A filled-in `bom_template.xlsx` (or any `.xlsx` with a `BOM` sheet in the FORMATS.md columns) runs as-is. Use the saved BOM-input folder for bare filenames and the saved template folder for a new template; never overwrite a filled template. If the file is not in the FORMATS.md columns, offer to convert it into a new file (never overwrite the original). Categories must match `suppliers.toml`.
2. **Estimate.** Run `sourcing estimate <bom>` (free, no API calls). If it prints a `Calibrated from logged runs` block, that figure (fitted to past actual costs) is the best estimate; the ranges are the uncalibrated bounds.
3. **Ask the critical questions** in one AskUserQuestion call (plain questions if the tool is unavailable). Put the estimate figures in the options, leading with the calibrated figure when there is one:

   | Question | Options | Flag |
   | --- | --- | --- |
   | Run mode? | Batch (Recommended): Sonnet range, under 1 h (up to 24 h) · Live: Sonnet range, minutes | Live → `--live` |
   | Retry failed rows on Opus 5.5? | Allow (Recommended): + per-row range, only rows Sonnet cannot source · Skip | Skip → `--no-escalate` |
   | Quote freshness? (only if cached rows > 0) | Reuse quotes up to 7 days old (free) · Re-source everything | Re-source → `--max-age 0`, then re-run step 2 |
   | CBOM location? | Saved CBOM folder (default) · Explicit path | `--out <chosen-folder>/<bom>_cbom.csv` |

4. **Confirm the run.** Show a summary, then ask "Run now?" (Run / Cancel). Run only on explicit confirmation. On Cancel, stop; on changed options, update the estimate and summary before confirming again.

   | Item | Value |
   | --- | --- |
   | BOM | Path, rows (new / cached) |
   | Mode | Batch or live, expected time |
   | Opus 5.5 retries | Allowed or skipped |
   | Estimated cost | Calibrated figure (if any) and range for the chosen options |
   | Output | CBOM path |

5. **Run** in the background with the chosen flags:

   ```
   sourcing run <bom.xlsx|bom.csv> --out <cbom.csv> [--live] [--no-escalate] [--max-age 0]
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

7. **Prune suppliers.** Run `sourcing suppliers <cbom.csv> [older CBOMs…]` (free). Only consider candidates backed by the learning log: at least three consecutive first-pass runs with five or more explicitly checked rows each and no valid quotes. Zero wins alone is never a failure; valid losing quotes reset the streak, and errors, unchecked rows, or smaller samples break it. Historical CBOMs alone cannot justify drops. If it lists drop candidates, ask in one AskUserQuestion call (multiSelect) which to drop, noting that each saves ~1 search per future row and re-sources that category's cached quotes. For each chosen drop, remove the category from that supplier's `categories` in `<repo>/suppliers.toml`; drop the whole `[[suppliers]]` entry only if no categories remain. If rows came back `not_found`, offer `/find-suppliers` for those categories.

## Rules

- Never start `sourcing run` without the step 4 confirmation, even if the user asked to source the BOM.
- Never edit prices in the CBOM by hand; re-run rows instead.
- Never edit or delete `run_log.jsonl`; it is the calibration data.
- Never drop a supplier without the step 7 answer, and never leave a category with no supplier.
- Exit code 2 means bad input or credentials; show the message and stop.
