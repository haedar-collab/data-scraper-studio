"""Sumber siap pakai. URL situs pihak ketiga bisa berubah sewaktu-waktu — uji dulu lewat tombol Preview."""

PRESETS = [
    {
        "name": "Google News — IHSG",
        "type": "google_news",
        "category": "Saham BEI",
        "config": {"query": "IHSG", "lang": "id", "country": "ID", "max_items": 100},
    },
    {
        "name": "Google News — Keterbukaan Informasi Emiten",
        "type": "google_news",
        "category": "Saham BEI",
        "config": {"query": "emiten \"keterbukaan informasi\" OR dividen OR \"right issue\"",
                   "lang": "id", "country": "ID", "max_items": 100},
    },
    {
        "name": "Google News — Bitcoin & Crypto",
        "type": "google_news",
        "category": "Crypto",
        "config": {"query": "bitcoin OR ethereum OR crypto", "lang": "en", "country": "US", "max_items": 100},
    },
    {
        "name": "CNBC Indonesia — Market (RSS)",
        "type": "rss",
        "category": "Saham BEI",
        "config": {"url": "https://www.cnbcindonesia.com/market/rss", "max_items": 100},
    },
    {
        "name": "Cointelegraph (RSS)",
        "type": "rss",
        "category": "Crypto",
        "config": {"url": "https://cointelegraph.com/rss", "max_items": 100},
    },
    {
        "name": "CoinGecko — Top 100 Market",
        "type": "json_api",
        "category": "Crypto",
        "config": {
            "url": "https://api.coingecko.com/api/v3/coins/markets",
            "method": "GET",
            "params": {"vs_currency": "usd", "order": "market_cap_desc", "per_page": "100", "page": "1"},
            "items_path": "",
            "fields": {
                "title": "name", "symbol": "symbol", "price_usd": "current_price",
                "market_cap": "market_cap", "volume_24h": "total_volume",
                "change_24h_pct": "price_change_percentage_24h", "rank": "market_cap_rank",
            },
            "dedupe": False,
            "dedupe_by": "data",
            "respect_robots": False,
        },
    },
    {
        "name": "Books to Scrape — Harga (demo HTML)",
        "type": "html",
        "category": "Harga",
        "config": {
            "url": "https://books.toscrape.com/catalogue/page-{page}.html",
            "page_start": 1, "page_end": 3,
            "item_selector": "article.product_pod",
            "fields": {
                "title": "h3 a@title", "url": "h3 a@href", "price": "p.price_color",
                "stock": "p.instock.availability", "rating": "p.star-rating@class",
            },
            "numeric_fields": ["price"],
            "delay": 1.0,
            "dedupe": False,
            "dedupe_by": "data",
        },
    },
    {
        "name": "Open Data CKAN — contoh pencarian",
        "type": "ckan",
        "category": "Open Data",
        "config": {"base_url": "https://catalog.data.gov", "query": "inflation", "rows": 50},
    },
]
