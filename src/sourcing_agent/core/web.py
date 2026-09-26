"""Domain helpers for supplier URLs."""

# Host names from URLs.
from urllib.parse import urlparse


def host_of(url_or_domain: str) -> str:
    """Return the lowercase host without www. from a URL or bare domain."""
    # Accept bare domains as well as URLs.
    text = url_or_domain.strip().lower()
    # Parse the host.
    host = urlparse(text if "://" in text else f"https://{text}").hostname or ""
    # Drop the www. prefix.
    return host.removeprefix("www.")


def is_known(domain: str, known: set[str]) -> bool:
    """True when the domain is, or is under, one of the known domains."""
    # Exact or subdomain match.
    return any(domain == k or domain.endswith("." + k) for k in known)


def domain_allowed(url: str, domains: list[str]) -> bool:
    """True when the URL's host is one of the domains or a subdomain of one."""
    # Lowercase host from the URL, matched exactly or as a subdomain.
    return is_known((urlparse(url).hostname or "").lower(), set(domains))
