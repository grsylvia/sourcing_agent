"""Outside supplier evidence: source guide, normalized observations, and review flags."""

# Dates assigned by the application rather than the scout.
import datetime
# Validate and normalize source URLs.
from urllib.parse import urlsplit, urlunsplit

# Project-owned source guide.
from ..core import paths
# Report missing guidance before making paid requests.
from ..core.errors import InputError
# Exclude the supplier's own pages from outside evidence.
from ..core.web import host_of, is_known

# Supported outside source categories.
SOURCE_TYPES = ['manufacturer', 'directory', 'forum', 'business_review', 'domain_registry', 'certificate']
# Distinguish direct reports and authoritative records from weaker claims.
EVIDENCE_BASES = ['firsthand', 'official_record', 'seller_claim', 'hearsay']
# Report supporting, adverse, and inconclusive observations separately.
SIGNALS = ['supporting', 'concern', 'neutral']


def source_guide() -> str:
    """Load the maintained guide for every discovery prompt and estimate."""
    # Fail clearly instead of silently skipping the requested verification workflow.
    try:
        # Keep the Markdown as the single source of research instructions.
        guide = paths.SUPPLIER_SOURCES_PATH.read_text(encoding='utf-8').strip()
    # Convert file errors to the CLI's normal input error.
    except OSError as error:
        # A missing guide is a configuration error before any API calls.
        raise InputError(f'cannot read supplier source guide {paths.SUPPLIER_SOURCES_PATH}: {error}') from error
    # Empty guidance is also an input error.
    if not guide:
        # Stop before constructing an incomplete scout prompt.
        raise InputError(f'supplier source guide is empty: {paths.SUPPLIER_SOURCES_PATH}')
    # Return maintained source guidance verbatim.
    return guide


def normalize_evidence(items: list[dict], supplier_domain: str) -> list[dict]:
    """Keep distinct external HTTP(S) observations and stamp the date checked."""
    # First report per normalized URL, preserving any conflicting observations in its summary.
    kept = {}
    # The application knows when the evidence was collected.
    today = datetime.date.today().isoformat()
    # Inspect every reported outside observation.
    for item in items:
        # URL parsing can fail on malformed host syntax.
        try:
            # Remove fragments so repeated links count once.
            parsed = urlsplit(item.get('url', '').strip())
            # Reject non-web, credential-bearing, and self-hosted evidence URLs.
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or is_known(host_of(item['url']), {supplier_domain}):
                # Supplier testimonials do not establish outside verification.
                continue
        # Ignore malformed URL observations without discarding the candidate.
        except ValueError:
            # Unusable evidence cannot count toward coverage.
            continue
        # Require a real observation and recognized classifications.
        if not item.get('summary', '').strip() or item.get('source_type') not in SOURCE_TYPES or item.get('basis') not in EVIDENCE_BASES or item.get('signal') not in SIGNALS:
            # Incomplete observations remain unverified.
            continue
        # Standardize source publication dates, leaving unknown dates empty.
        try:
            # Accept only actual ISO calendar dates.
            published = datetime.date.fromisoformat(item.get('published_at', '')).isoformat()
        # Missing or malformed publication dates remain unknown.
        except (ValueError, TypeError):
            # Never invent a publication date.
            published = ''
        # Normalize host capitalization and remove fragments.
        url = urlunsplit(parsed._replace(netloc=parsed.netloc.lower(), fragment=''))
        # Retain one observation per exact source URL.
        if url not in kept:
            # Only persist the documented fields, with collection date assigned locally.
            kept[url] = {key: item[key] for key in ('source_type', 'basis', 'signal', 'summary')}
            # Attach normalized provenance.
            kept[url].update(url=url, published_at=published, checked_at=today)
        # A duplicate page must not hide a later adverse report.
        else:
            # Keep distinct findings from the same source together.
            if item['summary'] != kept[url]['summary']:
                # Preserve disagreement without counting the page twice.
                kept[url]['summary'] += ' | ' + item['summary']
            # Any reported concern remains visible for human review.
            if item['signal'] == 'concern':
                # Do not let a prior favorable observation suppress a concern.
                kept[url]['signal'] = 'concern'
            # Conflicting classifications cannot strengthen coverage.
            if item['basis'] != kept[url]['basis'] or item['source_type'] != kept[url]['source_type']:
                # Treat an inconsistently classified page as weaker evidence.
                kept[url]['basis'] = 'hearsay'
    # Persisted observations are scout reports, not independent authentication of their claims.
    return list(kept.values())


def evidence_flags(items: list[dict]) -> list[str]:
    """Flag missing outside coverage and reported concerns without rejecting a supplier."""
    # Self-promotion and hearsay do not satisfy independent coverage.
    independent = [e for e in items if e.get('basis') in ('firsthand', 'official_record')]
    # Forum and review coverage should come from distinct hosts.
    forums = {host_of(e['url']) for e in independent if e['source_type'] == 'forum'}
    # Business reviews complement forum accounts.
    reviews = {host_of(e['url']) for e in independent if e['source_type'] == 'business_review'}
    # Complete coverage does not imply the supplier is reliable.
    covered = any(forum != review for forum in forums for review in reviews)
    # Missing reports are unknown, never a supplier failure.
    flags = [] if covered else ['outside verification incomplete (forum + business review)']
    # Preserve adverse reports for human review without automatic rejection.
    if any(e.get('signal') == 'concern' for e in items):
        # An allegation alone cannot justify removal or approval refusal.
        flags.append('outside concern reported; review evidence')
    # Keep outside evidence separate from the priced-page screening gates.
    return flags
