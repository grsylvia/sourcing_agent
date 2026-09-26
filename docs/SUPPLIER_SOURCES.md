# Outside supplier verification

Research shortlist, checked 2026-09-26. Discovery loads this guide into each scout prompt. Scouts research within the existing caps and return dated outside observations; code records coverage gaps and concerns for review.

## Sources

| Source | Best use | Evidence limit |
| --- | --- | --- |
| [NSK Americas distributor finder](https://www.nsk.com/am-en/distributor-search/) and [SKF authorized-channel guidance](https://cdn.skfmediahub.skf.com/api/public/0901d19680479f29/pdf_preview_medium/0901d19680479f29_pdf_preview_medium.pdf) | Verify brand authorization with the manufacturer; discover distributors | Match company, region, and brand; authorization does not establish stock or price |
| [Thomasnet](https://www.thomasnet.com/) | Find industrial suppliers by product and location | [Verified badge](https://help.thomasnet.com/supplier-badging) covers business information and capabilities, not every shipped part |
| [Reddit](https://www.reddit.com/r/AskEngineers/comments/1cezw90/) | Firsthand orders, returns, longevity, alternative stores; search r/AskEngineers, r/Machinists, r/robotics and r/3Dprinting | Prefer dated accounts with part numbers and actual outcomes; exclude ads and copied recommendations |
| [Practical Machinist](https://www.practicalmachinist.com/forum/) | Industrial sourcing and bearing experience | Match the application; a spindle-bearing report may not apply to a robot joint |
| [CNCzone / CNC-Arena](https://en.cncarena.com/forum/) | CNC components, motion parts, supplier experiences | Vendor posts and affiliate recommendations are leads, not independent verification |
| [Hobby-Machinist](https://www.hobby-machinist.com/) | Small orders and workshop purchasing experiences | Anecdotes need corroboration |
| [BBB profiles and complaints](https://www.bbb.org/faq/) | Business identity, complaint patterns, responses and resolution | [BBB ratings](https://www.bbb.org/about/overview-of-ratings) concern business conduct, not bearing quality; absence of a profile is unknown |
| [Trustpilot](https://www.trustpilot.com/) | Recent delivery, refunds and customer-service patterns | Match the exact domain; inspect dates, review volume and specifics instead of trusting the average alone |
| [ICANN Lookup / RDAP](https://lookup.icann.org/) | Registration dates and domain identity | Domain age is context, not proof of reliability; privacy protection is not a failure |
| [IAF CertSearch](https://www.iafcertsearch.org/) | Check claimed accredited ISO management-system certificates | Verify entity, site, scope and validity; a management-system certificate does not certify each bearing |

## Fast search order

1. Search the part number plus exact dimensions; use manufacturer distributor lists and Thomasnet to form a small shortlist.
2. Check own-domain product pages for dimensions, closure, manufacturer, visible price, pack/MOQ, US delivery and returns.
3. For each finalist, check one relevant forum and one business-review source; add another independent source when evidence conflicts.
4. Verify manufacturer authorization when claimed; check RDAP once per domain and certificates only when relevant claims need checking.
5. Compare total cost at BOM quantity, excluding shipping; trial candidates through the CLI and obtain user approval.

| Search purpose | Reusable query |
| --- | --- |
| Buyer experience | `site:reddit.com "supplier name" bearings` |
| Industrial experience | `site:practicalmachinist.com "supplier name"` |
| Delivery and returns | `"supplier.example" (delivery OR refund OR returns)` |
| Quality problems | `"supplier name" bearings (counterfeit OR failure OR wrong)` |
| Exact Arctos fit | `"688-2Z-W4" "8x16x4"` |
| Brand authorization | `site:nsk.com "supplier name"` |

## Keep the evidence reusable

Outside observations are saved per candidate in `supplier_candidates.json` and shown by discovery and `sourcing candidates`, separately from quote-outcome learning:

| Field | Record |
| --- | --- |
| Identity | Supplier name and domain; findings should identify the matching legal entity and region |
| Provenance | Source URL, publication date, date checked and source type |
| Observation | Short factual summary; firsthand order, seller claim or hearsay |
| Outcome | Delivery, correct specification, authenticity concern, return/refund or unknown |
| Corroboration | Independent supporting and conflicting reports; do not count reposts twice |
| Status | Observation signal: supporting, neutral or concern; missing forum/review coverage is flagged; approval and trial status stay separate |

The parser removes own-domain and duplicate source URLs and assigns the check date. Source text remains scout-reported, not independently authenticated by code. Older records display as unverified.

Keep durable evidence links and short summaries; refresh stale or conflicting findings before a new supplier decision. Missing reviews remain unknown. A single complaint or zero price wins must not cause automatic removal.

## Approved bearing shortlist

User approved these five on 2026-09-26 without trials: VXB, 123Bearing, Quality Bearings Online, Boca Bearings and Motion Industries. Approval records are in the candidate registry; supplier domains are in `suppliers.toml`. Approval does not mean the outside checks above were completed.
