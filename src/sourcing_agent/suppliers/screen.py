"""Free screening: checks a scout's candidate without tokens (own-domain priced evidence, HTTPS, RDAP domain age)."""

# Domain ages.
import datetime
# RDAP replies.
import json
# RDAP lookups (free, no key).
import urllib.error
import urllib.request

# Domain helper.
from ..core.web import domain_allowed
# Candidate record.
from .scout import Candidate

# Priced product pages on its own domain a candidate must show.
MIN_EVIDENCE = 2
# Domains younger than this (years) are screened out.
MIN_DOMAIN_AGE_YEARS = 1
# Domains younger than this (years) are flagged.
CAUTION_DOMAIN_AGE_YEARS = 2


def rdap_age_years(domain: str, today: datetime.date) -> float | None:
    """Domain age in years from the free RDAP service, or None when unknown."""
    # Try the host, then its last two labels (the registrable name for most US domains).
    for name in dict.fromkeys([domain, ".".join(domain.split(".")[-2:])]):
        try:
            # rdap.org redirects to the registry's own RDAP server.
            request = urllib.request.Request(f"https://rdap.org/domain/{name}", headers={"Accept": "application/rdap+json"})
            with urllib.request.urlopen(request, timeout=10) as reply:
                data = json.load(reply)
        except (urllib.error.URLError, TimeoutError, ValueError):
            # Not found, unsupported TLD, or network error: try the next name.
            continue
        # Registration event, if present.
        for event in data.get("events", []):
            if event.get("eventAction") == "registration":
                born = datetime.date.fromisoformat(event["eventDate"][:10])
                return round((today - born).days / 365.25, 1)
    # Age unknown.
    return None


def screen(c: Candidate, age_years: float | None) -> Candidate:
    """Run the free checks and set the verdict and reasons."""
    # Priced pages actually on the candidate's own domain.
    own = [p for p in c.product_pages if domain_allowed(p["url"], [c.domain])]
    # Distinct evidence pages.
    c.evidence = len({p["url"] for p in own})
    # Evidence served over HTTPS.
    c.https = bool(own) and all(p["url"].lower().startswith("https://") for p in own)
    # Trust pages on the candidate's own domain.
    c.contact_found = bool(c.contact_url) and domain_allowed(c.contact_url, [c.domain])
    c.returns_found = bool(c.returns_url) and domain_allowed(c.returns_url, [c.domain])
    # Domain age from RDAP.
    c.domain_age_years = age_years
    # Checks a candidate must pass to be trialed.
    must = [
        (c.evidence >= MIN_EVIDENCE, f"fewer than {MIN_EVIDENCE} priced product pages on its own domain"),
        (c.https, "evidence pages not on HTTPS"),
        (c.prices_visible_without_login, "prices need a login"),
        (c.ships_to_us, "does not ship to the US"),
        (age_years is None or age_years >= MIN_DOMAIN_AGE_YEARS, f"domain under {MIN_DOMAIN_AGE_YEARS} year old"),
    ]
    # Caution flags that do not block a trial.
    flags = [
        (c.price_breaks_or_packs_shown, "no price breaks or pack sizes shown"),
        (c.contact_found, "no contact page found"),
        (c.returns_found, "no returns policy found"),
        (age_years is not None, "domain age unknown"),
        (age_years is None or age_years >= CAUTION_DOMAIN_AGE_YEARS, f"domain under {CAUTION_DOMAIN_AGE_YEARS} years old"),
    ]
    # Failed checks first, then flags.
    failed = [msg for ok, msg in must if not ok]
    c.reasons = failed + [msg for ok, msg in flags if not ok]
    # Trial only when every must-pass check passes.
    c.verdict = "screened_out" if failed else "trial"
    return c
