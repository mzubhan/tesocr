import io
import os
import re
import zipfile
from datetime import datetime, date
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import pandas as pd
from num2words import num2words
from docx import Document  # usages elsewhere pass actual Document objects
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph
from docx.shared import Pt


# ---------- Employees ----------

_EMP_CACHE: Optional[pd.DataFrame] = None
_EMP_SRC_MTIME: Optional[float] = None


def load_employees(path: str = 'database_files/database_daftar_pegawai.xlsx') -> pd.DataFrame:
    global _EMP_CACHE, _EMP_SRC_MTIME
    # Detect file changes to auto-reload data
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if _EMP_CACHE is None or (_EMP_SRC_MTIME is not None and mtime is not None and mtime != _EMP_SRC_MTIME):
        # Read everything as text to avoid Excel numeric precision issues on long IDs like NIP
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
        # Normalize common column names (tolerate punctuation/case)
        def _norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]+', '', str(s).lower())
        norm_cols = {_norm(c): c for c in df.columns}
        mapping = {}
        specs = {
            'nama': ['nama', 'name'],
            'nip': ['nip'],
            'jabatan': ['jabatan', 'posisi', 'position'],
            'pangkat_gol': ['pangkat/gol', 'pangkat_gol', 'pang/gol', 'pangkat', 'golongan', 'pangkat/gol.'],
        }
        for logical, candidates in specs.items():
            for cand in candidates:
                key = _norm(cand)
                if key in norm_cols:
                    mapping[logical] = norm_cols[key]
                    break
        # Heuristics when expected columns aren't found
        # Try to infer 'nama' from any column containing 'nama'
        if 'nama' not in mapping:
            for nkey, orig in norm_cols.items():
                if 'nama' in nkey:  # e.g., 'namapegawai', 'namalengkap'
                    mapping['nama'] = orig
                    break
        # Try to infer 'nip' from any column containing 'nip'
        if 'nip' not in mapping:
            for nkey, orig in norm_cols.items():
                if 'nip' in nkey:
                    mapping['nip'] = orig
                    break
        # Try to infer 'jabatan'
        if 'jabatan' not in mapping:
            for nkey, orig in norm_cols.items():
                if 'jabatan' in nkey or 'posisi' in nkey or 'position' in nkey:
                    mapping['jabatan'] = orig
                    break
        # Try to infer 'pangkat_gol'
        if 'pangkat_gol' not in mapping:
            for nkey, orig in norm_cols.items():
                if 'pangkat' in nkey or 'gol' in nkey:
                    mapping['pangkat_gol'] = orig
                    break
        # Rename to standard keys: original -> logical
        df = df.rename(columns={orig: logical for logical, orig in mapping.items()})
        # Ensure NIP as plain string
        if 'nip' in df.columns:
            def _fmt_nip(v):
                s = str(v or '').strip()
                if not s or s.lower() == 'nan':
                    return ''
                # Remove spaces/dashes and non-digits
                s = s.replace(' ', '').replace('-', '')
                # Trim trailing .0 if present
                if s.endswith('.0'):
                    s = s[:-2]
                # If scientific notation sneaks in, best-effort: keep digits only
                s = ''.join(ch for ch in s if ch.isdigit())
                return s
            df['nip'] = df['nip'].apply(_fmt_nip)
        # Ensure expected columns exist to avoid KeyError downstream
        for col in ('nama', 'nip', 'jabatan', 'pangkat_gol'):
            if col not in df.columns:
                df[col] = ''
        _EMP_CACHE = df
        _EMP_SRC_MTIME = mtime
    return _EMP_CACHE


def search_employees(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    df = load_employees()
    if not query:
        res = df.head(limit)
    else:
        mask = df['nama'].astype(str).str.contains(re.escape(query), case=False, na=False)
        res = df[mask].head(limit)
    return res.fillna('').to_dict(orient='records')


def get_employee_by_name(name: str) -> Optional[Dict[str, Any]]:
    df = load_employees()
    row = df[df['nama'].astype(str) == str(name)]
    if not row.empty:
        return row.iloc[0].fillna('').to_dict()
    return None


# ---------- Formatting helpers ----------

MONTHS_ID = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
]


def terbilang_rupiah(n: int) -> str:
    words = num2words(int(n), lang='id')
    words_cap = ' '.join(w.capitalize() for w in words.split())
    return f"{words_cap} Rupiah"

def terbilang_id(n: int) -> str:
    words = num2words(int(n), lang='id')
    return ' '.join(w.capitalize() for w in words.split())


def format_date_id(d: date) -> str:
    return f"{d.day:02d} {MONTHS_ID[d.month-1].upper()} {d.year}"

def format_date_id_title(d: date) -> str:
    # "10 Juni 2025" (bulan kapital huruf awal saja)
    return f"{d.day} {MONTHS_ID[d.month-1]} {d.year}"


def format_date_range_id(start: date, end: date) -> str:
    if start == end:
        return f"{start.day} {MONTHS_ID[start.month-1]} {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day} - {end.day} {MONTHS_ID[end.month-1]} {end.year}"
    if start.year == end.year:
        return f"{start.day} {MONTHS_ID[start.month-1]} - {end.day} {MONTHS_ID[end.month-1]} {end.year}"
    return f"{start.day} {MONTHS_ID[start.month-1]} {start.year} - {end.day} {MONTHS_ID[end.month-1]} {end.year}"


def panggil_biaya_dinas(tujuan: str, detail: str) -> int:
    tujuan = (tujuan or '').strip().lower()
    # Normalize variants like "Prabumulih (DD)" to base name.
    if tujuan:
        tujuan = re.sub(r'\s*\(.*\)\s*$', '', tujuan).strip()
    detail = (detail or '').strip().lower()
    if tujuan == 'palembang' and 'bimtek' in detail:
        return 110_000
    if tujuan == 'palembang':
        # assume DL lainnya
        return 380_000
    if tujuan == 'prabumulih' and ('titik nol' in detail or 'titik' in detail):
        return 150_000
    if tujuan == 'prabumulih':
        return 150_000
    return 0


def birthdate_from_nip(nip: str) -> str:
    """Parse yyyyMMdd from beginning of NIP and return 'DD Bulan YYYY'."""
    if not nip:
        return ''
    s = ''.join(ch for ch in str(nip) if ch.isdigit())
    if len(s) < 8:
        return ''
    y = int(s[:4])
    m = int(s[4:6])
    d = int(s[6:8])
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return ''
    return f"{d} {MONTHS_ID[m-1]} {y}"


# ---------- DOCX helpers ----------

def iter_block_items(parent):
    if isinstance(parent, DocxDocument):
        parent_elmnts = parent.element.body
    else:
        parent_elmnts = parent._tc
    for child in parent_elmnts.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def replace_placeholders_docx(doc: Document, mapping: Dict[str, str], font_name: Optional[str] = None, font_size_pt: Optional[int] = None):
    # Replace longer tokens first to avoid partial collisions like 'PEG1' inside 'NIPPEG1'
    ordered_keys = sorted(mapping.keys(), key=len, reverse=True)

    def apply_font(run):
        if font_name:
            run.font.name = font_name
        if font_size_pt:
            run.font.size = Pt(font_size_pt)

    def rebuild_paragraph_with_text(p: Paragraph, text: str):
        # Clear existing runs while preserving paragraph properties, then rebuild with tabs support
        p.clear()
        segments = text.split('\t')
        for idx, segment in enumerate(segments):
            if segment:
                run = p.add_run(segment)
                apply_font(run)
            if idx < len(segments) - 1:
                tab_run = p.add_run()
                tab_run.add_tab()

    def replace_text_in_paragraph(p: Paragraph):
        current_text = p.text or ''
        if not current_text:
            return
        new_text = current_text
        for k in ordered_keys:
            new_text = new_text.replace(k, mapping[k])
        if new_text == current_text:
            return
        rebuild_paragraph_with_text(p, new_text)

    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            replace_text_in_paragraph(block)
        else:
            for row in block.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        replace_text_in_paragraph(p)


def remove_table_rows_with_tokens(doc: Document, tokens: List[str]):
    def cell_contains_any(cell: _Cell) -> bool:
        text = '\n'.join(p.text for p in cell.paragraphs)
        return any(tok in text for tok in tokens)

    for table in doc.tables:
        rows_to_delete = []
        for i, row in enumerate(table.rows):
            row_text = ' '.join(cell.text for cell in row.cells)
            if any(tok in row_text for tok in tokens):
                rows_to_delete.append(i)
        for idx in reversed(rows_to_delete):
            tbl = table._tbl
            tr = tbl.tr_lst[idx]
            tbl.remove(tr)

def remove_paragraphs_with_tokens(doc: Document, tokens: List[str]):
    pars = list(doc.paragraphs)
    for p in pars:
        text = p.text or ''
        if any(tok in text for tok in tokens):
            try:
                p._element.getparent().remove(p._element)
            except Exception:
                pass

def remove_cell_paragraphs_with_tokens(doc: Document, tokens: List[str]):
    """Remove only the paragraphs (lines) inside table cells that contain any token.
    Does NOT remove the entire row or cell, preserving the surrounding structure
    like headers or other columns.
    """
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                # Copy list to avoid mutating while iterating
                for p in list(cell.paragraphs):
                    text = p.text or ''
                    if any(tok in text for tok in tokens):
                        try:
                            p._element.getparent().remove(p._element)
                        except Exception:
                            pass


def set_document_font(doc: Document, font_name: str = 'Arial', font_size_pt: Optional[int] = None):
    style = doc.styles['Normal']
    style.font.name = font_name
    if font_size_pt:
        style.font.size = Pt(font_size_pt)


# ---------- XLSX helpers ----------

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


def copy_sheet(wb, source_name: str, new_name: str):
    src = wb[source_name]
    tgt = wb.copy_worksheet(src)
    tgt.title = new_name[:31]  # Excel sheet title limit
    return tgt


def save_workbook_to_bytes(wb) -> bytes:
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
