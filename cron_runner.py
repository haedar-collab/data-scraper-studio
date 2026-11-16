"""Dijalankan oleh GitHub Actions (atau cron server) secara berkala.

Mengeksekusi semua jadwal yang sudah jatuh tempo sejak run terakhirnya, lalu keluar.
  python cron_runner.py            → jalankan jadwal yang jatuh tempo
  python cron_runner.py --all      → jalankan semua sumber aktif sekarang juga
"""
import logging
import os
import sys

from core import db
from core.runner import run_source
from core.scheduler import due_schedules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron_runner")


def _summary(lines: list[str]) -> None:
    """Tulis ringkasan ke halaman run GitHub Actions (bila tersedia)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main() -> int:
    db.init_db()
    db.mark_stale_runs()
    if "--all" in sys.argv:
        targets = [(None, s["id"]) for s in db.list_sources() if s["enabled"]]
    else:
        # satu sumber cukup dijalankan sekali walau punya beberapa jadwal jatuh tempo
        seen: dict[int, list[int]] = {}
        for s in due_schedules():
            seen.setdefault(s["source_id"], []).append(s["id"])
        targets = [(ids, sid) for sid, ids in seen.items()]

    lines = ["### Data Scraper Studio", "", "| Sumber | Status | Pesan |", "|---|---|---|"]
    errors = 0
    for schedule_ids, source_id in targets:
        fired_at = db.now_iso()
        res = run_source(source_id, "schedule" if schedule_ids else "manual-all")
        for sch_id in schedule_ids or []:
            db.mark_schedule_fired(sch_id, fired_at)
        log.info("%s → %s: %s", res.get("source"), res["status"], res["message"])
        lines.append(f"| {res.get('source')} | {res['status']} | {res['message'][:150]} |")
        errors += res["status"] == "error"

    removed = db.apply_retention()
    db.prune_runs(keep_days=30)
    if not targets:
        lines = ["### Data Scraper Studio", "", "Tidak ada jadwal yang jatuh tempo."]
        log.info("Tidak ada jadwal yang jatuh tempo.")
    if removed:
        lines.append(f"\n🧹 {removed} item lama dihapus sesuai aturan retensi.")
    _summary(lines)
    log.info("Selesai: %d sumber, %d error, %d item lama dihapus", len(targets), errors, removed)
    return 0  # error per-sumber tercatat di tab Log (+ Telegram bila aktif); workflow tetap hijau


if __name__ == "__main__":
    sys.exit(main())
