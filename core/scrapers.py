"""Mesin scraping: RSS, Google News, HTML (CSS selector), JSON API, dan portal CKAN (open data)."""
from __future__ import annotations

import hashlib
import json
import re
import os
import time
import warnings
from calendar import timegm
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import robotparser
from urllib.parse import quote_plus, urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
TZ = os.environ.get("SCRAPER_TZ", "Asia/Jakarta")
MAX_BYTES = 15 * 1024 * 1024      # batas ukuran respons per request
ROBOTS_TTL = 6 * 3600              # cache robots.txt 6 jam

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 DataScraperStudio/1.0"
)


class ScrapeError(Exception):
    """Kesalahan yang pesannya aman ditampilkan ke pengguna."""


# ---------------- HTTP ----------------
def make_session(cfg: dict) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "POST"]))
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({
        "User-Agent": cfg.get("user_agent") or DEFAULT_UA,
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    })
    s.headers.update(cfg.get("headers") or {})
    return s


_robots_cache: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}


def robots_allows(session: requests.Session, url: str) -> bool:
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    cached = _robots_cache.get(root)
    if cached is None or time.time() - cached[0] > ROBOTS_TTL:
        rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
        try:
            r = session.get(root + "/robots.txt", timeout=10)
            if r.status_code >= 400:
                rp = None
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots_cache[root] = (time.time(), rp)
    rp = _robots_cache[root][1]
    return True if rp is None else rp.can_fetch(session.headers.get("User-Agent", "*"), url)


def fetch(session: requests.Session, url: str, cfg: dict, method: str = "GET", **kw) -> requests.Response:
    if not url:
        raise ScrapeError("URL kosong.")
    if cfg.get("respect_robots", True) and not robots_allows(session, url):
        raise ScrapeError(f"Diblokir robots.txt: {url} (matikan 'Patuhi robots.txt' hanya jika kamu punya izin).")
    try:
        r = session.request(method, url, timeout=float(cfg.get("timeout", 20)), stream=True, **kw)
        if r.status_code >= 400:
            r.close()
            hint = " (situs menolak akses; sering terjadi pada IP server cloud)" if r.status_code in (401, 403) else ""
            raise ScrapeError(f"HTTP {r.status_code} dari {url}{hint}")
        buf, size = [], 0
        for chunk in r.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                r.close()
                raise ScrapeError(f"Respons dari {url} lebih dari {MAX_BYTES // 1024 // 1024} MB, dihentikan.")
            buf.append(chunk)
        r._content = b"".join(buf)
    except requests.RequestException as e:
        raise ScrapeError(f"Gagal mengakses {url}: {e}") from e
    return r


# ---------------- Helpers ----------------
def clean_text(html: str | None) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True) if "<" in html else html
    return re.sub(r"\s+", " ", text).strip()


def struct_to_iso(st) -> str:
    if not st:
        return ""
    try:
        return (datetime.fromtimestamp(timegm(st), tz=timezone.utc).astimezone(ZoneInfo(TZ))
                .strftime("%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return ""


def parse_number(value: Any) -> float | None:
    """Ubah teks harga/angka ke float. Contoh: 'Rp 1.234.500' -> 1234500, '£51.77' -> 51.77, '$1,234.5' -> 1234.5."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    neg = s.startswith("-") or s.startswith("(")
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts[-1]) == 3 and parts[0] not in ("", "0")):
            s = s.replace(sep, "")          # pemisah ribuan
        else:
            s = s.replace(sep, ".")         # pemisah desimal
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def get_path(obj: Any, path: str) -> Any:
    """Ambil nilai bertingkat dengan notasi titik: 'data.items', 'quote.0.price'."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            elif isinstance(v, list):
                out[key] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                out[key] = v
    else:
        out[prefix or "value"] = d
    return out


def extract(node, spec: str, base_url: str) -> str:
    """spec: 'css' -> teks; 'css@attr' -> atribut; '@href' -> atribut elemen item itu sendiri."""
    spec = (spec or "").strip()
    attr = None
    sel = spec
    if "@" in spec:
        sel, attr = spec.rsplit("@", 1)
        sel, attr = sel.strip(), attr.strip()
    el = node.select_one(sel) if sel else node
    if el is None:
        return ""
    if attr:
        v = el.get(attr, "")
        if isinstance(v, list):
            v = " ".join(v)
        v = str(v).strip()
        if v and attr in ("href", "src", "data-src", "data-href"):
            v = urljoin(base_url, v)
        return v
    return el.get_text(" ", strip=True)


# ---------------- Scrapers ----------------
def scrape_rss(cfg: dict, session: requests.Session) -> list[dict]:
    r = fetch(session, cfg.get("url", ""), cfg)
    feed = feedparser.parse(r.content)
    if not feed.entries:
        if feed.bozo:
            raise ScrapeError(f"Bukan feed RSS/Atom yang valid: {feed.bozo_exception}")
        return []
    out = []
    for e in feed.entries[: int(cfg.get("max_items", 100))]:
        src = e.get("source") or {}
        out.append({
            "title": clean_text(e.get("title", "")),
            "url": e.get("link", ""),
            "published": struct_to_iso(e.get("published_parsed") or e.get("updated_parsed")),
            "summary": clean_text(e.get("summary", ""))[:2000],
            "author": e.get("author", ""),
            "publisher": src.get("title", "") if isinstance(src, dict) else "",
            "feed": feed.feed.get("title", ""),
        })
    return out


def scrape_google_news(cfg: dict, session: requests.Session) -> list[dict]:
    q = (cfg.get("query") or "").strip()
    if not q:
        raise ScrapeError("Kata kunci pencarian kosong.")
    lang, country = cfg.get("lang", "id"), cfg.get("country", "ID")
    url = (f"https://news.google.com/rss/search?q={quote_plus(q)}"
           f"&hl={lang}&gl={country}&ceid={country}:{lang}")
    return scrape_rss({**cfg, "url": url, "respect_robots": False}, session)


def _page_urls(cfg: dict) -> list[str]:
    url = cfg.get("url", "")
    if "{page}" in url:
        start, end = int(cfg.get("page_start", 1)), int(cfg.get("page_end", 1))
        return [url.replace("{page}", str(p)) for p in range(start, max(start, end) + 1)]
    return [url]


def scrape_html(cfg: dict, session: requests.Session) -> list[dict]:
    item_sel = (cfg.get("item_selector") or "").strip()
    fields: dict[str, str] = cfg.get("fields") or {}
    if not item_sel or not fields:
        raise ScrapeError("item_selector dan minimal satu field wajib diisi.")
    numeric = cfg.get("numeric_fields") or []
    delay = float(cfg.get("delay", 1.5))
    max_items = int(cfg.get("max_items", 500))
    out: list[dict] = []
    for i, page_url in enumerate(_page_urls(cfg)):
        if i:
            time.sleep(delay)
        try:
            r = fetch(session, page_url, cfg)
        except ScrapeError:
            if i == 0:
                raise
            break  # halaman lanjutan tidak ada (mis. 404) -> simpan yang sudah terkumpul
        soup = BeautifulSoup(r.content, "lxml")
        nodes = soup.select(item_sel)
        if not nodes:
            if i == 0:
                raise ScrapeError(f"Selector '{item_sel}' tidak menemukan elemen di {page_url}. "
                                  "Bisa jadi halaman dirender JavaScript atau selector salah.")
            break  # halaman terakhir terlewati
        for n in nodes:
            rec = {name: extract(n, spec, page_url) for name, spec in fields.items()}
            if not any(rec.values()):
                continue
            for nf in numeric:
                rec[f"{nf}_num"] = parse_number(rec.get(nf))
            rec["page_url"] = page_url
            out.append(rec)
            if len(out) >= max_items:
                return out
    return out


def scrape_json(cfg: dict, session: requests.Session) -> list[dict]:
    method = (cfg.get("method") or "GET").upper()
    kw: dict[str, Any] = {}
    if cfg.get("params"):
        kw["params"] = cfg["params"]
    if method == "POST" and cfg.get("body"):
        kw["json"] = cfg["body"]
    r = fetch(session, cfg.get("url", ""), cfg, method=method, **kw)
    try:
        payload = r.json()
    except ValueError as e:
        raise ScrapeError("Respons bukan JSON yang valid.") from e
    records = get_path(payload, cfg.get("items_path", ""))
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ScrapeError(f"items_path '{cfg.get('items_path', '')}' tidak mengarah ke list data.")
    fields: dict[str, str] = cfg.get("fields") or {}
    out = []
    for rec in records[: int(cfg.get("max_items", 1000))]:
        row = {k: get_path(rec, p) for k, p in fields.items()} if fields else flatten(rec)
        out.append(row)
    return out


def scrape_ckan(cfg: dict, session: requests.Session) -> list[dict]:
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise ScrapeError("Base URL portal kosong.")
    params = {"q": cfg.get("query", ""), "rows": int(cfg.get("rows", 50)), "sort": "metadata_modified desc"}
    r = fetch(session, f"{base}/api/3/action/package_search", cfg, params=params)
    try:
        payload = r.json()
    except ValueError as e:
        raise ScrapeError("Portal tidak mengembalikan JSON. Pastikan portal memakai CKAN API.") from e
    if not payload.get("success"):
        raise ScrapeError(f"CKAN API gagal: {payload.get('error')}")
    out = []
    for p in payload["result"]["results"]:
        org = p.get("organization") or {}
        res = p.get("resources") or []
        out.append({
            "title": p.get("title", ""),
            "url": f"{base}/dataset/{p.get('name', '')}",
            "published": (p.get("metadata_modified") or "")[:19].replace("T", " "),
            "organization": org.get("title", ""),
            "notes": clean_text(p.get("notes", ""))[:1500],
            "num_resources": len(res),
            "formats": ", ".join(sorted({(x.get("format") or "").upper() for x in res if x.get("format")})),
            "resource_urls": " | ".join(x.get("url", "") for x in res[:10]),
        })
    return out


SOURCE_TYPES: dict[str, dict[str, Any]] = {
    "rss": {"label": "RSS / Atom Feed", "fn": scrape_rss,
            "help": "Portal berita, blog, rilis pers. Paling stabil & ringan."},
    "google_news": {"label": "Google News (pencarian)", "fn": scrape_google_news,
                    "help": "Cari berita berdasarkan kata kunci, misal 'IHSG', 'BBCA', 'Bitcoin'."},
    "html": {"label": "Halaman Web (CSS Selector)", "fn": scrape_html,
             "help": "Untuk situs harga, katalog, direktori perusahaan. Halaman yang dirender JavaScript tidak didukung."},
    "json_api": {"label": "JSON API", "fn": scrape_json,
                 "help": "Endpoint API publik (CoinGecko, Binance, dsb)."},
    "ckan": {"label": "Portal Open Data (CKAN)", "fn": scrape_ckan,
             "help": "Cari dataset di portal open data berbasis CKAN (mis. data.humdata.org, open.canada.ca/data). Catatan: data.go.id tidak menyediakan CKAN API — pakai tipe JSON API/HTML untuk situs tersebut."},
}


def normalize(raw: list[dict], cfg: dict) -> list[dict]:
    title_key = cfg.get("title_field") or "title"
    url_key = cfg.get("url_field") or "url"
    include = [k.lower() for k in cfg.get("keywords", []) if k.strip()]
    exclude = [k.lower() for k in cfg.get("exclude_keywords", []) if k.strip()]
    items = []
    for d in raw:
        blob = json.dumps(d, ensure_ascii=False, default=str).lower()
        if include and not any(k in blob for k in include):
            continue
        if exclude and any(k in blob for k in exclude):
            continue
        title = str(d.get(title_key) or "")[:500]
        url = str(d.get(url_key) or "")
        basis = url if url and cfg.get("dedupe_by", "url") == "url" else json.dumps(d, sort_keys=True, default=str)
        items.append({
            "title": title,
            "url": url,
            "published": str(d.get("published") or ""),
            "fingerprint": hashlib.sha1(basis.encode("utf-8")).hexdigest(),
            "data": d,
        })
    return items


def run_scraper(type_: str, cfg: dict) -> list[dict]:
    if type_ not in SOURCE_TYPES:
        raise ScrapeError(f"Tipe sumber tidak dikenal: {type_}")
    fn: Callable = SOURCE_TYPES[type_]["fn"]
    with make_session(cfg) as session:
        raw = fn(cfg, session)
    return normalize(raw, cfg)
