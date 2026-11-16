"""Menjalankan satu sumber: scrape -> simpan -> catat log run -> notifikasi (opsional)."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

from . import db, notify
from .scrapers import ScrapeError, run_scraper

log = logging.getLogger("scraper.runner")
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def _previous_status(source_id: int, current_run: int) -> str | None:
    rows = db._rows("SELECT status FROM runs WHERE source_id=:s AND id<:r AND status!='running' "
                    "ORDER BY id DESC LIMIT 1", s=source_id, r=current_run)
    return rows[0]["status"] if rows else None


def _notify_safe(text: str) -> None:
    try:
        notify.send(text)
    except Exception:  # noqa: BLE001 — notifikasi tidak boleh menggagalkan scraping
        log.exception("Notifikasi gagal")


def run_source(source_id: int, trigger: str = "manual") -> dict:
    src = db.get_source(source_id)
    if not src:
        return {"source": f"#{source_id}", "status": "error", "found": 0, "new": 0, "message": "Sumber tidak ditemukan"}
    lock = _locks[source_id]
    if not lock.acquire(blocking=False):
        return {"source": src["name"], "status": "skipped", "found": 0, "new": 0,
                "message": "Masih berjalan dari proses sebelumnya"}
    cfg = src["config"]
    run_id = db.start_run(source_id, trigger)
    try:
        try:
            items = run_scraper(src["type"], cfg)
            if not cfg.get("dedupe", True):
                # Simpan snapshot tiap run (cocok untuk riwayat harga)
                for it in items:
                    it["fingerprint"] = f"{it['fingerprint']}:{run_id}"
            fresh = db.save_items(source_id, run_id, items)
        except ScrapeError as e:
            msg = str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Run gagal untuk sumber %s", src["name"])
            msg = f"{type(e).__name__}: {e}"
        else:
            msg = f"{len(items)} item ditemukan, {len(fresh)} baru"
            db.finish_run(run_id, "success", len(items), len(fresh), msg)
            # snapshot (dedupe=False) selalu 'baru' tiap run → jangan dikirimi notifikasi
            if fresh and cfg.get("notify") and cfg.get("dedupe", True) and notify.configured():
                _notify_safe(notify.format_new_items(src["name"], fresh))
            return {"source": src["name"], "status": "success", "found": len(items), "new": len(fresh), "message": msg}

        db.finish_run(run_id, "error", 0, 0, msg)
        if trigger == "schedule" and notify.configured() and _previous_status(source_id, run_id) != "error":
            _notify_safe(notify.format_error(src["name"], msg))
        return {"source": src["name"], "status": "error", "found": 0, "new": 0, "message": msg}
    finally:
        lock.release()
