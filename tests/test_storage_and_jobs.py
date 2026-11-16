import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from core import notify
from core.exporter import build_results_frame, to_csv_bytes, to_excel_bytes
from core.runner import run_source
from core.scheduler import due_schedules, next_fire, validate_trigger

HTML = {"item_selector": "article.p", "fields": {"title": "h3 a@title", "url": "h3 a@href", "price": ".price"},
        "numeric_fields": ["price"], "delay": 0}


def test_sources_crud_and_cascade(database, server):
    db = database
    sid = db.save_source("rss", "rss", {"url": f"{server}/feed.xml"}, "T")
    db.save_source("rss2", "rss", {"url": "x"}, "T", enabled=False, source_id=sid)
    assert db.get_source(sid)["name"] == "rss2" and not db.get_source(sid)["enabled"]
    db.add_schedule(sid, "interval", "30")
    run_source(sid)
    db.delete_source(sid)
    assert db.list_schedules() == [] and len(db.query_items()) == 0 and db.stats()["sources"] == 0


def test_dedupe_and_snapshot(database, server):
    db = database
    a = db.save_source("rss", "rss", {"url": f"{server}/feed.xml"})
    b = db.save_source("snap", "html", {**HTML, "url": f"{server}/page-1.html", "dedupe": False})
    assert run_source(a)["new"] == 2 and run_source(a)["new"] == 0
    assert run_source(b)["new"] == 2 and run_source(b)["new"] == 2  # snapshot = simpan tiap run
    df = build_results_frame(db.query_items([a], "IHSG"))  # pencarian tidak peka huruf besar/kecil
    assert df["title"].tolist() == ["IHSG naik 1%"]
    assert len(db.query_items([a], "ihsg")) == 1
    s = db.stats()
    assert (s["items"], s["errors_24h"]) == (6, 0)


def test_error_run_logged(database, server):
    db = database
    sid = db.save_source("bad", "html", {**HTML, "url": f"{server}/page-1.html", "item_selector": "div.none"})
    res = run_source(sid)
    assert res["status"] == "error" and "tidak menemukan" in res["message"]
    assert db.list_runs().iloc[0]["status"] == "error" and db.stats()["errors_24h"] == 1


def test_retention_and_stale_runs(database, server):
    db = database
    sid = db.save_source("r", "rss", {"url": f"{server}/feed.xml", "retention_days": 7})
    run_source(sid)
    old = (db.now_dt() - timedelta(days=10)).isoformat(sep=" ")
    db._write("UPDATE items SET scraped_at=:o WHERE url='http://x/1'", o=old)
    assert db.apply_retention() == 1 and len(db.query_items()) == 1
    rid = db.start_run(sid, "schedule")
    db._write("UPDATE runs SET started_at=:o WHERE id=:i", o=old, i=rid)
    assert db.mark_stale_runs() == 1
    assert db.db_size_bytes() > 0


def test_triggers_validation():
    assert validate_trigger("weekday", "08:45, 16:15") is None
    assert validate_trigger("cron", "*/15 9-16 * * mon-fri") is None
    assert "tidak valid" in validate_trigger("daily", "25:00")
    assert validate_trigger("interval", "0")
    assert validate_trigger("cron", "bad cron")


def test_next_fire_weekday_skips_weekend():
    tz = ZoneInfo("Asia/Jakarta")
    # Jumat 13 Nov 2026 17:00 → jadwal hari kerja berikutnya Senin 16 Nov 08:45
    s = {"mode": "weekday", "value": "08:45", "last_fired_at": "2026-11-13 17:00:00", "created_at": None}
    assert next_fire(s) == datetime(2026, 11, 16, 8, 45, tzinfo=tz)
    s2 = {"mode": "interval", "value": "30", "last_fired_at": "2026-11-13 17:00:00"}
    assert next_fire(s2) == datetime(2026, 11, 13, 17, 30, tzinfo=tz)


def test_due_schedules_and_cron_runner(database, server, monkeypatch, tmp_path):
    db = database
    a = db.save_source("rss", "rss", {"url": f"{server}/feed.xml"})
    s1 = db.add_schedule(a, "interval", "30")
    s2 = db.add_schedule(a, "daily", "00:00")  # dua jadwal jatuh tempo → sumber cukup jalan sekali
    db.add_schedule(a, "interval", "600")       # belum jatuh tempo
    old = (db.now_dt() - timedelta(days=2)).isoformat(sep=" ")
    db._write("UPDATE schedules SET created_at=:o WHERE id IN (:x,:y)", o=old, x=s1, y=s2)
    assert {d["id"] for d in due_schedules()} == {s1, s2}
    import cron_runner
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cron_runner.main() == 0
    assert due_schedules() == []
    assert len(db.list_runs()) == 1 and "rss" in summary.read_text()


def test_notifications(database, server, monkeypatch):
    db = database
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(notify, "send", lambda text: sent.append(text) or (True, "ok"))
    a = db.save_source("berita <IHSG>", "rss", {"url": f"{server}/feed.xml", "notify": True})
    run_source(a)
    assert len(sent) == 1 and "2 item baru" in sent[0] and "&lt;IHSG&gt;" in sent[0]
    run_source(a)
    assert len(sent) == 1  # tidak ada item baru → tidak ada pesan
    bad = db.save_source("bad", "rss", {"url": f"{server}/missing.xml"})
    run_source(bad, "schedule")
    run_source(bad, "schedule")
    assert len(sent) == 2 and "gagal" in sent[1]  # error hanya dikirim saat berubah status
    snap = db.save_source("snap", "html", {**HTML, "url": f"{server}/page-1.html", "dedupe": False, "notify": True})
    run_source(snap)
    assert len(sent) == 2  # snapshot tidak memicu notifikasi


def test_notify_send_handles_failures(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert notify.send("x")[0] is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "post", boom)
    ok, msg = notify.send("x")
    assert not ok and "ConnectionError" in msg


def test_export(database, server):
    db = database
    a = db.save_source("rss", "rss", {"url": f"{server}/feed.xml"}, "Saham")
    b = db.save_source("harga", "html", {**HTML, "url": f"{server}/page-1.html"}, "Harga")
    run_source(a)
    run_source(b)
    db._write("UPDATE items SET title=:t WHERE url='http://x/1'", t="judul\x07aneh")
    df = build_results_frame(db.query_items())
    wb = openpyxl.load_workbook(__import__("io").BytesIO(to_excel_bytes(df)))
    assert wb.sheetnames == ["Ringkasan", "Semua Data", "harga", "rss"]
    assert wb["Ringkasan"]["C1"].value == "jumlah_item"
    assert to_excel_bytes(df[["title"]])[:2] == b"PK"  # subset kolom tanpa 'source' tetap bisa
    assert to_csv_bytes(df).startswith("﻿".encode())


def test_database_url_cleaning():
    from core.db import clean_database_url, masked_url, password_hint
    raw = "psql '“postgresql://u:npg_secret@h.neon.tech/neondb?sslmode=require”'\n"
    assert clean_database_url(raw) == "postgresql://u:npg_secret@h.neon.tech/neondb?sslmode=require"
    assert "npg_secret" not in masked_url(raw)
    assert "****" in password_hint("postgresql://u:****@h/db")


@pytest.mark.skipif(sys.platform == "win32", reason="posix")
def test_worker_imports():
    assert subprocess.run([sys.executable, "-c", "import worker"], cwd=str(__import__("pathlib").Path(
        __file__).resolve().parent.parent)).returncode == 0
