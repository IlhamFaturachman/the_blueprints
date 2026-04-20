from market_discovery import fetch_orderbook_quote


def test_fetch_orderbook_quote_parses_list_of_list_levels(monkeypatch):
    """CLOB API returns levels as [[price, size], ...] format."""
    def fake_fetch(_url, **kwargs):
        return {
            "bids": [["0.45", "10"]],
            "asks": [["0.47", "12"]],
        }

    monkeypatch.setattr("market_discovery_internal.pricing.fetch_with_retry", fake_fetch)
    quote = fetch_orderbook_quote("tok-1")

    # Implementation returns {"bid": ..., "ask": ...}
    assert quote == {"bid": 0.45, "ask": 0.47}


def test_fetch_orderbook_quote_parses_list_levels(monkeypatch):
    def fake_fetch(_url, **kwargs):
        return {
            "bids": [["0.40", "5"]],
            "asks": [["0.44", "7"]],
        }

    monkeypatch.setattr("market_discovery_internal.pricing.fetch_with_retry", fake_fetch)
    quote = fetch_orderbook_quote("tok-2")

    assert quote == {"bid": 0.40, "ask": 0.44}


def test_fetch_orderbook_quote_returns_none_prices_when_empty(monkeypatch):
    """Empty orderbook returns bid=None, ask=None."""
    def fake_fetch(_url, **kwargs):
        return {"bids": [], "asks": []}

    monkeypatch.setattr("market_discovery_internal.pricing.fetch_with_retry", fake_fetch)
    quote = fetch_orderbook_quote("tok-3")
    assert quote == {"bid": None, "ask": None}


def test_fetch_orderbook_quote_returns_none_on_fetch_error(monkeypatch):
    def fake_fetch(_url, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("market_discovery_internal.pricing.fetch_with_retry", fake_fetch)
    assert fetch_orderbook_quote("tok-4") is None
