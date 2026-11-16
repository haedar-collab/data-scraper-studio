import functools
import http.server
import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = {
    "feed.xml": """<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title>
<item><title>IHSG naik 1%</title><link>http://x/1</link><pubDate>Fri, 13 Nov 2026 02:00:00 GMT</pubDate>
<description>&lt;p&gt;Asing net buy&lt;/p&gt;</description><source url="http://pub">Kontan</source></item>
<item><title>Bitcoin turun</title><link>http://x/2</link></item></channel></rss>""",
    "page-1.html": """<html><body>
<article class="p"><h3><a href="/item/1" title="Saham A">A</a></h3><p class="price">Rp 1.250.000</p></article>
<article class="p"><h3><a href="/item/2" title="Buku B">B</a></h3><p class="price">£51.77</p></article></body></html>""",
    "page-2.html": """<html><body>
<article class="p"><h3><a href="/item/3" title="Koin C">C</a></h3><p class="price">$1,234.50</p></article></body></html>""",
    "api.json": json.dumps({"data": {"items": [{"name": "BTC", "p": {"usd": 90000}}, {"name": "ETH", "p": {"usd": 3000}}]}}),
    "ckan/api/3/action/package_search": json.dumps({"success": True, "result": {"count": 1, "results": [
        {"title": "Inflasi 2026", "name": "inflasi-2026", "metadata_modified": "2026-11-01T10:00:00.123",
         "organization": {"title": "BPS"}, "notes": "<p>Data inflasi</p>",
         "resources": [{"format": "csv", "url": "http://r/1.csv"}, {"format": "xlsx", "url": "http://r/2.xlsx"}]}]}}),
    "robots.txt": "User-agent: *\nDisallow: /secret\n",
    "secret/page.html": "<html><article class='p'><h3>x</h3></article></html>",
    "notfeed.html": "<html><body>bukan feed</body></html>",
}


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    for name, body in FIXTURES.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _backends():
    out = ["sqlite"]
    if os.environ.get("TEST_DATABASE_URL"):
        out.append("postgres")
    return out


@pytest.fixture(params=_backends())
def database(request, tmp_path, monkeypatch):
    """Database bersih per tes, dijalankan untuk SQLite dan (bila tersedia) PostgreSQL."""
    from sqlalchemy import text

    from core import db
    if request.param == "sqlite":
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SCRAPER_DB", str(tmp_path / "t.db"))
    else:
        monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    db.init_db()
    if request.param == "postgres":
        with db.engine().begin() as c:
            c.execute(text("TRUNCATE items, runs, schedules, sources RESTART IDENTITY CASCADE"))
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    yield db
    db.engine().dispose()
