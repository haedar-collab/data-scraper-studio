"""Notifikasi Telegram (opsional).

Aktif bila environment / Secrets berisi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID.
- Item baru: dikirim per sumber yang mengaktifkan opsi "Notifikasi Telegram".
- Error: dikirim saat sebuah sumber BERUBAH dari sukses ke gagal (tidak spam tiap run).
"""
from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger("scraper.notify")
MAX_LEN = 3900  # batas pesan Telegram 4096


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str) -> tuple[bool, str]:
    if not configured():
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi"
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat = os.environ["TELEGRAM_CHAT_ID"].strip()
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:MAX_LEN], "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and data.get("ok"):
            return True, "Terkirim"
        return False, f"Telegram menolak: {data.get('description') or r.status_code}"
    except requests.RequestException as e:
        log.warning("Telegram gagal: %s", e)
        return False, f"Gagal menghubungi Telegram: {type(e).__name__}"


def format_new_items(source_name: str, items: list[dict], limit: int = 8) -> str:
    lines = [f"🆕 <b>{html.escape(source_name)}</b>: {len(items)} item baru"]
    for it in items[:limit]:
        title = html.escape((it.get("title") or "(tanpa judul)")[:160])
        url = it.get("url") or ""
        lines.append(f"• <a href=\"{html.escape(url, quote=True)}\">{title}</a>" if url.startswith("http") else f"• {title}")
    if len(items) > limit:
        lines.append(f"…dan {len(items) - limit} lainnya")
    return "\n".join(lines)


def format_error(source_name: str, message: str) -> str:
    return f"⚠️ <b>{html.escape(source_name)}</b> gagal di-scrape\n<code>{html.escape(message[:600])}</code>"
