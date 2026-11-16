"""Penyimpanan data: sumber, jadwal, riwayat run, dan item hasil scraping.

- Cloud  : set DATABASE_URL (PostgreSQL: Neon / Supabase / dsb.)
- Lokal  : tanpa DATABASE_URL → otomatis SQLite di data/scraper.db
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import bindparam, create_engine, event, inspect, text
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent.parent
TZ = os.environ.get("SCRAPER_TZ", "Asia/Jakarta")
_write_lock = threading.Lock()
_engine: Engine | None = None


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TZ)).replace(microsecond=0, tzinfo=None)


def now_iso() -> str:
    return now_dt().isoformat(sep=" ")


def clean_database_url(raw: str) -> str:
    """Bersihkan salinan dari HP/Neon: spasi, baris baru, kutip miring, awalan `psql '...'`."""
    url = (raw or "").strip().replace("\n", "").replace("\r", "")
    for q in ('"', "'", "\u201c", "\u201d", "\u2018", "\u2019", "`"):
        url = url.replace(q, "")
    i = url.find("postgres")
    if i > 0:
        url = url[i:]
    return url.split()[0] if url.split() else ""


def masked_url(raw: str) -> str:
    import re
    return re.sub(r"(://[^:/@]*:)[^@]*@", r"\1****@", clean_database_url(raw))


def password_hint(raw: str) -> str:
    from urllib.parse import unquote, urlsplit
    try:
        pw = unquote(urlsplit(clean_database_url(raw)).password or "")
    except ValueError:
        return "tidak terbaca"
    if not pw:
        return "**kosong** — password tidak ikut tersalin"
    if set(pw) <= {"*", "•", "x", "X"}:
        return "**masih berupa `****`** — kamu menyalin versi tersembunyi, klik ikon mata/Copy di Neon"
    return f"{len(pw)} karakter, diawali `{pw[:2]}…`"


def database_url() -> str:
    url = clean_database_url(os.environ.get("DATABASE_URL") or "")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url
    path = Path(os.environ.get("SCRAPER_DB", BASE_DIR / "data" / "scraper.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


_engine_url: str | None = None


def engine() -> Engine:
    """Engine dibuat ulang otomatis bila DATABASE_URL berubah (mis. Secrets diperbarui)."""
    global _engine, _engine_url
    url = database_url()
    if _engine is not None and url != _engine_url:
        _engine.dispose()
        _engine = None
    if _engine is None:
        _engine_url = url
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2, pool_recycle=300)
    return _engine


def is_postgres() -> bool:
    return engine().dialect.name == "postgresql"


def _schema() -> list[str]:
    pk = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        f"""CREATE TABLE IF NOT EXISTS sources (
            id {pk}, name TEXT UNIQUE NOT NULL, type TEXT NOT NULL, category TEXT DEFAULT '',
            config TEXT NOT NULL, enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS schedules (
            id {pk}, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            mode TEXT NOT NULL, value TEXT NOT NULL, enabled INTEGER DEFAULT 1,
            created_at TEXT, last_fired_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS runs (
            id {pk}, source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE, trigger TEXT,
            started_at TEXT, finished_at TEXT, status TEXT, items_found INTEGER DEFAULT 0,
            items_new INTEGER DEFAULT 0, message TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS items (
            id {pk}, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            run_id INTEGER, fingerprint TEXT NOT NULL, title TEXT, url TEXT, published TEXT,
            data TEXT NOT NULL, scraped_at TEXT, UNIQUE(source_id, fingerprint))""",
        "CREATE INDEX IF NOT EXISTS idx_items_source_time ON items(source_id, scraped_at)",
        "CREATE INDEX IF NOT EXISTS idx_runs_time ON runs(started_at)",
    ]


def init_db() -> None:
    with engine().begin() as c:
        for stmt in _schema():
            c.execute(text(stmt))
    # migrasi ringan dari versi lama
    cols = {col["name"] for col in inspect(engine()).get_columns("schedules")}
    if "last_fired_at" not in cols:
        with engine().begin() as c:
            c.execute(text("ALTER TABLE schedules ADD COLUMN last_fired_at TEXT"))


def _rows(sql, **params) -> list[dict[str, Any]]:
    with engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(sql if not isinstance(sql, str) else text(sql), params)]


def _write(sql, **params) -> int:
    """Eksekusi tulis. Query dengan RETURNING id → id baru; selain itu → jumlah baris terdampak."""
    stmt = sql if not isinstance(sql, str) else text(sql)
    with _write_lock, engine().begin() as c:
        res = c.execute(stmt, params)
        if "RETURNING" in str(stmt).upper():
            return int(res.scalar_one())
        return res.rowcount


def _source(d: dict) -> dict:
    d["config"] = json.loads(d.get("config") or "{}")
    return d


# ---------- Sources ----------
def list_sources() -> list[dict[str, Any]]:
    rows = _rows(
        """SELECT s.*, (SELECT COUNT(*) FROM items i WHERE i.source_id = s.id) AS item_count,
                  (SELECT MAX(started_at) FROM runs r WHERE r.source_id = s.id) AS last_run
           FROM sources s ORDER BY s.category, s.name""")
    return [_source(r) for r in rows]


def get_source(source_id: int) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM sources WHERE id=:id", id=source_id)
    return _source(rows[0]) if rows else None


def save_source(name: str, type_: str, config: dict, category: str = "",
                enabled: bool = True, source_id: int | None = None) -> int:
    p = dict(name=name, type=type_, category=category, config=json.dumps(config, ensure_ascii=False),
             enabled=int(enabled), ts=now_iso())
    if source_id:
        _write("""UPDATE sources SET name=:name, type=:type, category=:category, config=:config,
                  enabled=:enabled, updated_at=:ts WHERE id=:id""", id=source_id, **p)
        return source_id
    res = _write("""INSERT INTO sources(name,type,category,config,enabled,created_at,updated_at)
                    VALUES(:name,:type,:category,:config,:enabled,:ts,:ts) RETURNING id""", **p)
    return res


def delete_source(source_id: int) -> None:
    _write("DELETE FROM sources WHERE id=:id", id=source_id)


# ---------- Schedules ----------
def add_schedule(source_id: int, mode: str, value: str) -> int:
    res = _write("""INSERT INTO schedules(source_id,mode,value,enabled,created_at)
                    VALUES(:s,:m,:v,1,:ts) RETURNING id""", s=source_id, m=mode, v=value, ts=now_iso())
    return res


def list_schedules() -> list[dict[str, Any]]:
    return _rows("""SELECT sc.*, s.name AS source_name, s.enabled AS source_enabled
                    FROM schedules sc JOIN sources s ON s.id = sc.source_id ORDER BY s.name, sc.id""")


def set_schedule_enabled(schedule_id: int, enabled: bool) -> None:
    _write("UPDATE schedules SET enabled=:e WHERE id=:id", e=int(enabled), id=schedule_id)


def mark_schedule_fired(schedule_id: int, when: str) -> None:
    _write("UPDATE schedules SET last_fired_at=:w WHERE id=:id", w=when, id=schedule_id)


def delete_schedule(schedule_id: int) -> None:
    _write("DELETE FROM schedules WHERE id=:id", id=schedule_id)


# ---------- Runs ----------
def start_run(source_id: int, trigger: str) -> int:
    res = _write("""INSERT INTO runs(source_id,trigger,started_at,status)
                    VALUES(:s,:t,:ts,'running') RETURNING id""", s=source_id, t=trigger, ts=now_iso())
    return res


def finish_run(run_id: int, status: str, found: int, new: int, message: str) -> None:
    _write("""UPDATE runs SET finished_at=:ts, status=:st, items_found=:f, items_new=:n, message=:m
              WHERE id=:id""", ts=now_iso(), st=status, f=found, n=new, m=message[:2000], id=run_id)


def list_runs(limit: int = 300) -> pd.DataFrame:
    with engine().connect() as c:
        return pd.read_sql_query(text(
            """SELECT r.id, s.name AS sumber, r.trigger, r.started_at AS mulai, r.finished_at AS selesai,
                      r.status, r.items_found AS ditemukan, r.items_new AS baru, r.message AS pesan
               FROM runs r LEFT JOIN sources s ON s.id = r.source_id
               ORDER BY r.id DESC LIMIT :lim"""), c, params={"lim": limit})


# ---------- Items ----------
def save_items(source_id: int, run_id: int, items: Iterable[dict]) -> list[dict]:
    """Simpan item; kembalikan daftar item yang BENAR-BENAR baru (untuk notifikasi)."""
    items = list({it["fingerprint"]: it for it in items}.values())  # buang duplikat dalam satu run
    if not items:
        return []
    with _write_lock, engine().begin() as c:
        existing: set[str] = set()
        fps = [it["fingerprint"] for it in items]
        q = text("SELECT fingerprint FROM items WHERE source_id=:s AND fingerprint IN :fps").bindparams(
            bindparam("fps", expanding=True))
        for i in range(0, len(fps), 500):
            existing |= {r[0] for r in c.execute(q, {"s": source_id, "fps": fps[i:i + 500]})}
        ts = now_iso()
        fresh = [it for it in items if it["fingerprint"] not in existing]
        rows = [{"s": source_id, "r": run_id, "fp": it["fingerprint"], "t": it.get("title", ""),
                 "u": it.get("url", ""), "p": it.get("published", ""),
                 "d": json.dumps(it["data"], ensure_ascii=False, default=str), "ts": ts}
                for it in fresh]
        if rows:
            c.execute(text("""INSERT INTO items(source_id,run_id,fingerprint,title,url,published,data,scraped_at)
                              VALUES(:s,:r,:fp,:t,:u,:p,:d,:ts)
                              ON CONFLICT(source_id, fingerprint) DO NOTHING"""), rows)
    return fresh


def query_items(source_ids: list[int] | None = None, keyword: str = "",
                date_from: str | None = None, date_to: str | None = None,
                limit: int = 5000) -> pd.DataFrame:
    sql = """SELECT i.id, s.name AS source, s.category, i.title, i.url, i.published, i.scraped_at, i.data
             FROM items i JOIN sources s ON s.id = i.source_id WHERE 1=1"""
    params: dict[str, Any] = {"lim": int(limit)}
    if source_ids:
        sql += " AND i.source_id IN :ids"
        params["ids"] = list(source_ids)
    if keyword:
        sql += " AND (LOWER(i.title) LIKE :kw OR LOWER(i.data) LIKE :kw)"
        params["kw"] = f"%{keyword.lower()}%"
    if date_from:
        sql += " AND i.scraped_at >= :df"
        params["df"] = f"{date_from} 00:00:00"
    if date_to:
        sql += " AND i.scraped_at <= :dt"
        params["dt"] = f"{date_to} 23:59:59"
    sql += " ORDER BY i.id DESC LIMIT :lim"
    stmt = text(sql)
    if source_ids:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    with engine().connect() as c:
        return pd.read_sql_query(stmt, c, params=params)


def delete_items(source_ids: list[int] | None = None) -> int:
    if source_ids:
        stmt = text("DELETE FROM items WHERE source_id IN :ids").bindparams(bindparam("ids", expanding=True))
        return _write(stmt, ids=list(source_ids))
    return _write("DELETE FROM items")


def apply_retention() -> int:
    """Hapus item lebih tua dari `retention_days` milik masing-masing sumber (0 = simpan selamanya)."""
    removed = 0
    for src in list_sources():
        days = int(src["config"].get("retention_days") or 0)
        if days > 0:
            cutoff = (now_dt() - timedelta(days=days)).isoformat(sep=" ")
            removed += _write("DELETE FROM items WHERE source_id=:s AND scraped_at < :c", s=src["id"], c=cutoff)
    return removed


def mark_stale_runs(max_minutes: int = 30) -> int:
    """Run yang 'running' terlalu lama (proses mati/timeout) ditandai error."""
    cutoff = (now_dt() - timedelta(minutes=max_minutes)).isoformat(sep=" ")
    return _write("""UPDATE runs SET status='error', finished_at=:ts,
                     message='Proses berhenti sebelum selesai (timeout/restart)'
                     WHERE status='running' AND started_at < :c""", ts=now_iso(), c=cutoff)


def last_run_by_trigger(trigger: str) -> str | None:
    with engine().connect() as c:
        return c.execute(text("SELECT MAX(started_at) FROM runs WHERE trigger=:t"), {"t": trigger}).scalar()


def db_size_bytes() -> int | None:
    try:
        with engine().connect() as c:
            if is_postgres():
                return int(c.execute(text("SELECT pg_database_size(current_database())")).scalar())
            return int(c.execute(text("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")).scalar())
    except Exception:  # noqa: BLE001
        return None


def prune_runs(keep_days: int = 30) -> int:
    cutoff = (now_dt() - timedelta(days=keep_days)).isoformat(sep=" ")
    return _write("DELETE FROM runs WHERE started_at < :c", c=cutoff)


def stats() -> dict[str, Any]:
    cutoff = (now_dt() - timedelta(days=1)).isoformat(sep=" ")
    with engine().connect() as c:
        one = lambda q, **p: c.execute(text(q), p).scalar()  # noqa: E731
        return {
            "sources": one("SELECT COUNT(*) FROM sources"),
            "items": one("SELECT COUNT(*) FROM items"),
            "schedules": one("SELECT COUNT(*) FROM schedules WHERE enabled=1"),
            "last_run": one("SELECT MAX(started_at) FROM runs"),
            "errors_24h": one("SELECT COUNT(*) FROM runs WHERE status='error' AND started_at >= :c", c=cutoff),
        }
