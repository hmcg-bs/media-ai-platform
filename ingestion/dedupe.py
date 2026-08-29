"""3-tier deduplication for advertorial funnel pages (Phase 0.5e, Module A).

Failed URLs from Phase 0.5d average 4.0 ads per unique URL — heavily-promoted
advertorial funnels get many ad-variant creatives pointing at the same
landing page, sometimes via distinct tracker URLs that don't share query
params (see track.tryrosabella.com's per-ad path-UUID pattern). Three tiers,
cheapest-first:

1. canonicalize_url() — strip ad-tracking/funnel query params, catches exact
   duplicates that differ only by tracking noise. Pre-fetch, free.
2. get_content_hash() — SHA-256 of normalized visible text, post-fetch.
   Catches byte-for-byte-after-normalization duplicates cheaply.
3. is_near_duplicate() — MinHash Jaccard similarity, post-fetch. Catches
   near-duplicates (minor copy edits, dynamic tracker path segments) that a
   canonical URL or exact hash won't.

MinHash is implemented in-house (below) rather than via the ``datasketch``
package: confirmed live that ``datasketch``'s ``__init__.py`` eagerly imports
its entire public surface — including ``MinHashLSH``, which we never use —
and that pulls in ``scipy.integrate`` at import time. ``import scipy`` alone
reproduced a multi-minute hang on this machine (macOS Gatekeeper/XProtect
scanning scipy's many ad-hoc-signed compiled binaries on first load). We only
ever need plain ``MinHash`` + Jaccard comparison, so a ~40-line
standard-library implementation removes the dependency (and the scipy pull)
entirely rather than working around the import cost.
"""

from __future__ import annotations

import hashlib
import random
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

# Standard MinHash universal-hashing constants (same scheme datasketch itself
# uses): hash values live in [0, _MAX_HASH]; permutation coefficients are
# combined mod a large Mersenne prime.
_MAX_HASH = (1 << 32) - 1
_MERSENNE_PRIME = (1 << 61) - 1
_DEFAULT_SEED = 1  # fixed so every MinHash instance shares the same permutation family


class MinHash:
    """Minimal MinHash signature: num_perm independent hash functions via
    universal hashing (h_i(x) = (a_i*x + b_i) mod prime), each tracking a
    running minimum over the shingles seen via update(). jaccard() estimates
    Jaccard similarity as the fraction of matching minimum slots between two
    signatures built with the same num_perm/seed."""

    def __init__(self, num_perm: int = 128, seed: int = _DEFAULT_SEED):
        self.num_perm = num_perm
        rng = random.Random(seed)
        self._a = [rng.randint(1, _MERSENNE_PRIME - 1) for _ in range(num_perm)]
        self._b = [rng.randint(0, _MERSENNE_PRIME - 1) for _ in range(num_perm)]
        self.hashvalues = [_MAX_HASH] * num_perm

    def update(self, data: bytes) -> None:
        h = int(hashlib.sha1(data).hexdigest(), 16) & _MAX_HASH
        for i in range(self.num_perm):
            permuted = ((self._a[i] * h + self._b[i]) % _MERSENNE_PRIME) & _MAX_HASH
            if permuted < self.hashvalues[i]:
                self.hashvalues[i] = permuted

    def jaccard(self, other: MinHash) -> float:
        if self.num_perm != other.num_perm:
            raise ValueError("num_perm mismatch between MinHash instances")
        matches = sum(1 for a, b in zip(self.hashvalues, other.hashvalues) if a == b)
        return matches / self.num_perm

# Ad tracking params (utm_*handled separately via prefix match) + funnel/ecom
# noise params that vary per ad/session but don't change the underlying page.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "ttclid",
        "ad_id",
        "adset_id",
        "campaign_id",
        "placement",
        "site_source_name",
        "variant",
        "_ke",
        "_pos",
        "_ss",
        "preview_theme_id",
        "affiliate_id",
        "ref",
    }
)


def _is_tracking_param(key: str) -> bool:
    key_lower = key.lower()
    return key_lower in _TRACKING_PARAMS or key_lower.startswith(_TRACKING_PARAM_PREFIXES)


def canonicalize_url(url: str) -> str:
    """Strip tracking/funnel query params, lowercase host, sort remaining
    params, drop trailing slash. Two URLs differing only by utm_source or
    fbclid canonicalize to the same string."""
    parts = urlsplit(url)
    all_params = parse_qsl(parts.query, keep_blank_values=True)
    kept_params = sorted((k, v) for k, v in all_params if not _is_tracking_param(k))
    query = urlencode(kept_params)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


_DROP_TAGS = ("script", "style", "svg", "noscript", "iframe")


def get_content_hash(html: str) -> str:
    """SHA-256 of the page's normalized visible text, after dropping
    non-content tags. Two fetches of the same page (e.g. re-scraped later,
    or served to a different tracker URL) hash identically."""
    import hashlib
    import re

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    # Strip likely dynamic tokens (long hex/base64-ish ids, ISO timestamps)
    # that would otherwise make two fetches of the same page hash differently.
    text = re.sub(r"\b[0-9a-f]{16,}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_minhash(text: str, num_perm: int = 128) -> MinHash:
    """MinHash signature over whitespace-tokenized shingles of `text`."""
    mh = MinHash(num_perm=num_perm)
    for token in text.split():
        mh.update(token.encode("utf-8"))
    return mh


def is_near_duplicate(
    text: str,
    existing_hashes: list[MinHash],
    *,
    threshold: float = 0.95,
    num_perm: int = 128,
) -> bool:
    """True if `text`'s MinHash similarity to any signature in
    `existing_hashes` exceeds `threshold`. Catches near-duplicate pages
    (minor copy edits, different tracker-path UUID) that canonicalize_url
    and get_content_hash won't."""
    if not existing_hashes:
        return False
    candidate = compute_minhash(text, num_perm=num_perm)
    return any(candidate.jaccard(existing) >= threshold for existing in existing_hashes)
