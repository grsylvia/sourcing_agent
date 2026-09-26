# Supplier discovery

```
CBOM not_found rows ──▶ discover (scout per category) ──▶ free screen ──▶ trial (real rows) ──▶ you approve ──▶ suppliers.toml
                              open web, blocked:                 evidence, HTTPS,     candidate's domain                     │
                              approved + reviewed + non-stores   ships to US, RDAP    only, vs CBOM price                    ▼
                                                                                                     sourcing suppliers (win rates) ──▶ prune
```

| Command | Does | API cost |
| --- | --- | --- |
| `sourcing discover <cbom>… [--category C] [--estimate]` | One scout per category with unsourced rows; screens candidates | Yes (estimate first) |
| `sourcing trial <domain> --cbom <cbom>… [--rows 4] [--estimate]` | Sources sample rows on the candidate's domain only; compares with the CBOM | Yes (estimate first) |
| `sourcing candidates [--approve D \| --reject D]` | Lists the registry; approve appends to `suppliers.toml` | Free |

## Is a supplier good? Verify by outcome, cheapest checks first

| Stage | Check | Cost | Blocks? |
| --- | --- | --- | --- |
| 1. Evidence | ≥2 product pages with a visible price, on the candidate's **own** domain | In scout | ✅ |
| 1. Evidence | Evidence pages on HTTPS | Free | ✅ |
| 1. Usability | Prices visible without login; ships to US | In scout | ✅ |
| 1. Usability | Price breaks or pack sizes shown | In scout | Flag |
| 2. Trust | Domain age via RDAP (<1 y blocks, <2 y flags) | Free | ✅ / Flag |
| 2. Trust | Contact and returns pages on its domain | In scout | Flag |
| 3. **Trial** | Quote coverage, gaps filled, cheaper vs CBOM on real rows | ~$0.1–0.6 | You decide |
| 4. Approval | Human picks; `--approve` requires a trial (unless `--without-trial`) | Free | ✅ |
| 5. Ongoing | Win rate per category (`sourcing suppliers`); prune if it never wins | Free | You decide |

Scout claims are unverified; the trial is the real test (the normal sourcing worker must find and price parts there). Web content is treated as data; nothing a scout returns reaches `suppliers.toml` without your approval.

## Cost-effective searching

| Lever | Setting |
| --- | --- |
| Demand-driven | Only categories with `not_found` / `error` rows (or `--category`) |
| One scout per category, not per part | ≤3 example parts per scout |
| Caps | 8 searches, 6 fetches, 5K-token pages per scout |
| No wasted results | `blocked_domains` = approved + already reviewed + non-stores (≤64) |
| Never pay twice | Registry (`supplier_candidates.json`) skips every reviewed domain |
| Free checks before paid ones | Screening and RDAP before any trial |
| Batch by default | 50% off tokens; `--live` for speed |
| Estimate | ~$0.12–0.36 per category (batch), uncalibrated |

## Better sources than open search

| Source | Cost | Best for | Use |
| --- | --- | --- | --- |
| URLs already in BOM notes | Free | Any | ✅ Seeds each scout ("free leads") |
| Manufacturer "where to buy" / authorized distributors | Search only | Electronics, bearings, motors | ✅ Scout step 2 |
| Industrial directories (ThomasNet) | Search only | Mechanical, industrial | ✅ Scout step 3 (no official API; scraping not used) |
| [Nexar / Octopart API](https://nexar.com/api) | Paid beyond a small evaluation allowance (~$500/mo tier) | Electronics: all distributors, prices, authorized flag | ⏳ Worth it only at higher volume |
| Open web search | ~$0.01/search + tokens | Gaps the above miss | ✅ Scout step 4 |
