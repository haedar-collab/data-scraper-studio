# 🕸️ Data Scraper Studio

Aplikasi scraping dengan UI: pilih sumber → jadwalkan → lihat hasil → ekspor ke Excel/CSV.
Bisa dijalankan di PC (SQLite) atau di cloud dan dibuka dari HP (lihat bagian ☁️).

## Instalasi (sekali saja)

```bash
cd scraper-studio
python -m venv .venv
# Windows: .venv\Scripts\activate    |  Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Menjalankan

```bash
streamlit run app.py
```
Browser akan terbuka di `http://localhost:8501`. Data tersimpan di `data/scraper.db` (SQLite).

## Fitur

| Tab | Fungsi |
|---|---|
| 📡 Sumber | Tambah, edit, hapus sumber, dan **Preview** hasil tanpa menyimpan. Ada ⚡ Preset siap pakai. |
| ▶️ Jalankan | Jalankan banyak sumber sekaligus (bisa difilter per kategori) dengan progress bar. |
| ⏰ Jadwal | Jadwal tiap N menit, harian, **hari kerja Sen–Jum** (pas untuk jam bursa BEI), atau cron. |
| 📊 Hasil & Ekspor | Filter sumber/kata kunci/tanggal, grafik item per hari, pilih kolom, lalu ekspor **CSV** atau **Excel** (sheet ringkasan + satu sheet per sumber, header berformat, filter otomatis). |
| 📜 Log | Riwayat setiap run beserta pesan error. |

### Tipe sumber
- **RSS / Atom**: portal berita, blog, rilis pers
- **Google News**: pencarian berita per kata kunci (`IHSG`, `BBCA dividen`, `bitcoin OR ethereum`)
- **Halaman Web (CSS Selector)**: harga, katalog, direktori perusahaan. Mendukung paginasi `{page}`
- **JSON API**: API publik (CoinGecko, Binance, dsb.), mendukung path bertingkat & header API key
- **Portal Open Data (CKAN)**: pencarian dataset di portal berbasis CKAN

### Cara menulis field HTML
Satu baris per field: `nama = selector` untuk mengambil teks, atau `nama = selector@atribut` untuk mengambil atribut.
```
title = h3 a@title
url   = h3 a@href        # link relatif otomatis jadi absolut
price = .price           # isi "price" di "Field angka" → kolom price_num
```
Cara cari selector: buka situs → klik kanan elemen → **Inspect** → klik kanan di DevTools → **Copy selector**.

### Mode simpan
- **Hanya item baru**: duplikat dibuang (cocok untuk berita)
- **Snapshot tiap run**: setiap run disimpan (cocok untuk riwayat harga / time series)

## ☁️ Versi cloud — bisa dibuka dari HP (gratis)

Arsitektur:
- **GitHub**: menyimpan kode. **GitHub Actions** mengecek jadwal tiap ±15 menit dan menjalankan scraping.
- **Neon** (PostgreSQL gratis): menyimpan sumber, jadwal, dan hasil, jadi data aman walau aplikasi tidur.
- **Streamlit Community Cloud**: tampilan web yang dibuka dari browser HP, dilindungi password.

### 1. Buat database (Neon)
1. Buka **neon.tech** → *Sign up with GitHub* → *Create project* (region: Singapore).
2. Di Dashboard, klik **Connect**, lalu salin *connection string* (`postgresql://...neon.tech/neondb?sslmode=require`).

### 2. Isi secret di GitHub
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `DATABASE_URL`, Value: connection string dari Neon.

Lalu buka tab **Actions** → aktifkan workflow bila diminta → pilih *Scheduled scraping* → **Run workflow** untuk tes.

### 3. Deploy tampilan (Streamlit Cloud)
1. Buka **share.streamlit.io** → *Sign in with GitHub*.
2. **Create app** → pilih repo ini, branch `main`, file `app.py`.
3. *Advanced settings → Secrets*, tempel:
   ```toml
   DATABASE_URL = "postgresql://...neon.tech/neondb?sslmode=require"
   APP_PASSWORD = "password-pilihanmu"
   ```
4. **Deploy**. Kamu akan mendapat link `https://....streamlit.app`. Buka di HP, lalu *Add to Home Screen* supaya tampil seperti aplikasi.

### Catatan mode cloud
- Jadwal dijalankan oleh GitHub Actions. Interval di bawah 15 menit efektif menjadi 15 menit, dan GitHub bisa terlambat beberapa menit saat sibuk.
- Tombol **Jalankan sekarang** di aplikasi tetap bisa dipakai kapan saja.
- Aplikasi Streamlit akan "tidur" kalau lama tidak dibuka. Buka link-nya lalu tunggu beberapa detik. Jadwal tetap jalan karena dijalankan oleh GitHub.
- Beberapa situs memblokir IP server cloud. Kalau suatu sumber selalu error di cloud tapi lancar di PC, itu penyebabnya.
- Kuota gratis: repo **publik** = GitHub Actions tanpa batas. Repo **privat** = 2.000 menit/bulan (cek tiap 15 menit ≈ 1 menit/run, jadi sekitar 2.900 menit/bulan). Untuk repo privat, ubah cron di `.github/workflows/scrape.yml` menjadi `*/30 * * * *`. Aman dibuat publik karena password dan database tersimpan sebagai *secret*, bukan di kode.

## Scraping otomatis 24/7 (lokal / VPS)
Jadwal berjalan selama `streamlit run app.py` hidup. Untuk server/VPS, pakai worker terpisah:
```bash
# terminal 1 — penjadwal
python worker.py
# terminal 2 — UI tanpa scheduler internal (agar job tidak dobel)
SCHEDULER_IN_APP=0 streamlit run app.py          # Windows PowerShell: $env:SCHEDULER_IN_APP=0; streamlit run app.py
```
Perubahan jadwal dari UI terbaca oleh worker dalam 30 detik.

Variabel lingkungan: `DATABASE_URL` (PostgreSQL; kosong = SQLite lokal), `APP_PASSWORD`, `SCRAPER_DB` (lokasi SQLite), `SCRAPER_TZ` (default `Asia/Jakarta`), `SCHEDULER_IN_APP` (`1`/`0`).

## Batasan & etika
- Situs yang konten utamanya dirender JavaScript (SPA) tidak terbaca oleh tipe HTML. Cari endpoint JSON-nya di DevTools → Network, lalu pakai tipe **JSON API**.
- Media sosial (Facebook, X, WhatsApp) tidak disertakan karena butuh login dan dilarang ToS. Pakai API resmi masing-masing platform.
- Default aplikasi mematuhi `robots.txt`, memberi jeda antar halaman, dan retry otomatis. Patuhi ToS situs dan jangan set interval terlalu rapat.
- URL preset pihak ketiga bisa berubah sewaktu-waktu. Cek dengan tombol Preview.

## Struktur
```
app.py            UI Streamlit
worker.py         Penjadwal headless (lokal/VPS)
cron_runner.py    Dipanggil GitHub Actions: jalankan jadwal jatuh tempo
.github/workflows Workflow GitHub Actions
core/scrapers.py  Mesin scraping + pembersih angka/harga
core/db.py        Database: PostgreSQL (cloud) / SQLite (lokal)
core/scheduler.py APScheduler
core/exporter.py  CSV & Excel
core/presets.py   Sumber contoh
```
