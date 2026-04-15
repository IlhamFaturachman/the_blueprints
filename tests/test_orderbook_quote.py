from market_discovery import fetch_orderbook_quote


def test_fetch_orderbook_quote_parses_dict_levels(monkeypatch):
    def fake_fetch(_url, params=None, max_retries=3):
        assert params == {"token_id": "tok-1"}
        assert max_retries == 2
        return {
            "bids": [{"price": "0.45", "size": "10"}],
            "asks": [{"price": "0.47", "size": "12"}],
        }

    monkeypatch.setattr("market_discovery.fetch_with_retry", fake_fetch)
    quote = fetch_orderbook_quote("tok-1")

    assert quote == {"best_bid": 0.45, "best_ask": 0.47}


def test_fetch_orderbook_quote_parses_list_levels(monkeypatch):
    def fake_fetch(_url, params=None, max_retries=3):
        return {
            "bids": [["0.40", "5"]],
            "asks": [["0.44", "7"]],
        }

    monkeypatch.setattr("market_discovery.fetch_with_retry", fake_fetch)
    quote = fetch_orderbook_quote("tok-2")

    assert quote == {"best_bid": 0.40, "best_ask": 0.44}


def test_fetch_orderbook_quote_returns_none_when_no_prices(monkeypatch):
    def fake_fetch(_url, params=None, max_retries=3):
        return {"bids": [], "asks": []}

    monkeypatch.setattr("market_discovery.fetch_with_retry", fake_fetch)
    assert fetch_orderbook_quote("tok-3") is None


def test_fetch_orderbook_quote_returns_none_on_fetch_error(monkeypatch):
    def fake_fetch(_url, params=None, max_retries=3):
        raise RuntimeError("boom")

    monkeypatch.setattr("market_discovery.fetch_with_retry", fake_fetch)
    assert fetch_orderbook_quote("tok-4") is None
