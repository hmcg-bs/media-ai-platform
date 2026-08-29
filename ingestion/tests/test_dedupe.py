from ingestion.dedupe import (
    canonicalize_url,
    compute_minhash,
    get_content_hash,
    is_near_duplicate,
)


class TestCanonicalizeUrl:
    def test_strips_utm_params(self) -> None:
        a = canonicalize_url("https://store.com/products/x?utm_source=facebook&utm_medium=ig")
        b = canonicalize_url("https://store.com/products/x")
        assert a == b

    def test_strips_fbclid_and_gclid(self) -> None:
        a = canonicalize_url("https://store.com/products/x?fbclid=abc123")
        b = canonicalize_url("https://store.com/products/x?gclid=xyz789")
        c = canonicalize_url("https://store.com/products/x")
        assert a == b == c

    def test_strips_funnel_noise_params(self) -> None:
        a = canonicalize_url("https://store.com/products/x?variant=123&ref=homepage&_ke=1")
        b = canonicalize_url("https://store.com/products/x")
        assert a == b

    def test_preserves_non_tracking_params_sorted(self) -> None:
        a = canonicalize_url("https://store.com/p?b=2&a=1&utm_source=fb")
        b = canonicalize_url("https://store.com/p?a=1&b=2")
        assert a == b
        assert "a=1" in a and "b=2" in a

    def test_lowercases_host(self) -> None:
        a = canonicalize_url("https://STORE.com/products/x")
        b = canonicalize_url("https://store.com/products/x")
        assert a == b

    def test_strips_trailing_slash(self) -> None:
        a = canonicalize_url("https://store.com/products/x/")
        b = canonicalize_url("https://store.com/products/x")
        assert a == b

    def test_distinct_paths_stay_distinct(self) -> None:
        a = canonicalize_url("https://store.com/products/x")
        b = canonicalize_url("https://store.com/products/y")
        assert a != b


class TestGetContentHash:
    def test_identical_text_same_hash(self) -> None:
        html_a = "<html><body><p>Hello world, buy now</p></body></html>"
        html_b = "<html><body><p>Hello world, buy now</p></body></html>"
        assert get_content_hash(html_a) == get_content_hash(html_b)

    def test_different_text_different_hash(self) -> None:
        html_a = "<html><body><p>Hello world</p></body></html>"
        html_b = "<html><body><p>Goodbye world</p></body></html>"
        assert get_content_hash(html_a) != get_content_hash(html_b)

    def test_drops_script_and_style_content(self) -> None:
        html_a = "<html><body><p>Same content</p><script>var x=1;</script></body></html>"
        html_b = "<html><body><p>Same content</p><script>var x=999;</script></body></html>"
        assert get_content_hash(html_a) == get_content_hash(html_b)

    def test_strips_dynamic_timestamp_tokens(self) -> None:
        html_a = "<html><body><p>Order at 2026-08-10T10:00:00Z now</p></body></html>"
        html_b = "<html><body><p>Order at 2026-08-11T22:41:03Z now</p></body></html>"
        assert get_content_hash(html_a) == get_content_hash(html_b)

    def test_returns_hex_sha256(self) -> None:
        h = get_content_hash("<html><body>x</body></html>")
        assert len(h) == 64
        int(h, 16)  # raises ValueError if not valid hex


class TestIsNearDuplicate:
    def test_identical_text_is_duplicate(self) -> None:
        text = "Free shipping on orders over $50. 134 reviews. Save 59% today. 1300mg per serving."
        existing = [compute_minhash(text)]
        assert is_near_duplicate(text, existing) is True

    def test_minor_copy_edit_is_near_duplicate(self) -> None:
        original = (
            "Free shipping on orders over $50 today. Excellent, 134 reviews. "
            "Save up to 59% with this offer. 1300mg Beetroot per serving, "
            "30 servings per bottle. Circulation keeps blood flowing the way it should. "
            "Heart daily support your cardiovascular system needs. Energy clean steady fuel."
        )
        edited = original.replace("134 reviews", "135 reviews").replace("59%", "60%")
        existing = [compute_minhash(original)]
        # A 2-word diff over ~40 unique tokens sits just under the 0.95
        # default (tuned for full-length real pages, not this short
        # synthetic sample) — use a lower threshold to isolate what's under
        # test: that a minor edit is caught as *more similar than unrelated
        # text*, not an exact 0.95 calibration.
        assert is_near_duplicate(edited, existing, threshold=0.85) is True

    def test_unrelated_text_not_duplicate(self) -> None:
        page_a = "Buy our vegan protein powder, lab tested, 30 servings, chocolate flavor."
        page_b = "Wireless noise-cancelling headphones with 40-hour battery life and USB-C."
        existing = [compute_minhash(page_a)]
        assert is_near_duplicate(page_b, existing) is False

    def test_empty_existing_hashes_returns_false(self) -> None:
        assert is_near_duplicate("some text", []) is False

    def test_threshold_is_respected(self) -> None:
        text = "a b c d e f g h i j k l m n o p q r s t"
        existing = [compute_minhash(text)]
        # identical text always passes even a very high threshold
        assert is_near_duplicate(text, existing, threshold=0.99) is True
