"""Dijalankan oleh GitHub Actions (atau cron server) secara berkala.

Mengeksekusi semua jadwal yang sudah jatuh tempo sejak run terakhirnya, lalu keluar.
  python cron_runner.py            → jalankan jadwal yang jatuh tempo
  python cron_runner.py --all      → jalankan semua sumber aktif sekarang juga
"""
import logging
import sys

from core import db
from core.runner import run_source
from core.scheduler import due_schedules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron_runner")


def main() -> int:
    db.init_db()
    if "--all" in sys.argv:
        targets = [(None, s["id"]) for s in db.list_sources() if s["enabled"]]
    else:
        due = due_schedules()
        # satu sumber cukup dijalankan sekali walau punya beberapa jadwal jatuh tempo
        seen: dict[int, list[int]] = {}
        for s in due:
            seen.setdefault(s["source_id"], []).append(s["id"])
        targets = [(ids, sid) for sid, ids in seen.items()]
    if not targets:
        log.info("Tidak ada jadwal yang jatuh tempo.")
        return 0

    errors = 0
    for schedule_ids, source_id in targets:
        fired_at = db.now_iso()
        res = run_source(source_id, "schedule" if schedule_ids else "manual-all")
        for sch_id in schedule_ids or []:
            db.mark_schedule_fired(sch_id, fired_at)
        log.info("%s → %s: %s", res.get("source"), res["status"], res["message"])
        errors += res["status"] == "error"
    db.prune_runs(keep_days=30)
    log.info("Selesai: %d sumber, %d error", len(targets), errors)
    return 0  # error per-sumber sudah tercatat di tab Log; workflow tetap hijau


if __name__ == "__main__":
    sys.exit(main())
