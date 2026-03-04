import io
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from difflib import get_close_matches
from typing import Any, Dict, List, Optional, Set, Tuple

import xml.etree.ElementTree as ET

from docx import Document
from pypdf import PdfReader

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageSequence
except ImportError:  # Pillow belum terpasang
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageSequence = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

try:
    import pytesseract
except ImportError:  # pytesseract belum terpasang
    pytesseract = None

_TESSERACT_NOT_FOUND = getattr(pytesseract, 'TesseractNotFoundError', RuntimeError) if pytesseract else RuntimeError
OCR_CONFIG = '--psm 6'

from gen_utils import load_employees

MONTH_MAP = {
    'januari': 1,
    'februari': 2,
    'maret': 3,
    'april': 4,
    'mei': 5,
    'juni': 6,
    'juli': 7,
    'agustus': 8,
    'september': 9,
    'oktober': 10,
    'november': 11,
    'desember': 12,
}

MONTH_CANON = {name: name.title() for name in MONTH_MAP.keys()}
NUMBER_WORDS = {
    'satu': 1,
    'dua': 2,
    'tiga': 3,
    'empat': 4,
    'lima': 5,
    'enam': 6,
    'tujuh': 7,
    'delapan': 8,
    'sembilan': 9,
    'sepuluh': 10,
    'sebelas': 11,
    'dua belas': 12,
    'tiga belas': 13,
    'empat belas': 14,
    'lima belas': 15,
}

DATE_RE = re.compile(
    r'(\d{1,2})\s+(' + '|'.join(MONTH_MAP.keys()) + r')\s+(\d{4})',
    flags=re.IGNORECASE,
)
MONTH_YEAR_RE = re.compile(
    r'(' + '|'.join(MONTH_MAP.keys()) + r')\s+(\d{4})',
    flags=re.IGNORECASE,
)


def _read_text_from_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    texts = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ''
        except Exception:
            txt = ''
        texts.append(txt)
    return '\n'.join(texts)


def _read_text_from_docx(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    return '\n'.join(p.text for p in doc.paragraphs)


def _read_text_from_docx_zip(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml_bytes = zf.read('word/document.xml')
    except KeyError:
        raise ValueError('Tidak menemukan konten utama di DOCX.')

    root = ET.fromstring(xml_bytes)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    paragraphs: List[str] = []
    for p in root.findall('.//w:p', ns):
        texts: List[str] = []
        for t in p.findall('.//w:t', ns):
            if t.text:
                texts.append(t.text)
        paragraph = ''.join(texts).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return '\n'.join(paragraphs)


def _ocr_image_frame(frame) -> str:
    img = frame
    try:
        img = img.convert('L')  # grayscale
    except Exception:
        pass
    try:
        img = ImageEnhance.Contrast(img).enhance(1.6)
    except Exception:
        pass
    try:
        img = img.filter(ImageFilter.MedianFilter(size=3))
    except Exception:
        pass

    last_text = ''
    for lang in ('ind', 'eng'):
        try:
            text = pytesseract.image_to_string(img, lang=lang, config=OCR_CONFIG)
        except _TESSERACT_NOT_FOUND as exc:
            raise ValueError('Tesseract OCR belum terpasang di server.') from exc
        except Exception:
            text = ''
        if text and text.strip():
            return text
        last_text = text or last_text
    return last_text


def _read_text_from_image(data: bytes, ext: Optional[str] = None) -> str:
    if Image is None or ImageEnhance is None or ImageFilter is None or pytesseract is None:
        raise ValueError('Dukungan OCR belum tersedia (library Pillow/pytesseract tidak ditemukan).')
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        # Coba fallback HEIF manual
        if ext and ext.lower() in {'heif', 'heic', 'heics'}:
            if pillow_heif is None:
                raise ValueError('Format HEIF memerlukan paket pillow-heif dan libheif di sistem.') from exc
            try:
                heif = pillow_heif.read_heif(data)
                mode = heif.mode or 'RGB'
                img = Image.frombytes(mode, heif.size, heif.data, 'raw')
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
            except Exception as heif_exc:
                raise ValueError('Gagal membuka file HEIF. Pastikan file tidak rusak dan dukungan HEIF terpasang.') from heif_exc
        else:
            raise ValueError('Gagal membuka file gambar.') from exc

    try:
        exif = getattr(img, '_getexif', lambda: None)()
        orientation = exif.get(274) if isinstance(exif, dict) else None
        if orientation == 3:
            img = img.rotate(180, expand=True)
        elif orientation == 6:
            img = img.rotate(270, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)
    except Exception:
        pass

    frames = []
    if ImageSequence is not None:
        try:
            for frame in ImageSequence.Iterator(img):
                frames.append(frame.copy())
        except Exception:
            frames = []
    if not frames:
        frames = [img]

    texts = []
    for frame in frames:
        text = _ocr_image_frame(frame)
        if text:
            texts.append(text.strip())
    combined = '\n'.join(t for t in texts if t).strip()
    return combined


def _normalize_line(line: str) -> str:
    line = unicodedata.normalize('NFKC', line)
    line = line.replace('\ufeff', '').strip()
    line = re.sub(r'\s+', ' ', line)
    return line.strip()


_SECTION_LABELS = ['dasar', 'kepada', 'untuk', 'waktu', 'pada tanggal']
_SECTION_PATTERN = re.compile(
    r'^[\s0-9ivxlcdm\.\-()]*\s*(dasar|kepada|untuk|waktu|pada tanggal)\b',
    re.IGNORECASE,
)


def _split_sections(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {k: [] for k in _SECTION_LABELS}
    current: Optional[str] = None

    for raw in text.splitlines():
        line = _normalize_line(raw)
        if not line:
            continue
        lowered = line.lower()
        if lowered in {'menugaskan', 'menugaskan:'}:
            current = None
            continue
        if lowered.startswith('dikeluarkan di') or lowered.startswith('kepala dinas'):
            current = None
            continue

        match = _SECTION_PATTERN.match(line)
        if match:
            current = match.group(1).lower()
            remainder = line[match.end():].strip(' :.-\t')
            if remainder:
                sections[current].append(remainder)
            continue

        if current:
            if line.startswith(':'):
                without = line[1:].strip()
                if without:
                    sections[current].append(without)
                continue
            sections[current].append(line)
    return sections


def _split_spt_chunks(text: str) -> List[str]:
    pattern = re.compile(r'(?im)^[\s0-9ivxlcdm\.\-()]*dasar\b')
    matches = list(pattern.finditer(text))
    if len(matches) <= 1:
        return [text]
    starts = [0]
    for match in matches[1:]:
        starts.append(match.start())
    ends = [match.start() for match in matches[1:]] + [len(text)]
    chunks: List[str] = []
    for start, end in zip(starts, ends):
        chunk = text[start:end].strip()
        if not chunk:
            continue
        lowered = chunk.lower()
        if 'dasar' not in lowered or 'kepada' not in lowered:
            continue
        chunks.append(chunk)
    return chunks or [text]


def _clean_name(raw: str) -> str:
    cleaned = re.sub(r'^[0-9]+\.\s*', '', raw)
    cleaned = cleaned.strip(' .:;')
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


def _extract_names(lines: List[str]) -> List[str]:
    names: List[str] = []
    pending = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        # Handle patterns like "Nama :" or "Nama -"
        match = re.search(r'\bnama\b\s*[:.;-]?\s*(.+)', line, flags=re.IGNORECASE)
        if match:
            candidate = _clean_name(match.group(1))
            if candidate:
                names.append(candidate)
            pending = False
            continue
        # Some OCRs place value on next line after "Nama"
        if lowered == 'nama':
            pending = True
            continue
        if pending:
            candidate = _clean_name(line)
            if candidate:
                names.append(candidate)
            pending = False
            continue
        # Fallback: detect numbered bullet containing name without explicit keyword
        fallback = re.match(r'^[0-9]+\.?\s+([A-Za-z].+)', line)
        if fallback:
            candidate = _clean_name(fallback.group(1))
            if candidate:
                names.append(candidate)
    # Deduplicate while preserving order
    seen = set()
    unique_names = []
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_names.append(name)
    return unique_names


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode('ascii')
    normalized = normalized.lower()
    normalized = re.sub(r'[^a-z0-9]+', '', normalized)
    return normalized


def _strip_titles(name: str) -> str:
    base = name.split(',', 1)[0]
    return base.strip()


def _match_employees(names: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    df = load_employees()
    index: Dict[str, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        record = {
            'nama': row.get('nama', '') or '',
            'nip': row.get('nip', '') or '',
            'pangkat_gol': row.get('pangkat_gol', '') or '',
            'jabatan': row.get('jabatan', '') or '',
        }
        variants = {_normalize_name(record['nama']), _normalize_name(_strip_titles(record['nama']))}
        for key in variants:
            if key and key not in index:
                index[key] = record
    keys = list(index.keys())

    matched: List[Dict[str, Any]] = []
    missing: List[str] = []

    for name in names:
        target = _normalize_name(name)
        record = index.get(target)
        if not record and target:
            record = index.get(_normalize_name(_strip_titles(name)))
        if not record and target:
            close = get_close_matches(target, keys, n=1, cutoff=0.88)
            if close:
                record = index.get(close[0])
        if record:
            matched.append({'nama': record['nama'], 'nip': record['nip']})
        else:
            missing.append(name)
    return matched, missing


def _extract_total_days(lines: List[str]) -> Optional[int]:
    text = ' '.join(lines).lower()
    digit_match = re.search(r'(\d+)\s*(?:\([^)]*\))?\s*hari', text)
    if digit_match:
        try:
            return int(digit_match.group(1))
        except ValueError:
            pass
    for word, value in NUMBER_WORDS.items():
        if f'{word} hari' in text:
            return value
    return None


def _extract_date_range(lines: List[str]) -> Tuple[Optional[date], Optional[date]]:
    text = ' '.join(lines)

    months_pattern = '|'.join(MONTH_MAP.keys())
    dates: List[date] = []
    seen: Set[date] = set()

    def add_date(year: int, month: int, day: int):
        try:
            d = date(year, month, day)
        except ValueError:
            return
        if d not in seen:
            dates.append(d)
            seen.add(d)

    # Pattern: 08-09 April 2025
    pattern_same_month = re.compile(
        rf'(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})\s+({months_pattern})\s+(\d{{4}})',
        re.IGNORECASE,
    )
    for match in pattern_same_month.finditer(text):
        d1, d2, month_name, year = match.groups()
        month = MONTH_MAP.get(month_name.lower())
        if not month:
            continue
        add_date(int(year), month, int(d1))
        add_date(int(year), month, int(d2))

    # Pattern: 22 April - 2 Mei 2025
    pattern_cross_month = re.compile(
        rf'(\d{{1,2}})\s+({months_pattern})\s*[-–]\s*(\d{{1,2}})\s+({months_pattern})\s+(\d{{4}})',
        re.IGNORECASE,
    )
    for match in pattern_cross_month.finditer(text):
        d1, month1, d2, month2, year = match.groups()
        m1 = MONTH_MAP.get(month1.lower())
        m2 = MONTH_MAP.get(month2.lower())
        if not m1 or not m2:
            continue
        add_date(int(year), m1, int(d1))
        add_date(int(year), m2, int(d2))

    # Pattern: 22 April 2024 - 2 Mei 2025
    pattern_cross_year = re.compile(
        rf'(\d{{1,2}})\s+({months_pattern})\s+(\d{{4}})\s*[-–]\s*(\d{{1,2}})\s+({months_pattern})\s+(\d{{4}})',
        re.IGNORECASE,
    )
    for match in pattern_cross_year.finditer(text):
        d1, month1, year1, d2, month2, year2 = match.groups()
        m1 = MONTH_MAP.get(month1.lower())
        m2 = MONTH_MAP.get(month2.lower())
        if not m1 or not m2:
            continue
        add_date(int(year1), m1, int(d1))
        add_date(int(year2), m2, int(d2))

    # Fallback: individual dates
    for match in DATE_RE.finditer(text):
        day, month_name, year = match.groups()
        month = MONTH_MAP.get(month_name.lower())
        if not month:
            continue
        add_date(int(year), month, int(day))

    if not dates:
        return None, None

    dates.sort()
    return dates[0], dates[-1]


def _extract_spt_date(lines: List[str]) -> Tuple[Optional[date], Optional[str]]:
    text = ' '.join(lines)
    date_match = DATE_RE.search(text)
    if date_match:
        day, month_name, year = date_match.groups()
        month = MONTH_MAP.get(month_name.lower())
        if month:
            try:
                spt_date = date(int(year), month, int(day))
                return spt_date, MONTH_CANON.get(month_name.lower(), month_name.title())
            except ValueError:
                pass
    month_match = MONTH_YEAR_RE.search(text)
    if month_match:
        month_name, year = month_match.groups()
        month_key = month_name.lower()
        return None, MONTH_CANON.get(month_key, month_name.title())
    return None, None


def _scan_single_spt(text: str) -> Dict[str, Any]:
    sections = _split_sections(text)
    dasar = ' '.join(sections.get('dasar', [])).strip()
    perihal = ' '.join(sections.get('untuk', [])).strip()
    names = _extract_names(sections.get('kepada', []))
    matched_emps, missing_emps = _match_employees(names)

    waktu_lines = sections.get('waktu', [])
    total_days = _extract_total_days(waktu_lines)
    start_date, end_date = _extract_date_range(waktu_lines)

    if start_date and not end_date:
        if total_days and total_days > 1:
            end_date = start_date + timedelta(days=total_days - 1)
        else:
            end_date = start_date
    if start_date and end_date and (not total_days or total_days <= 0):
        diff = (end_date - start_date).days
        if diff >= 0:
            total_days = diff + 1
    if not start_date and end_date:
        start_date = end_date

    spt_date, bulan_spt = _extract_spt_date(sections.get('pada tanggal', []))

    return {
        'dasar': dasar,
        'perihal': perihal,
        'pegawai': matched_emps,
        'pegawai_raw': names,
        'pegawai_unmatched': missing_emps,
        'total_hari': total_days,
        'tanggal_pergi': start_date.isoformat() if start_date else None,
        'sampai_tanggal': end_date.isoformat() if end_date else None,
        'tanggal_spt': spt_date.isoformat() if spt_date else None,
        'bulan_spt': bulan_spt,
        'waktu_text': ' '.join(waktu_lines).strip(),
        'pada_tanggal_text': ' '.join(sections.get('pada tanggal', [])).strip(),
    }


def read_text_from_file(data: bytes, filename: str) -> str:
    if not data:
        return ''
    if not filename:
        raise ValueError('Nama file tidak valid.')
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return _read_text_from_pdf(data)
    if ext == 'docx':
        try:
            return _read_text_from_docx(data)
        except Exception as exc:
            msg = str(exc).lower()
            if 'expected pattern' in msg or 'package not found' in msg or 'not a zip file' in msg:
                return _read_text_from_docx_zip(data)
            raise
    if ext == 'txt':
        return data.decode('utf-8', errors='ignore')
    if ext in {'jpg', 'jpeg', 'png', 'bmp', 'tif', 'tiff', 'heic', 'heif', 'heics'}:
        return _read_text_from_image(data, ext)
    raise ValueError('Format file belum didukung. Gunakan PDF, DOCX, atau gambar (JPG/PNG/HEIC).')


def scan_spt_file(data: bytes, filename: str) -> Dict[str, Any]:
    text = read_text_from_file(data, filename)
    if not text.strip():
        raise ValueError('Tidak menemukan teks pada file. Pastikan dokumen bukan hasil scan gambar.')

    chunks = _split_spt_chunks(text)
    entries: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for chunk in chunks:
        result = _scan_single_spt(chunk)
        key = (
            (result.get('dasar') or '').strip().lower(),
            (result.get('perihal') or '').strip().lower(),
            result.get('tanggal_pergi') or '',
            result.get('sampai_tanggal') or '',
        )
        if key in seen:
            continue
        seen.add(key)
        result['batch_index'] = len(entries) + 1
        entries.append(result)

    if not entries:
        return _scan_single_spt(text)

    if len(entries) == 1:
        return entries[0]

    primary = {**entries[0]}
    primary['batch'] = entries
    return primary
