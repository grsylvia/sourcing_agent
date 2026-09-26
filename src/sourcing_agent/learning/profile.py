"""Token profile: the per-conversation quantities an estimator needs, learned from logged conversations instead of guessed."""

# Profile record.
from dataclasses import dataclass

# Tokens in the fixed opening of every conversation.
from ..core.pricing import PROMPT_TOKENS

# Conversations needed before a learned profile replaces the assumed one.
MIN_CONVERSATIONS = 5
# Low and high percentiles (costs have a long tail, so high sits well above the median).
LOW_PCT, HIGH_PCT = 25, 90
# Key for the profile pooled over every model.
POOLED = "*"


# Low and high values for each quantity a conversation's usage is built from.
@dataclass
class Profile:
    # Web searches per BOM row per approved supplier.
    searches_per_row_supplier: tuple[float, float]
    # Page fetches per BOM row per approved supplier.
    fetches_per_row_supplier: tuple[float, float]
    # Context tokens added per web search.
    search_tokens: tuple[float, float]
    # Context tokens added per page fetch.
    fetch_tokens: tuple[float, float]
    # Share of gathered context billed as uncached input (the rest is cache writes).
    input_share: tuple[float, float]
    # Times gathered context is re-read from the prompt cache.
    rereads: tuple[float, float]
    # Output tokens (thinking, queries, submission) per BOM row.
    output_per_row: tuple[float, float]
    # Requests per conversation.
    requests: tuple[float, float]
    # Conversations learned from (0 for the assumed profile).
    n: int = 0


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile of a non-empty list."""
    # Sorted values.
    v = sorted(values)
    # Fractional index.
    k = (len(v) - 1) * pct / 100
    # Neighbours and interpolation.
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def spread(values: list[float]) -> tuple[float, float]:
    """(low, high) percentiles of the values."""
    # Low and high cases.
    return percentile(values, LOW_PCT), percentile(values, HIGH_PCT)


def step_tokens(points: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Tokens per search and per fetch from (searches, fetches, gathered tokens) by least squares through the origin."""
    # Normal-equation sums.
    ss = sum(s * s for s, _, _ in points)
    sf = sum(s * f for s, f, _ in points)
    ff = sum(f * f for _, f, _ in points)
    sy = sum(s * y for s, _, y in points)
    fy = sum(f * y for _, f, y in points)
    # Determinant of the 2×2 system.
    det = ss * ff - sf * sf
    # Solve only when searches and fetches vary independently enough (r² < 0.99); otherwise the split is noise.
    if det > 0.01 * ss * ff:
        a, b = (sy * ff - fy * sf) / det, (fy * ss - sy * sf) / det
        if a > 0 and b > 0:
            return a, b
    # Otherwise one shared rate per step.
    steps = sum(s + f for s, f, _ in points)
    rate = sum(y for _, _, y in points) / steps if steps else 0.0
    return rate, rate


def learn_profile(conversations: list[dict]) -> Profile | None:
    """Learn a profile from completed conversations; None with fewer than MIN_CONVERSATIONS."""
    # Conversations that submitted and used the API.
    done = [c for c in conversations if c["outcome"] == "submitted" and c["usage"]["requests"] and c["rows"] and c["suppliers"]]
    # Too little data.
    if len(done) < MIN_CONVERSATIONS:
        return None
    # Per-conversation quantities.
    sprs, fprs, shares, rereads, out_row, reqs, steps = [], [], [], [], [], [], []
    for c in done:
        u = c["usage"]
        # Row-supplier pairs searched.
        pairs = c["rows"] * c["suppliers"]
        sprs.append(u["web_searches"] / pairs)
        fprs.append(u["web_fetches"] / pairs)
        # Tokens that entered context beyond the fixed opening.
        gathered = max(1.0, u["input_tokens"] + u["cache_write_tokens"] - PROMPT_TOKENS)
        shares.append(u["input_tokens"] / (u["input_tokens"] + u["cache_write_tokens"]) if u["input_tokens"] + u["cache_write_tokens"] else 1.0)
        # Re-reads of gathered context (opening re-reads per extra request removed).
        rereads.append(max(0.0, u["cache_read_tokens"] - PROMPT_TOKENS * (u["requests"] - 1)) / gathered)
        out_row.append(u["output_tokens"] / c["rows"])
        reqs.append(u["requests"])
        steps.append((u["web_searches"], u["web_fetches"], gathered))
    # Tokens per search and per fetch.
    per_search, per_fetch = step_tokens(steps)
    # The learned profile.
    return Profile(
        searches_per_row_supplier=spread(sprs),
        fetches_per_row_supplier=spread(fprs),
        search_tokens=(per_search, per_search),
        fetch_tokens=(per_fetch, per_fetch),
        input_share=spread(shares),
        rereads=spread(rereads),
        output_per_row=spread(out_row),
        requests=spread(reqs),
        n=len(done),
    )


def conversations_of(records: list[dict]) -> list[dict]:
    """Every logged conversation, tagged with its pass's model (older records have none)."""
    # Flatten, keeping the model for per-model profiles.
    return [c | {"model": r["model"]} for r in records for c in r.get("conversations") or []]


def learn_profiles(records: list[dict]) -> dict[str, Profile]:
    """Learned profiles per model with enough data, plus the pooled profile under POOLED."""
    # All logged conversations.
    convs = conversations_of(records)
    # One profile per model, and one over everything.
    candidates = {m: learn_profile([c for c in convs if c["model"] == m]) for m in {c["model"] for c in convs}}
    candidates[POOLED] = learn_profile(convs)
    # Only the ones with enough data.
    return {k: p for k, p in candidates.items() if p is not None}


def pick_profile(profiles: dict[str, Profile], model: str, fallback: Profile) -> Profile:
    """The model's own profile, else the pooled one, else the fallback (assumed) profile."""
    # Most specific first.
    return profiles.get(model) or profiles.get(POOLED) or fallback
