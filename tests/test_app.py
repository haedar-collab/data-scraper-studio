"""Uji UI Streamlit secara headless."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def app_env(database, monkeypatch):
    monkeypatch.setenv("SCHEDULER_IN_APP", "0")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    import streamlit as st
    st.cache_data.clear()
    st.cache_resource.clear()
    return database


def new_app():
    return AppTest.from_file(APP, default_timeout=60)


def btn(at, label):
    return next(b for b in at.button if b.label.startswith(label))


def test_login_and_lockout(app_env, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "rahasia")
    at = new_app().run()
    assert not at.tabs
    for _ in range(5):
        at.text_input[0].input("salah")
        btn(at, "Masuk").click().run()
    assert "Terlalu banyak" in at.error[0].value
    at2 = new_app().run()
    at2.text_input[0].input(" rahasia ")
    btn(at2, "Masuk").click().run()
    assert not at2.exception and len(at2.tabs) > 5


def test_full_flow(app_env, server):
    db = app_env
    db.save_source("rss", "rss", {"url": f"{server}/feed.xml"}, "Saham")
    db.save_source("harga", "html", {"item_selector": "article.p", "url": f"{server}/page-1.html", "delay": 0,
                                     "fields": {"title": "h3 a@title", "price": ".price"}}, "Harga")
    at = new_app().run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert "⚙️ Sistem" in labels and "📊 Hasil & Ekspor" in labels

    btn(at, "▶️ Jalankan").click().run()
    assert not at.exception
    assert any("2 berhasil" in m.value for m in at.markdown)
    assert db.stats()["items"] == 4

    # Hasil: kolom dipilih, lalu filter sumber diubah → tidak boleh crash
    ms = next(m for m in at.multiselect if m.label.startswith("Kolom"))
    ms.set_value(["title", "price"]).run()
    assert not at.exception
    src = next(m for m in at.multiselect if m.label == "Sumber" and m.key == "res_src")
    src.set_value([1]).run()
    assert not at.exception

    # Preset ditambahkan dan cache ikut diperbarui
    btn(at, "Tambah").click().run()
    assert not at.exception and db.stats()["sources"] == 3

    # Jadwal: validasi input
    at.multiselect(key="sch_src").set_value([1]).run()
    at.selectbox(key="sch_mode").set_value("daily").run()
    at.text_input(key="sch_val_daily").input("25:00").run()
    btn(at, "➕ Tambah jadwal").click().run()
    assert any("tidak valid" in e.value for e in at.error)
    at.text_input(key="sch_val_daily").input("07:30").run()
    btn(at, "➕ Tambah jadwal").click().run()
    assert not at.exception and len(db.list_schedules()) == 1


def test_cloud_warnings(app_env, monkeypatch):
    if not app_env.is_postgres():
        pytest.skip("Banner mode cloud hanya relevan dengan PostgreSQL")
    sid = app_env.save_source("rss", "rss", {"url": "http://x"})
    app_env.add_schedule(sid, "interval", "30")
    at = new_app().run()
    texts = " ".join(w.value for w in at.warning)
    assert "APP_PASSWORD belum diisi" in texts and "3 jam" in texts
