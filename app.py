"""Data Scraper Studio — UI Streamlit.

Jalankan:  streamlit run app.py
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Data Scraper Studio", page_icon="🕸️", layout="wide",
                   initial_sidebar_state="collapsed")

# Secrets Streamlit Cloud → environment (harus sebelum modul core dimuat)
try:
    for _k in ("DATABASE_URL", "APP_PASSWORD", "SCRAPER_TZ", "SCHEDULER_IN_APP"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except FileNotFoundError:
    pass
except Exception:  # noqa: BLE001 — tidak ada secrets.toml saat lokal
    pass

from core import db  # noqa: E402
from core.exporter import build_results_frame, to_csv_bytes, to_excel_bytes  # noqa: E402
from core.presets import PRESETS  # noqa: E402
from core.runner import run_source  # noqa: E402
from core.scheduler import (MODE_HINTS, MODE_LABELS, ScrapeScheduler, next_fire,  # noqa: E402
                            validate_trigger)
from core.scrapers import SOURCE_TYPES, ScrapeError, run_scraper  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Tampilan ramah layar HP
st.markdown("""<style>
.block-container {padding-top: 2.2rem; padding-left: .8rem; padding-right: .8rem;}
.stButton button, .stDownloadButton button {min-height: 2.8rem; border-radius: 10px;}
div[data-baseweb="tab-list"] {overflow-x: auto; flex-wrap: nowrap;}
button[data-baseweb="tab"] {padding: .4rem .6rem; white-space: nowrap;}
[data-testid="stMetricValue"] {font-size: 1.4rem;}
.stat-row {display: flex; gap: .5rem; margin: .2rem 0 .6rem;}
.stat {flex: 1; background: #F5F3FF; border-radius: 10px; padding: .45rem .3rem; text-align: center;}
.stat .v {font-size: 1.25rem; font-weight: 700; color: #4C1D95; line-height: 1.2;}
.stat .l {font-size: .72rem; color: #6B7280;}
@media (max-width: 640px) {
  h1 {font-size: 1.5rem !important;}
  h2, h3 {font-size: 1.15rem !important;}
}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- login
def check_password() -> bool:
    pw = os.environ.get("APP_PASSWORD", "").strip()
    if not pw or st.session_state.get("authed"):
        return True
    st.title("🕸️ Data Scraper Studio")
    entered = st.text_input("Password", type="password",
                            help="Ketuk ikon mata untuk melihat ketikan. Awas huruf kapital otomatis dari keyboard HP.")
    if st.button("Masuk", type="primary", width="stretch"):
        if hmac.compare_digest(entered.strip().encode(), pw.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Password salah. Cek huruf besar/kecil dan spasi, atau cek APP_PASSWORD di Secrets Streamlit.")
    return False


if not check_password():
    st.stop()

try:
    db.init_db()
except Exception as _e:  # noqa: BLE001
    _url = os.environ.get("DATABASE_URL", "")
    st.error("❌ Gagal terhubung ke database.")
    st.markdown(f"""
**Pesan error:** `{type(_e).__name__}: {str(_e).splitlines()[0][:300]}`

Cek di **Streamlit → ⋮ → Settings → Secrets**:
- Terbaca sebagai: `{db.masked_url(_url) or '(kosong)'}`
- Password di URL: {db.password_hint(_url)}
- `DATABASE_URL` harus berbentuk `postgresql://user:password@host/neondb?sslmode=require`
- Pakai tanda kutip biasa `"`, bukan kutip miring `“ ”` dari keyboard HP
- Salin ulang connection string dari Neon (tombol **Connect**), pastikan password ikut tersalin, bukan `****`
- Setelah mengubah Secrets, klik **⋮ → Reboot app**
""")
    st.stop()
CLOUD_MODE = bool(os.environ.get("DATABASE_URL")) and os.environ.get("SCHEDULER_IN_APP", "0") != "1"


@st.cache_resource
def get_scheduler() -> ScrapeScheduler:
    s = ScrapeScheduler()
    if not CLOUD_MODE and os.environ.get("SCHEDULER_IN_APP", "1") == "1":
        s.start()
    return s


sched = get_scheduler()

# ---------------------------------------------------------------- helpers
def parse_mapping(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def mapping_to_text(d: dict | None) -> str:
    return "\n".join(f"{k} = {v}" for k, v in (d or {}).items())


def parse_list(text: str) -> list[str]:
    return [x.strip() for x in (text or "").replace("\n", ",").split(",") if x.strip()]


def source_editor(prefix: str, src: dict | None = None) -> dict | None:
    """Form sumber (baru/edit). Mengembalikan dict sumber bila valid, None bila belum lengkap."""
    src = src or {}
    cfg = dict(src.get("config") or {})
    types = list(SOURCE_TYPES)
    c1, c2, c3 = st.columns([2, 2, 1])
    name = c1.text_input("Nama sumber", src.get("name", ""), key=f"{prefix}_name")
    category = c2.text_input("Kategori", src.get("category", ""), key=f"{prefix}_cat",
                             placeholder="Saham BEI, Crypto, Harga, ...")
    enabled = c3.checkbox("Aktif", bool(src.get("enabled", 1)), key=f"{prefix}_en")
    type_ = st.selectbox("Tipe sumber", types, index=types.index(src.get("type", "rss")),
                         format_func=lambda t: SOURCE_TYPES[t]["label"], key=f"{prefix}_type")
    st.caption(SOURCE_TYPES[type_]["help"])

    new: dict = {}
    if type_ == "rss":
        new["url"] = st.text_input("URL feed", cfg.get("url", ""), key=f"{prefix}_url")
        new["max_items"] = st.number_input("Maks item", 1, 5000, int(cfg.get("max_items", 100)), key=f"{prefix}_max")
    elif type_ == "google_news":
        new["query"] = st.text_input("Kata kunci", cfg.get("query", ""), key=f"{prefix}_q",
                                     help="Mendukung OR, tanda kutip, dan -kata untuk mengecualikan")
        a, b, c = st.columns(3)
        new["lang"] = a.selectbox("Bahasa", ["id", "en"], index=["id", "en"].index(cfg.get("lang", "id")), key=f"{prefix}_lang")
        new["country"] = b.selectbox("Negara", ["ID", "US", "SG", "GB"],
                                     index=["ID", "US", "SG", "GB"].index(cfg.get("country", "ID")), key=f"{prefix}_ctry")
        new["max_items"] = c.number_input("Maks item", 1, 500, int(cfg.get("max_items", 100)), key=f"{prefix}_max")
    elif type_ == "html":
        new["url"] = st.text_input("URL halaman", cfg.get("url", ""), key=f"{prefix}_url",
                                   help="Pakai {page} untuk paginasi, contoh: https://situs.com/produk?page={page}")
        a, b, c, d = st.columns(4)
        new["page_start"] = a.number_input("Halaman awal", 1, 10000, int(cfg.get("page_start", 1)), key=f"{prefix}_ps")
        new["page_end"] = b.number_input("Halaman akhir", 1, 10000, int(cfg.get("page_end", 1)), key=f"{prefix}_pe")
        new["delay"] = c.number_input("Jeda antar halaman (detik)", 0.0, 60.0, float(cfg.get("delay", 1.5)), 0.5, key=f"{prefix}_dl")
        new["max_items"] = d.number_input("Maks item", 1, 100000, int(cfg.get("max_items", 500)), key=f"{prefix}_max")
        new["item_selector"] = st.text_input("Selector item (CSS)", cfg.get("item_selector", ""), key=f"{prefix}_isel",
                                             placeholder="article.product, div.card, tr")
        new["fields"] = parse_mapping(st.text_area(
            "Field (satu per baris: nama = selector  |  selector@atribut untuk atribut)",
            mapping_to_text(cfg.get("fields")) or "title = h3 a@title\nurl = h3 a@href\nprice = .price",
            height=130, key=f"{prefix}_fields"))
        new["numeric_fields"] = parse_list(st.text_input(
            "Field angka/harga yang dibersihkan (koma)", ", ".join(cfg.get("numeric_fields", [])), key=f"{prefix}_num",
            help="Menambah kolom <field>_num. 'Rp 1.250.000' → 1250000"))
    elif type_ == "json_api":
        a, b = st.columns([4, 1])
        new["url"] = a.text_input("URL endpoint", cfg.get("url", ""), key=f"{prefix}_url")
        new["method"] = b.selectbox("Method", ["GET", "POST"], index=["GET", "POST"].index(cfg.get("method", "GET")), key=f"{prefix}_m")
        new["params"] = parse_mapping(st.text_area("Query params (nama = nilai)", mapping_to_text(cfg.get("params")),
                                                   height=90, key=f"{prefix}_params"))
        new["items_path"] = st.text_input("Path ke list data (notasi titik, kosong = root)", cfg.get("items_path", ""),
                                          key=f"{prefix}_ip", placeholder="data.items")
        new["fields"] = parse_mapping(st.text_area("Pemetaan field (nama = path). Kosongkan untuk ambil semua",
                                                   mapping_to_text(cfg.get("fields")), height=120, key=f"{prefix}_fields"))
        a, b, c = st.columns(3)
        new["title_field"] = a.text_input("Field judul", cfg.get("title_field", "title"), key=f"{prefix}_tf")
        new["url_field"] = b.text_input("Field URL", cfg.get("url_field", "url"), key=f"{prefix}_uf")
        new["max_items"] = c.number_input("Maks item", 1, 100000, int(cfg.get("max_items", 1000)), key=f"{prefix}_max")
        if new["method"] == "POST":
            body = st.text_area("Body JSON", json.dumps(cfg.get("body", {}), indent=2), key=f"{prefix}_body")
            try:
                new["body"] = json.loads(body or "{}")
            except json.JSONDecodeError:
                st.error("Body bukan JSON valid")
                return None
    elif type_ == "ckan":
        new["base_url"] = st.text_input("Base URL portal", cfg.get("base_url", ""), key=f"{prefix}_base",
                                        help="Portal yang memakai CKAN API (/api/3/action/package_search)")
        a, b = st.columns([3, 1])
        new["query"] = a.text_input("Kata kunci dataset", cfg.get("query", ""), key=f"{prefix}_q")
        new["rows"] = b.number_input("Jumlah", 1, 1000, int(cfg.get("rows", 50)), key=f"{prefix}_rows")

    with st.expander("Opsi lanjutan: filter, duplikasi, etika & koneksi"):
        a, b = st.columns(2)
        new["keywords"] = parse_list(a.text_input("Hanya simpan jika mengandung (koma)",
                                                  ", ".join(cfg.get("keywords", [])), key=f"{prefix}_kw"))
        new["exclude_keywords"] = parse_list(b.text_input("Buang jika mengandung (koma)",
                                                          ", ".join(cfg.get("exclude_keywords", [])), key=f"{prefix}_xkw"))
        mode = st.radio("Mode simpan", ["Hanya item baru (hapus duplikat)", "Simpan snapshot tiap run (riwayat harga)"],
                        index=0 if cfg.get("dedupe", True) else 1, key=f"{prefix}_dd", horizontal=True)
        new["dedupe"] = mode.startswith("Hanya")
        new["dedupe_by"] = st.radio("Identitas item ditentukan oleh", ["url", "data"],
                                    index=["url", "data"].index(cfg.get("dedupe_by", "url")), horizontal=True,
                                    format_func=lambda x: "URL" if x == "url" else "Seluruh isi data",
                                    key=f"{prefix}_dby")
        a, b = st.columns(2)
        new["respect_robots"] = a.checkbox("Patuhi robots.txt", cfg.get("respect_robots", True), key=f"{prefix}_rb")
        new["timeout"] = b.number_input("Timeout (detik)", 5, 120, int(cfg.get("timeout", 20)), key=f"{prefix}_to")
        new["user_agent"] = st.text_input("User-Agent kustom (opsional)", cfg.get("user_agent", ""), key=f"{prefix}_ua")
        new["headers"] = parse_mapping(st.text_area("Header tambahan (nama = nilai), misal API key",
                                                    mapping_to_text(cfg.get("headers")), height=70, key=f"{prefix}_hd"))

    return {"name": name.strip(), "category": category.strip(), "enabled": enabled, "type": type_, "config": new}


def preview(source: dict, key: str) -> None:
    if st.button("🔍 Preview (tanpa simpan)", key=key):
        with st.spinner("Mengambil data..."):
            try:
                items = run_scraper(source["type"], source["config"])
            except ScrapeError as e:
                st.error(str(e))
                return
            except Exception as e:  # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
                return
        st.success(f"{len(items)} item ditemukan")
        if items:
            st.dataframe(pd.json_normalize([it["data"] for it in items[:50]]), width="stretch", height=320)


def show_run_results(results: list[dict]) -> None:
    df = pd.DataFrame(results)
    if df.empty:
        return
    ok = (df["status"] == "success").sum()
    st.write(f"✅ {ok} berhasil · ❌ {(df['status'] == 'error').sum()} gagal · 🆕 {int(df['new'].sum())} item baru")
    st.dataframe(df.rename(columns={"source": "sumber", "found": "ditemukan", "new": "baru", "message": "pesan"}),
                 width="stretch", hide_index=True)


# ---------------------------------------------------------------- header
stats = db.stats()
st.title("🕸️ Data Scraper Studio")
_cards = [("Sumber", stats["sources"]), ("Item", f"{stats['items']:,}".replace(",", ".")),
          ("Jadwal", stats["schedules"]), ("Error 24j", stats["errors_24h"])]
st.markdown('<div class="stat-row">' + "".join(
    f'<div class="stat"><div class="v">{v}</div><div class="l">{k}</div></div>' for k, v in _cards) + "</div>",
    unsafe_allow_html=True)
st.caption(f"Run terakhir: {stats['last_run'] or '—'} WIB · "
           + ("Jadwal dijalankan GitHub Actions (cek tiap ±15 menit)" if CLOUD_MODE else
              f"Scheduler lokal {'aktif' if sched.running else 'nonaktif'}"))
if st.button("🔄 Muat ulang data"):
    if not CLOUD_MODE:
        sched.sync(force=True)
    st.rerun()

tab_src, tab_run, tab_sched, tab_res, tab_log = st.tabs(
    ["📡 Sumber", "▶️ Jalankan", "⏰ Jadwal", "📊 Hasil & Ekspor", "📜 Log"])

# ---------------------------------------------------------------- Sumber
with tab_src:
    sources = db.list_sources()
    sub_list, sub_new, sub_edit, sub_preset = st.tabs(["Daftar", "➕ Tambah", "✏️ Edit / Hapus", "⚡ Preset"])

    with sub_list:
        if not sources:
            st.info("Belum ada sumber. Tambah manual atau pakai tab ⚡ Preset untuk mulai cepat.")
        else:
            st.dataframe(pd.DataFrame([{
                "ID": s["id"], "Nama": s["name"], "Kategori": s["category"],
                "Tipe": SOURCE_TYPES.get(s["type"], {}).get("label", s["type"]),
                "Aktif": bool(s["enabled"]), "Item": s["item_count"], "Run terakhir": s["last_run"],
                "Target": s["config"].get("url") or s["config"].get("query") or s["config"].get("base_url", ""),
            } for s in sources]), width="stretch", hide_index=True)

    with sub_new:
        draft = source_editor("new")
        if draft:
            c1, c2 = st.columns([1, 4])
            with c2:
                preview(draft, "prev_new")
            if c1.button("💾 Simpan sumber", type="primary", key="save_new"):
                if not draft["name"]:
                    st.error("Nama sumber wajib diisi")
                elif any(s["name"] == draft["name"] for s in sources):
                    st.error("Nama sumber sudah dipakai")
                else:
                    db.save_source(draft["name"], draft["type"], draft["config"], draft["category"], draft["enabled"])
                    st.success(f"Sumber '{draft['name']}' disimpan")
                    st.rerun()

    with sub_edit:
        if not sources:
            st.info("Belum ada sumber.")
        else:
            by_id = {s["id"]: s for s in sources}
            sid = st.selectbox("Pilih sumber", list(by_id), format_func=lambda i: f"#{i} · {by_id[i]['name']}", key="edit_pick")
            draft = source_editor(f"edit{sid}", by_id[sid])
            if draft:
                c1, c2, c3 = st.columns([1, 1, 3])
                if c1.button("💾 Simpan perubahan", type="primary", key=f"save_{sid}"):
                    if not draft["name"]:
                        st.error("Nama wajib diisi")
                    elif any(s["name"] == draft["name"] and s["id"] != sid for s in sources):
                        st.error("Nama sudah dipakai sumber lain")
                    else:
                        db.save_source(draft["name"], draft["type"], draft["config"], draft["category"],
                                       draft["enabled"], source_id=sid)
                        sched.sync() if not CLOUD_MODE else None
                        st.success("Tersimpan")
                        st.rerun()
                confirm = c2.checkbox("Yakin hapus?", key=f"cdel_{sid}")
                if c2.button("🗑️ Hapus sumber", key=f"del_{sid}", disabled=not confirm):
                    db.delete_source(sid)
                    sched.sync() if not CLOUD_MODE else None
                    st.rerun()
                with c3:
                    preview(draft, f"prev_{sid}")

    with sub_preset:
        st.caption("Sumber contoh siap pakai. Klik **Tambah**, lalu cek di tab Jalankan. Situs pihak ketiga bisa "
                   "mengubah struktur/URL kapan saja.")
        existing = {s["name"] for s in sources}
        for i, p in enumerate(PRESETS):
            c1, c2, c3 = st.columns([4, 2, 1])
            c1.markdown(f"**{p['name']}**  \n{SOURCE_TYPES[p['type']]['label']}")
            c2.write(p["category"])
            if p["name"] in existing:
                c3.write("✔️ ada")
            elif c3.button("Tambah", key=f"preset_{i}"):
                db.save_source(p["name"], p["type"], p["config"], p["category"])
                st.rerun()

# ---------------------------------------------------------------- Jalankan
with tab_run:
    sources = db.list_sources()
    if not sources:
        st.info("Tambahkan sumber dulu di tab 📡 Sumber.")
    else:
        by_id = {s["id"]: s for s in sources}
        cats = sorted({s["category"] or "(tanpa kategori)" for s in sources})
        c1, c2 = st.columns([1, 3])
        pick_cat = c1.multiselect("Filter kategori", cats)
        pool = [s["id"] for s in sources if not pick_cat or (s["category"] or "(tanpa kategori)") in pick_cat]
        chosen = c2.multiselect("Sumber yang dijalankan", pool, default=[i for i in pool if by_id[i]["enabled"]],
                                format_func=lambda i: by_id[i]["name"])
        if st.button(f"▶️ Jalankan {len(chosen)} sumber sekarang", type="primary", disabled=not chosen):
            results = []
            bar = st.progress(0.0)
            status = st.empty()
            for n, sid in enumerate(chosen, 1):
                status.write(f"Memproses **{by_id[sid]['name']}** ({n}/{len(chosen)})…")
                results.append(run_source(sid, "manual"))
                bar.progress(n / len(chosen))
            status.empty()
            st.session_state["last_results"] = results
        if st.session_state.get("last_results"):
            st.subheader("Hasil run terakhir")
            show_run_results(st.session_state["last_results"])

# ---------------------------------------------------------------- Jadwal
with tab_sched:
    sources = db.list_sources()
    if not sources:
        st.info("Tambahkan sumber dulu.")
    else:
        by_id = {s["id"]: s for s in sources}
        st.subheader("Tambah jadwal")
        c1, c2, c3 = st.columns([2, 2, 2])
        targets = c1.multiselect("Sumber", list(by_id), format_func=lambda i: by_id[i]["name"], key="sch_src")
        mode = c2.selectbox("Mode", list(MODE_LABELS), format_func=MODE_LABELS.get, key="sch_mode")
        value = c3.text_input("Nilai", {"interval": "30", "daily": "07:30", "weekday": "08:45, 16:15",
                                        "cron": "*/15 9-16 * * mon-fri"}[mode], key=f"sch_val_{mode}")
        st.caption(f"{MODE_HINTS[mode]} · Zona waktu: {os.environ.get('SCRAPER_TZ', 'Asia/Jakarta')}")
        if st.button("➕ Tambah jadwal", type="primary", disabled=not targets):
            err = validate_trigger(mode, value)
            if err:
                st.error(err)
            else:
                for sid in targets:
                    db.add_schedule(sid, mode, value.strip())
                sched.sync() if not CLOUD_MODE else None
                st.success(f"{len(targets)} jadwal ditambahkan")
                st.rerun()

        st.subheader("Jadwal terdaftar")
        schedules = db.list_schedules()
        if not schedules:
            st.info("Belum ada jadwal.")
        else:
            nxt = {} if CLOUD_MODE else sched.next_runs()
            for s in schedules:
                box = st.container(border=True)
                box.markdown(f"**{s['source_name']}**" + ("" if s["source_enabled"] else " _(sumber nonaktif)_")
                             + f"  \n{MODE_LABELS[s['mode']]}: `{s['value']}`")
                nr = next_fire(s) if CLOUD_MODE else nxt.get(s["id"])
                last = f" · terakhir {s['last_fired_at'][5:16]}" if s.get("last_fired_at") else ""
                box.caption(f"Berikutnya: {nr.strftime('%d %b %H:%M') if nr else '—'}{last}")
                c4, c5 = box.columns(2)
                on = c4.toggle("On", bool(s["enabled"]), key=f"tg_{s['id']}")
                if on != bool(s["enabled"]):
                    db.set_schedule_enabled(s["id"], on)
                    sched.sync() if not CLOUD_MODE else None
                    st.rerun()
                if c5.button("🗑️ Hapus", key=f"dsch_{s['id']}"):
                    db.delete_schedule(s["id"])
                    sched.sync() if not CLOUD_MODE else None
                    st.rerun()
            st.caption("Mode cloud: GitHub Actions mengecek jadwal tiap ±15 menit, jadi interval < 15 menit "
                       "efektif jadi 15 menit dan waktu jalan bisa mundur beberapa menit." if CLOUD_MODE else
                       "Jadwal berjalan selama aplikasi (atau worker.py) hidup.")

# ---------------------------------------------------------------- Hasil
with tab_res:
    sources = db.list_sources()
    by_id = {s["id"]: s for s in sources}
    c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
    sel = c1.multiselect("Sumber", list(by_id), format_func=lambda i: by_id[i]["name"], key="res_src",
                         placeholder="Semua sumber")
    kw = c2.text_input("Cari kata kunci", key="res_kw")
    rng = c3.date_input("Rentang tanggal scrape", (date.today() - timedelta(days=30), date.today()), key="res_rng")
    limit = c4.number_input("Maks baris", 100, 200000, 5000, 100, key="res_lim")
    d_from, d_to = (rng[0], rng[1]) if isinstance(rng, (tuple, list)) and len(rng) == 2 else (None, None)
    raw = db.query_items(sel or None, kw.strip(), d_from and d_from.isoformat(), d_to and d_to.isoformat(), int(limit))
    df = build_results_frame(raw)

    if df.empty:
        st.info("Belum ada data sesuai filter. Jalankan scraping dulu di tab ▶️ Jalankan.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Baris", f"{len(df):,}".replace(",", "."))
        m2.metric("Sumber", df["source"].nunique())
        m3.metric("Kolom", len(df.columns))

        per_day = (df.assign(tanggal=pd.to_datetime(df["scraped_at"]).dt.date)
                     .groupby(["tanggal", "source"]).size().unstack(fill_value=0))
        if len(per_day) > 0:
            st.caption("Item tersimpan per hari")
            st.bar_chart(per_day, height=220)

        cols = st.multiselect("Kolom ditampilkan / diekspor", list(df.columns),
                              default=[c for c in df.columns if df[c].notna().any()], key="res_cols")
        view = df[cols] if cols else df
        st.dataframe(view, width="stretch", height=400, hide_index=True,
                     column_config={"url": st.column_config.LinkColumn("url"),
                                    "page_url": st.column_config.LinkColumn("page_url")})

        stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
        e1, e2, e3 = st.columns([1, 1, 2])
        e1.download_button("⬇️ Ekspor CSV", to_csv_bytes(view), f"scrape_{stamp}.csv", "text/csv",
                           width="stretch")
        per_source = e3.checkbox("Excel: satu sheet per sumber", True, key="xl_split")
        e2.download_button("⬇️ Ekspor Excel", to_excel_bytes(view, per_source=per_source), f"scrape_{stamp}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width="stretch")

    with st.expander("🧹 Kelola data"):
        st.write("Hapus item tersimpan (sumber & jadwal tetap ada).")
        target = "sumber terpilih" if sel else "SEMUA sumber"
        ok = st.checkbox(f"Saya yakin menghapus data dari {target}", key="del_items_ok")
        if st.button("Hapus data", disabled=not ok, key="del_items"):
            n = db.delete_items(sel or None)
            st.success(f"{n} item dihapus")
            st.rerun()

# ---------------------------------------------------------------- Log
with tab_log:
    runs = db.list_runs()
    if runs.empty:
        st.info("Belum ada riwayat run.")
    else:
        only_err = st.checkbox("Tampilkan error saja")
        if only_err:
            runs = runs[runs["status"] == "error"]
        icon = {"success": "✅", "error": "❌", "running": "⏳"}
        runs.insert(0, "", runs["status"].map(icon).fillna(""))
        st.dataframe(runs, width="stretch", hide_index=True, height=420)
