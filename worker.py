"""Worker penjadwal tanpa UI — untuk scraping otomatis 24/7 (server/VPS/PC yang selalu nyala).

Jalankan:  python worker.py
Jika memakai worker ini, jalankan UI dengan SCHEDULER_IN_APP=0 supaya job tidak dobel.
Perubahan jadwal dari UI otomatis terbaca tiap 30 detik.
"""
import logging
import signal
import time

from core import db
from core.scheduler import ScrapeScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")


def main() -> None:
    db.init_db()
    s = ScrapeScheduler()
    s.start()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("Worker berjalan. Ctrl+C untuk berhenti.")
    while not stop["flag"]:
        time.sleep(30)
        if s.sync():
            log.info("Perubahan jadwal terdeteksi dan diterapkan")
    s.shutdown()
    log.info("Worker berhenti.")


if __name__ == "__main__":
    main()
