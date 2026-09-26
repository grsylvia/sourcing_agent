---
name: source-bom
description: Initialize personal sourcing preferences and review default sources, or price a BOM from approved suppliers and produce a CBOM. Use for sourcing setup, personal sourcing memory, pricing, quoting, or costing a BOM.
---

# Source a BOM

Runs `<repo>` (Anthropic Messages or OpenAI Responses API). Setup and estimates are offline; fresh sourcing spends API credits, while cache-only runs do not.

| Need | Detail |
| --- | --- |
| Credentials | `ANTHROPIC_API_KEY` for Anthropic or `OPENAI_API_KEY` for OpenAI |
| BOM format | `.xlsx` or CSV per `<repo>/docs/FORMATS.md` |
| BOM template | `<repo>/templates/bom_template.xlsx` (fill the `BOM` sheet) |
| Suppliers | `<repo>/suppliers.toml` |

## Startup and memory

Follow [PERSONAL_SETUP.md](../../docs/PERSONAL_SETUP.md) before sourcing or when asked to initialize personal memory or review defaults. It defines folder initialization, explicit source review, and the boundary between personal preferences and shared rules. For a setup-only request, finish after startup; do not begin sourcing.

## Steps

1. **Check the BOM.** A filled-in `bom_template.xlsx` (or any `.xlsx` with a `BOM` sheet in the FORMATS.md columns) runs as-is. If the user has no BOM, copy the template into their saved template folder for them to fill. If the file is not in the FORMATS.md columns, offer to convert it into a new file (never overwrite the original). Categories must match `suppliers.toml`.
2. **Estimate.** Run `sourcing estimate <bom> --compare` (free, no API calls). Show the provider comparison: identical assumed workload, including searches, not a measured quality benchmark. Use calibration only for the provider it names.
3. **Ask provider and model first**, using AskUserQuestion (plain questions if unavailable). Offer Anthropic / Sonnet 5 (default), OpenAI / GPT-6 Sol, and OpenAI / GPT-6 Luna, with their estimated costs from step 2. Also accept any supported model shown by `estimate --compare`, including Opus 5.5 and GPT-6 Astra. Respect choices already supplied by the user.

   Re-run the free estimate with `--provider <anthropic|openai> --model <model>`. Then ask the remaining questions together, using the selected provider's estimates:

   | Question | Options | Flag |
   | --- | --- | --- |
   | Run mode? | Anthropic: batch (default, up to 24 h) or live (minutes), with ranges · OpenAI: live only; state this without offering batch | Live → `--live` |
   | Retry failed rows? | Allow: Anthropic → Opus 5.5; OpenAI → GPT-6 Astra, with per-row range · Skip · Selected retry model | Skip → `--no-escalate`; custom → `--escalation-model <model>` |
   | Quote freshness? (only if cached rows > 0) | Reuse quotes up to 7 days old (free) · Re-source everything | Re-source → `--max-age 0`, then re-run step 2 |
   | CBOM location? | Saved CBOM folder (default) · Explicit path | `--out <chosen-folder>/<bom>_cbom.csv` |

   Re-estimate after any model, retry-model, or freshness change. Verify the selected provider's credential only when rows need fresh sourcing; never display its value.

4. **Confirm the run.** Show a summary, then ask "Run now?" (Run / Cancel). Run only on explicit confirmation. On Cancel, stop; on changed options, update the estimate and summary before confirming again.

   | Item | Value |
   | --- | --- |
   | BOM | Path, rows (new / cached) |
   | Provider / model | Selected provider, first-pass model, required credential variable |
   | Mode | Batch or live, expected time |
   | Retries | Allowed or skipped, selected retry model |
   | Estimated cost | Calibrated figure (if any) and range for the chosen options |
   | Output | CBOM path |

5. **Run** in the background with the chosen flags:

   ```
   sourcing run <bom.xlsx|bom.csv> --out <cbom.csv> --provider <provider> --model <model> [--escalation-model <model>] [--live] [--no-escalate] [--max-age 0]
   ```

6. **Report** from the CLI summary:

| Report | Source |
| --- | --- |
| CBOM path | `CBOM:` line |
| Sourced / not found / errors / reused | `Rows:` line |
| Parts total (no shipping) | `Parts total:` line |
| Retried rows | `Usage (<selected retry model>, …)` line |
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
