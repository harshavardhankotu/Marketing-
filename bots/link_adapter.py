"""
Multi-network affiliate link adapter — one interface across stores.

Zero-cost design: pure URL transforms driven by your own affiliate IDs.
No network calls, no paid converters.

Supported stores:
    * amazon.in   -> tag=<AMAZON_ASSOCIATE_TAG>
    * flipkart.com/dl.flipkart.com -> affid=<FLIPKART_AFFID>
    * myntra.com  -> aff_id=<MYNTRA_AFF_ID>  (Myntra program param)

Unknown domains pass through untouched (the operator may have pasted an
already-converted link). Every transform is idempotent — running it twice
never duplicates parameters.
"""

import os
import sys
import urllib.parse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from config import (
        AMAZON_ASSOCIATE_TAG, FLIPKART_AFFID, MYNTRA_AFF_ID,
    )
except ImportError:
    AMAZON_ASSOCIATE_TAG = ""
    FLIPKART_AFFID = ""
    MYNTRA_AFF_ID = ""

# Store registry: domain match -> query parameter carrying the ID.
STORE_PARAMS = {
    "amazon": ("amazon.in", "tag", lambda: AMAZON_ASSOCIATE_TAG),
    "flipkart": ("flipkart.com", "affid", lambda: FLIPKART_AFFID),
    "myntra": ("myntra.com", "aff_id", lambda: MYNTRA_AFF_ID),
}


def detect_store(url):
    """Return 'amazon' | 'flipkart' | 'myntra' | None for a raw URL."""
    try:
        host = urllib.parse.urlparse(url or "").netloc.lower()
        host_no_port = host.split(":")[0]
        for store, (domain, _param, _get_id) in STORE_PARAMS.items():
            if host_no_port == domain or host_no_port.endswith("." + domain):
                return store
    except Exception:
        pass
    return None


def _configured(store):
    _, _, get_id = STORE_PARAMS[store]
    try:
        value = get_id()
        return bool(value) and "your_" not in str(value).lower() and str(value) != ""
    except Exception:
        return False


def build_link(url):
    """
    Attach the correct affiliate ID for the detected store.

    Returns (converted_url:str, store:str|None). Unrecognized hosts or
    missing IDs return the original URL unchanged — never break a deal link.
    """
    if not url:
        return url, None
    store = detect_store(url)
    if not store or not _configured(store):
        return url, store

    try:
        parts = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        _, param, get_id = STORE_PARAMS[store]
        affiliate_id = str(get_id())

        # Idempotent: replace any existing value for this param.
        query = [(k, v) for (k, v) in query if k.lower() != param]
        query.append((param, affiliate_id))

        new_query = urllib.parse.urlencode(query)
        converted = urllib.parse.urlunparse(parts._replace(query=new_query))
        return converted, store
    except Exception:
        return url, store


def build_links_for_product(target_urls):
    """Batch helper: [(original, converted, store), ...] for many URLs."""
    out = []
    for url in target_urls:
        converted, store = build_link(url)
        out.append((url, converted, store))
    return out


if __name__ == "__main__":
    samples = [
        "https://www.amazon.in/dp/B0TEST",
        "https://dl.flipkart.com/dl/some/product",
        "https://www.myntra.com/tshirts/12345/buy",
        "https://example.org/not-a-store",
    ]
    for s in samples:
        print(s, "->", build_link(s))
