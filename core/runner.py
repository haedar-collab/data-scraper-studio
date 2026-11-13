"""Menjalankan satu sumber: scrape -> simpan -> catat log run."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

from . import db
from .scrapers import ScrapeError, run_scraper

log = logging.getLogger("scraper.runner")
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def run_source(source_id: int, trigger: str = "manual") -> dict:
    src = db.get_source(source_id)
    if not src:
        return {"source_id": source_id, "status": "error", "found": 0, "new": 0, "message": "Sumber tidak ditemukan"}
    lock = _locks[source_id]
    if not lock.acquire(blocking=False):
        return {"source": src["name"], "status": "skipped", "found": 0, "new": 0,
                "message": "Masih berjalan dari proses sebelumnya"}
    run_id = db.start_run(source_id, trigger)
    try:
        items = run_scraper(src["type"], src["config"])
        if not src["config"].get("dedupe", True):
            # Simpan snapshot tiap run (cocok untuk riwayat harga)
            for it in items:
                it["fingerprint"] = f"{it['fingerprint']}:{run_id}"
        new = db.save_items(source_id, run_id, items)
        msg = f"{len(items)} item ditemukan, {new} baru"
        db.finish_run(run_id, "success", len(items), new, msg)
        return {"source": src["name"], "status": "success", "found": len(items), "new": new, "message": msg}
    except ScrapeError as e:
        db.finish_run(run_id, "error", 0, 0, str(e))
        return {"source": src["name"], "status": "error", "found": 0, "new": 0, "message": str(e)}
    except Exception as e:  # noqa: BLE001
        log.exception("Run gagal untuk sumber %s", src["name"])
        msg = f"{type(e).__name__}: {e}"
        db.finish_run(run_id, "error", 0, 0, msg)
        return {"source": src["name"], "status": "error", "found": 0, "new": 0, "message": msg}
    finally:
        lock.release()
