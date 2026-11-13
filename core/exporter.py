"""Bentuk tabel hasil dan ekspor ke CSV / Excel."""
from __future__ import annotations

import io
import json
import re

import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE_COLS = ["id", "source", "category", "title", "url", "published", "scraped_at"]


def build_results_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Pecah kolom JSON 'data' menjadi kolom-kolom biasa."""
    if raw.empty:
        return pd.DataFrame(columns=BASE_COLS)
    data = pd.json_normalize([json.loads(x) if x else {} for x in raw["data"]])
    data = data.drop(columns=[c for c in ("title", "url", "published") if c in data.columns])
    data.index = raw.index
    df = pd.concat([raw[BASE_COLS], data], axis=1)
    return df


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    # utf-8-sig supaya Excel membaca karakter Indonesia dengan benar
    return df.to_csv(index=False).encode("utf-8-sig")


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]\:\*\?\/\\]", "_", name)[:28] or "Sheet"
    candidate, i = base, 2
    while candidate.lower() in used:
        candidate = f"{base[:25]}_{i}"
        i += 1
    used.add(candidate.lower())
    return candidate


def _sanitize(df: pd.DataFrame) -> pd.DataFrame:
    def fix(v):
        if isinstance(v, str):
            return ILLEGAL_CHARACTERS_RE.sub("", v)[:32000]
        return v
    return df.apply(lambda col: col.map(fix) if not pd.api.types.is_numeric_dtype(col) else col)


def _format_sheet(ws, df: pd.DataFrame) -> None:
    header_fill = PatternFill("solid", fgColor="4B2E83")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    for idx, col in enumerate(df.columns, start=1):
        sample = df[col].head(200).map(lambda v: "" if v is None or v != v else str(v))
        width = max([len(str(col))] + [len(s) for s in sample]) + 2
        ws.column_dimensions[get_column_letter(idx)].width = min(max(width, 8), 60)
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def to_excel_bytes(df: pd.DataFrame, per_source: bool = True) -> bytes:
    buf = io.BytesIO()
    used: set[str] = set()
    df = _sanitize(df)
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        has_src = "source" in df.columns
        if has_src and not df.empty:
            keys = ["source"] + (["category"] if "category" in df.columns else [])
            agg = {"jumlah_item": ("source", "size")}
            if "scraped_at" in df.columns:
                agg.update(pertama=("scraped_at", "min"), terakhir=("scraped_at", "max"))
            summary = df.groupby(keys, dropna=False).agg(**agg).reset_index()
        else:
            summary = pd.DataFrame({"jumlah_item": [len(df)]})
        sheets = [("Ringkasan", summary), ("Semua Data", df.dropna(axis=1, how="all"))]
        if per_source and has_src and not df.empty:
            for name, part in df.groupby("source"):
                sheets.append((str(name), part.dropna(axis=1, how="all")))
        for name, part in sheets:
            sheet = _safe_sheet_name(name, used)
            part.to_excel(writer, sheet_name=sheet, index=False)
            _format_sheet(writer.sheets[sheet], part)
    return buf.getvalue()
