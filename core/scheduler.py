"""Penjadwal berbasis APScheduler. Jadwal disimpan di DB dan disinkronkan ke scheduler."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import db
from .runner import run_source

TZ = db.TZ
log = logging.getLogger("scraper.scheduler")

MODE_LABELS = {
    "interval": "Setiap N menit",
    "daily": "Setiap hari jam (HH:MM)",
    "weekday": "Hari kerja Sen–Jum jam (HH:MM)",
    "cron": "Ekspresi cron (lanjutan)",
}
MODE_HINTS = {
    "interval": "Contoh: 30  → jalan tiap 30 menit",
    "daily": "Contoh: 07:30  (beberapa jam: 08:45, 12:00, 16:15)",
    "weekday": "Contoh: 08:45, 16:15  → cocok untuk jam bursa BEI",
    "cron": "Format 5 kolom: menit jam tgl bulan hari. Contoh: */15 9-16 * * mon-fri",
}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _times(value: str) -> list[tuple[int, int]]:
    out = []
    for part in value.split(","):
        part = part.strip()
        m = _TIME_RE.match(part)
        if not m:
            raise ValueError(f"Format jam tidak valid: '{part}' (pakai HH:MM)")
        out.append((int(m.group(1)), int(m.group(2))))
    return out


def build_triggers(mode: str, value: str) -> list:
    value = (value or "").strip()
    if mode == "interval":
        minutes = int(value)
        if minutes < 1:
            raise ValueError("Interval minimal 1 menit")
        return [IntervalTrigger(minutes=minutes, timezone=TZ)]
    if mode in ("daily", "weekday"):
        dow = "mon-fri" if mode == "weekday" else "*"
        return [CronTrigger(day_of_week=dow, hour=h, minute=m, timezone=TZ) for h, m in _times(value)]
    if mode == "cron":
        return [CronTrigger.from_crontab(value, timezone=TZ)]
    raise ValueError(f"Mode jadwal tidak dikenal: {mode}")


def validate_trigger(mode: str, value: str) -> str | None:
    """Kembalikan pesan error, atau None bila valid."""
    try:
        build_triggers(mode, value)
        return None
    except (ValueError, TypeError) as e:
        return str(e)


class ScrapeScheduler:
    def __init__(self) -> None:
        self.sched = BackgroundScheduler(
            timezone=TZ,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )
        self._signature: str | None = None

    @property
    def running(self) -> bool:
        return self.sched.running

    def start(self) -> None:
        if not self.sched.running:
            self.sched.start()
        self.sync(force=True)

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)

    def sync(self, force: bool = False) -> bool:
        """Sinkronkan job dengan tabel schedules. Hanya bongkar-pasang job bila jadwal berubah."""
        schedules = db.list_schedules()
        sig = hashlib.md5(json.dumps(
            [(s["id"], s["source_id"], s["mode"], s["value"], s["enabled"], s["source_enabled"]) for s in schedules]
        ).encode()).hexdigest()
        if not force and sig == self._signature:
            return False
        for job in self.sched.get_jobs():
            job.remove()
        for s in schedules:
            if not (s["enabled"] and s["source_enabled"]):
                continue
            try:
                for i, trig in enumerate(build_triggers(s["mode"], s["value"])):
                    self.sched.add_job(run_source, trig, args=[s["source_id"], "schedule"],
                                       id=f"sched-{s['id']}-{i}", replace_existing=True,
                                       name=f"{s['source_name']} ({s['mode']}: {s['value']})")
            except (ValueError, TypeError) as e:
                log.error("Jadwal #%s tidak valid: %s", s["id"], e)
        self._signature = sig
        log.info("Scheduler disinkronkan: %d job aktif", len(self.sched.get_jobs()))
        return True

    def next_runs(self) -> dict[int, datetime | None]:
        out: dict[int, datetime | None] = {}
        for job in self.sched.get_jobs():
            sid = int(job.id.split("-")[1])
            nrt = getattr(job, "next_run_time", None)
            if nrt and (out.get(sid) is None or nrt < out[sid]):
                out[sid] = nrt
        return out


# ---------------------------------------------------------------------------
# Mode cloud (GitHub Actions): tidak ada proses yang hidup terus. cron_runner.py
# dipanggil berkala (mis. tiap 15 menit) lalu menjalankan jadwal yang sudah jatuh tempo.
# ---------------------------------------------------------------------------
def _aware(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(str(ts)).replace(tzinfo=ZoneInfo(TZ))


def next_fire(schedule: dict, after: datetime | None = None) -> datetime | None:
    """Waktu jalan berikutnya setelah eksekusi terakhir (atau setelah `after`)."""
    last = _aware(schedule.get("last_fired_at") or schedule.get("created_at"))
    base = last or datetime.now(ZoneInfo(TZ))
    if after and after > base and schedule["mode"] != "interval":
        base = after
    try:
        trigs = build_triggers(schedule["mode"], schedule["value"])
    except (ValueError, TypeError):
        return None
    times = [t.get_next_fire_time(base, base + timedelta(seconds=1)) for t in trigs]
    times = [t for t in times if t]
    return min(times) if times else None


def due_schedules(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(ZoneInfo(TZ))
    due = []
    for s in db.list_schedules():
        if not (s["enabled"] and s["source_enabled"]):
            continue
        nf = next_fire(s)
        if nf and nf <= now:
            due.append(s)
    return due
