import pytest

from core import scrapers as S
from core.scrapers import ScrapeError, run_scraper


@pytest.mark.parametrize("raw,expected", [
    ("Rp 1.250.000", 1250000.0), ("£51.77", 51.77), ("$1,234.56", 1234.56), ("1.234,5", 1234.5),
    ("0.125", 0.125), ("12,5", 12.5), ("-3.5%", -3.5), ("(1,000)", -1000.0), ("abc", None), (None, None), (7, 7.0),
])
def test_parse_number(raw, expected):
    assert S.parse_number(raw) == expected


def test_get_path_and_flatten():
    obj = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert S.get_path(obj, "a.b.1.c") == 2
    assert S.get_path(obj, "a.x.y") is None
    assert S.get_path(obj, "a.b.9") is None
    assert S.flatten({"a": {"b": 1}, "l": [1, 2]}) == {"a.b": 1, "l": "[1, 2]"}


def test_rss(server):
    items = run_scraper("rss", {"url": f"{server}/feed.xml"})
    assert [i["title"] for i in items] == ["IHSG naik 1%", "Bitcoin turun"]
    first = items[0]["data"]
    assert first["summary"] == "Asing net buy"
    assert first["publisher"] == "Kontan"
    assert first["published"] == "2026-11-13 09:00:00"  # 02:00 GMT → 09:00 WIB


def test_rss_invalid_feed(server):
    assert run_scraper("rss", {"url": f"{server}/notfeed.html"}) == []


def test_keywords_filter(server):
    assert len(run_scraper("rss", {"url": f"{server}/feed.xml", "keywords": ["ihsg"]})) == 1
    assert len(run_scraper("rss", {"url": f"{server}/feed.xml", "exclude_keywords": ["bitcoin"]})) == 1


def test_google_news_builds_url(monkeypatch):
    seen = {}
    monkeypatch.setattr(S, "scrape_rss", lambda cfg, session: seen.update(cfg) or [])
    S.scrape_google_news({"query": "BBCA dividen", "lang": "id", "country": "ID"}, None)
    assert seen["url"] == "https://news.google.com/rss/search?q=BBCA+dividen&hl=id&gl=ID&ceid=ID:id"
    with pytest.raises(ScrapeError):
        S.scrape_google_news({"query": " "}, None)


HTML_CFG = {"item_selector": "article.p", "fields": {"title": "h3 a@title", "url": "h3 a@href", "price": ".price"},
            "numeric_fields": ["price"], "delay": 0}


def test_html_pagination_stops_on_404(server):
    items = run_scraper("html", {**HTML_CFG, "url": f"{server}/page-{{page}}.html", "page_start": 1, "page_end": 5})
    assert [i["data"]["price_num"] for i in items] == [1250000.0, 51.77, 1234.5]
    assert items[0]["url"] == f"{server}/item/1"


def test_html_selector_not_found(server):
    with pytest.raises(ScrapeError, match="tidak menemukan"):
        run_scraper("html", {**HTML_CFG, "url": f"{server}/page-1.html", "item_selector": "div.none"})


def test_html_first_page_404_is_error(server):
    with pytest.raises(ScrapeError, match="HTTP 404"):
        run_scraper("html", {**HTML_CFG, "url": f"{server}/missing.html"})


def test_robots_respected(server):
    S._robots_cache.clear()
    with pytest.raises(ScrapeError, match="robots"):
        run_scraper("html", {**HTML_CFG, "url": f"{server}/secret/page.html"})
    items = run_scraper("html", {**HTML_CFG, "url": f"{server}/secret/page.html", "respect_robots": False,
                                 "fields": {"title": "h3"}})
    assert items[0]["title"] == "x"


def test_response_size_cap(server, monkeypatch):
    monkeypatch.setattr(S, "MAX_BYTES", 50)
    with pytest.raises(ScrapeError, match="lebih dari"):
        run_scraper("rss", {"url": f"{server}/feed.xml"})


def test_json_api(server):
    items = run_scraper("json_api", {"url": f"{server}/api.json", "items_path": "data.items",
                                     "fields": {"title": "name", "usd": "p.usd"}})
    assert [(i["title"], i["data"]["usd"]) for i in items] == [("BTC", 90000), ("ETH", 3000)]
    flat = run_scraper("json_api", {"url": f"{server}/api.json", "items_path": "data.items", "title_field": "name"})
    assert flat[0]["data"]["p.usd"] == 90000 and flat[0]["title"] == "BTC"
    with pytest.raises(ScrapeError, match="tidak mengarah"):
        run_scraper("json_api", {"url": f"{server}/api.json", "items_path": "data.nope"})


def test_ckan(server):
    items = run_scraper("ckan", {"base_url": f"{server}/ckan", "query": "inflasi"})
    d = items[0]["data"]
    assert items[0]["url"].endswith("/dataset/inflasi-2026")
    assert d["formats"] == "CSV, XLSX" and d["organization"] == "BPS" and d["notes"] == "Data inflasi"


def test_dedupe_fingerprint():
    a = S.normalize([{"title": "x", "url": "u", "price": 1}], {})
    b = S.normalize([{"title": "x", "url": "u", "price": 2}], {})
    c = S.normalize([{"title": "x", "url": "u", "price": 2}], {"dedupe_by": "data"})
    assert a[0]["fingerprint"] == b[0]["fingerprint"] != c[0]["fingerprint"]


def test_unknown_type():
    with pytest.raises(ScrapeError):
        run_scraper("nope", {})
