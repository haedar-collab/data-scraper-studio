"""Uji semua preset ke internet sungguhan. Hasil ditulis sebagai anotasi GitHub (tidak menggagalkan build)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.presets import PRESETS  # noqa: E402
from core.scrapers import ScrapeError, run_scraper  # noqa: E402

ok = 0
lines = ["### Cek sumber preset", "", "| Preset | Hasil |", "|---|---|"]
for p in PRESETS:
    t = time.time()
    try:
        items = run_scraper(p["type"], p["config"])
        first = (items[0]["title"][:60] if items else "-").replace("|", "/")
        msg = f"{len(items)} item ({time.time() - t:.1f}s) · contoh: {first}"
        print(f"::notice title=PRESET OK::{p['name']} → {msg}")
        ok += bool(items)
        lines.append(f"| {p['name']} | ✅ {msg} |")
    except (ScrapeError, Exception) as e:  # noqa: BLE001
        print(f"::warning title=PRESET GAGAL::{p['name']} → {str(e)[:250]}")
        lines.append(f"| {p['name']} | ❌ {str(e)[:150]} |")
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
print(f"{ok}/{len(PRESETS)} preset mengembalikan data")
