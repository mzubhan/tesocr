from typing import Dict, Any, List, Tuple, Optional
import io
from datetime import datetime, date
import re
import html
import ssl
import urllib.parse
import urllib.error
import urllib.request

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.shared import Cm, Pt
import base64
from openpyxl import load_workbook
from openpyxl.styles import Font
from PIL import Image
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

from gen_utils import (
    terbilang_rupiah,
    terbilang_id,
    format_date_id,
    format_date_id_title,
    panggil_biaya_dinas,
    save_workbook_to_bytes,
    copy_sheet,
    format_date_range_id,
    replace_placeholders_docx,
    remove_table_rows_with_tokens,
    set_document_font,
    get_employee_by_name,
    birthdate_from_nip,
    iter_block_items,
    MONTHS_ID,
)


MAX_EMPLOYEE_SLOTS = 8


def _parse_date(s: str) -> date:
    # Expect ISO yyyy-mm-dd from input; fallback try other formats
    return datetime.fromisoformat(s).date()


def _parse_year(value: Any, *, fallback: Optional[date] = None) -> int:
    raw = str(value or '').strip()
    try:
        year = int(raw)
        if 1900 <= year <= 2100:
            return year
    except (TypeError, ValueError):
        pass
    if fallback is not None:
        return fallback.year
    return datetime.now().year


def _apply_year(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:
        # Handle Feb 29 on non-leap years
        if d.month == 2 and d.day == 29:
            return date(year, 2, 28)
        raise


def _get_limited_employees(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get('pegawai_list')
    if not isinstance(raw, list):
        return []
    return raw[:MAX_EMPLOYEE_SLOTS]


def _unused_employee_slots(count: int) -> range:
    used = max(0, min(int(count or 0), MAX_EMPLOYEE_SLOTS))
    return range(used + 1, MAX_EMPLOYEE_SLOTS + 1)


def _apply_text_style(doc: Document, target: str, *, bold: Optional[bool] = None, size_pt: Optional[float] = None) -> None:
    if not target:
        return

    def _set_style(run):
        if bold is not None:
            run.font.bold = bold
        if size_pt is not None:
            run.font.size = Pt(size_pt)

    for block in iter_block_items(doc):
        if hasattr(block, 'runs'):
            for run in block.runs:
                if target in (run.text or ''):
                    _set_style(run)
        else:
            for row in block.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            if target in (run.text or ''):
                                _set_style(run)


def _find_paragraphs_with_token(doc: Document, token: str) -> List[Paragraph]:
    if not token:
        return []
    matches: List[Paragraph] = []
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            if token in (block.text or ''):
                matches.append(block)
        else:
            for row in block.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        if token in (para.text or ''):
                            matches.append(para)
    return matches


def _insert_paragraph_after(paragraph: Paragraph) -> Paragraph:
    new_p = OxmlElement('w:p')
    paragraph._p.addnext(new_p)
    return Paragraph(new_p, paragraph._parent)


_MAX_REMOTE_IMAGE_BYTES = 8 * 1024 * 1024


def _is_valid_image_bytes(data: bytes) -> bool:
    if not data:
        return False
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:
        return False


def _decode_base64_image(data_url_or_b64: Any) -> Optional[bytes]:
    raw = str(data_url_or_b64 or '').strip()
    if not raw:
        return None
    try:
        if ',' in raw:
            raw = raw.split(',', 1)[1]
        decoded = base64.b64decode(raw)
    except Exception:
        return None
    return decoded if _is_valid_image_bytes(decoded) else None


def _extract_drive_file_id(raw_url: Any) -> str:
    raw = str(raw_url or '').strip()
    if not raw:
        return ''
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return ''
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if host != 'drive.google.com':
        return ''
    path = parsed.path or ''
    m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', path)
    if m:
        return m.group(1)
    query = urllib.parse.parse_qs(parsed.query or '')
    return (query.get('id') or [''])[0].strip()


def _build_google_drive_candidate_urls(raw_url: Any) -> List[str]:
    raw = str(raw_url or '').strip()
    if not raw:
        return []
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return []
    if parsed.scheme not in ('http', 'https'):
        return []

    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if host.endswith('googleusercontent.com'):
        return [raw]
    if host != 'drive.google.com':
        return []

    file_id = _extract_drive_file_id(raw)
    if not file_id:
        return []
    qid = urllib.parse.quote(file_id)
    return [
        f"https://drive.usercontent.google.com/download?id={qid}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={qid}",
        f"https://drive.google.com/uc?export=view&id={qid}",
    ]


def _normalize_google_drive_public_url(raw_url: Any) -> str:
    candidates = _build_google_drive_candidate_urls(raw_url)
    return candidates[0] if candidates else ''


def _extract_drive_download_links_from_html(html_text: str) -> List[str]:
    links: List[str] = []
    if not html_text:
        return links
    text = html.unescape(html_text)
    patterns = [
        r'https://drive\.usercontent\.google\.com[^\s"\'<>]+',
        r'https://drive\.google\.com/uc\?[^\s"\'<>]+',
        r'/uc\?[^\s"\'<>]+',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            url = match.strip()
            if url.startswith('/'):
                url = f"https://drive.google.com{url}"
            links.append(url.replace('&amp;', '&'))
    # deduplicate while preserving order
    seen = set()
    uniq: List[str] = []
    for url in links:
        if url in seen:
            continue
        seen.add(url)
        uniq.append(url)
    return uniq


def _fetch_remote_image_bytes(url: str, opener, depth: int = 0) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with opener.open(req, timeout=20) as response:
        content_len = response.headers.get('Content-Length')
        if content_len:
            try:
                if int(content_len) > _MAX_REMOTE_IMAGE_BYTES:
                    return None
            except ValueError:
                pass
        content_type = str(response.headers.get('Content-Type') or '').lower()
        data = response.read(_MAX_REMOTE_IMAGE_BYTES + 1)
    if not data or len(data) > _MAX_REMOTE_IMAGE_BYTES:
        return None
    if _is_valid_image_bytes(data):
        return data
    if depth >= 1 and 'html' not in content_type:
        return None
    try:
        html_text = data.decode('utf-8', errors='ignore')
    except Exception:
        return None
    for next_url in _extract_drive_download_links_from_html(html_text):
        try:
            img = _fetch_remote_image_bytes(next_url, opener, depth + 1)
        except Exception:
            img = None
        if img:
            return img
    return None


def _is_ssl_cert_error(exc: Exception) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
        return 'CERTIFICATE_VERIFY_FAILED' in str(reason)
    return 'CERTIFICATE_VERIFY_FAILED' in str(exc)


def _download_public_image_bytes(raw_url: Any) -> Optional[bytes]:
    candidates = _build_google_drive_candidate_urls(raw_url)
    if not candidates:
        return None
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    last_error: Optional[Exception] = None
    for target_url in candidates:
        try:
            data = _fetch_remote_image_bytes(target_url, opener, depth=0)
        except Exception as exc:
            last_error = exc
            data = None
        if data:
            return data
    if last_error and _is_ssl_cert_error(last_error):
        insecure_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
        )
        for target_url in candidates:
            try:
                data = _fetch_remote_image_bytes(target_url, insecure_opener, depth=0)
            except Exception:
                data = None
            if data:
                return data
    return None


def is_supported_public_image_url(raw_url: Any) -> bool:
    return bool(_build_google_drive_candidate_urls(raw_url))


def resolve_public_image_bytes(raw_url: Any) -> Optional[bytes]:
    return _download_public_image_bytes(raw_url)


def resolve_public_image_data_url(raw_url: Any) -> Optional[str]:
    data = _prepare_image_bytes_for_docx(_download_public_image_bytes(raw_url))
    if not data:
        return None
    mime = 'image/jpeg'
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or '').upper()
        mime = {
            'PNG': 'image/png',
            'WEBP': 'image/webp',
            'GIF': 'image/gif',
            'BMP': 'image/bmp',
            'TIFF': 'image/tiff',
            'HEIF': 'image/heif',
            'HEIC': 'image/heic',
            'JPEG': 'image/jpeg',
            'JPG': 'image/jpeg',
        }.get(fmt, 'image/jpeg')
    except Exception:
        pass
    encoded = base64.b64encode(data).decode('ascii')
    return f"data:{mime};base64,{encoded}"


def _normalize_photo_payload_items(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    raw_items = payload.get('photo_items')
    if isinstance(raw_items, list):
        source = raw_items
    else:
        fotos = payload.get('fotos') if isinstance(payload.get('fotos'), dict) else {}
        foto_links = payload.get('foto_links') if isinstance(payload.get('foto_links'), dict) else {}
        source = [
            {'dataUrl': fotos.get('fot1'), 'link': foto_links.get('fot1')},
            {'dataUrl': fotos.get('fot2'), 'link': foto_links.get('fot2')},
            {'dataUrl': fotos.get('fot3'), 'link': foto_links.get('fot3')},
        ]

    items: List[Dict[str, str]] = []
    for item in source:
        if isinstance(item, str):
            link = str(item).strip()
            data_url = ''
        elif isinstance(item, dict):
            link = str(item.get('link') or item.get('url') or '').strip()
            data_url = str(item.get('dataUrl') or item.get('data_url') or item.get('foto') or '').strip()
        else:
            continue
        if not link and not data_url:
            continue
        items.append({'link': link, 'data_url': data_url})
    return items


def _prepare_image_bytes_for_docx(data: Optional[bytes]) -> Optional[bytes]:
    if not data or not _is_valid_image_bytes(data):
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or '').upper()
            if fmt in {'PNG', 'JPEG', 'JPG', 'GIF', 'BMP', 'TIFF'}:
                return data
            converted = img.convert('RGB') if img.mode not in {'RGB', 'L'} else img.copy()
            bio = io.BytesIO()
            converted.save(bio, format='PNG')
            return bio.getvalue()
    except Exception:
        return data


def generate_rincian_biaya(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_rincian_biaya.xlsx'
    wb = load_workbook(template_path)
    base_sheet_name = wb.sheetnames[0]

    nomor_spt = (payload.get('nomor_spt') or ' ').strip() or ' '
    # Tanggal SPT boleh kosong; jika kosong, gunakan fallback format tanggal pergi (tanpa hari)
    ttd = _parse_date(payload['tanggal_spt']) if (payload.get('tanggal_spt') or '').strip() else None
    t_mulai = _parse_date(payload['tanggal_pergi'])
    t_selesai = _parse_date(payload['sampai_tanggal'])
    total_hari = int(payload.get('total_hari') or 1)
    tujuan = payload.get('tujuan') or ''
    detail_tujuan = payload.get('detail_tujuan') or ''
    sumber_dana = (payload.get('sumber_dana') or 'rutin').lower()
    tahun_berkas = _parse_year(payload.get('tahun_berkas'), fallback=ttd or t_mulai)
    employees = _get_limited_employees(payload)
    extras: List[Dict[str, Any]] = payload.get('pengeluaran_tambahan') or []

    lumpsum = panggil_biaya_dinas(tujuan, detail_tujuan)

    # Prepare per-employee extras mapping
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for e in extras:
        n = e.get('nama') or ''
        by_name.setdefault(n, []).append(e)

    for emp in employees:
        name = emp.get('nama') or ''
        nip = emp.get('nip') or ''
        # Create or get sheet for this employee
        sheet = copy_sheet(wb, base_sheet_name, name or 'Sheet')

        # Fixed placements
        # Lumpsum + total hari
        sheet['G13'].value = lumpsum
        sheet['H13'].value = total_hari

        # Additional expenses per person
        def sum_for(kind: str) -> Dict[str, int]:
            total_amount = 0
            total_days = 0
            for item in by_name.get(name, []):
                if (item.get('jenis') or '').lower() == kind.lower():
                    amt = int(item.get('jumlah') or 0)
                    dy = int(item.get('hari') or 0)
                    total_amount += amt
                    total_days += dy
            return {'amount': total_amount, 'days': total_days}

        # Map to cells
        for kind, cell_g, cell_h, has_days in [
            ('angkutan setempat', 'G14', 'H14', True),
            ('uang saku', 'G15', 'H15', True),
            ('uang representasi', 'G16', 'H16', True),
            ('biaya transportasi', 'G17', None, False),
            ('biaya penginapan', 'G18', 'H18', True),
            ('taksi / sewa kendaraan', 'G19', 'H19', True),
        ]:
            v = sum_for(kind)
            sheet[cell_g].value = v['amount']
            if has_days and cell_h:
                # Jika jumlah hari tidak diisi (0), kosongkan sel.
                sheet[cell_h].value = (v['days'] if v['days'] else None)

        # Header/meta
        sheet['D5'].value = (tujuan or '').upper()
        # D4 default: tanggal SPT; jika kosong, tampilkan "      BULAN YYYY" dari tanggal pergi.
        if ttd is not None:
            sheet['D4'].value = format_date_id(_apply_year(ttd, tahun_berkas))
        else:
            dmy = format_date_id(_apply_year(t_mulai, tahun_berkas)).split()
            bulan_tahun = ' '.join(dmy[1:]) if len(dmy) >= 3 else format_date_id(_apply_year(t_mulai, tahun_berkas))
            sheet['D4'].value = f"      {bulan_tahun}"
        # I26: selalu ganti token THNX -> tahun berkas.
        # Jika Bulan Rincian Biaya dipilih, THNX diganti menjadi "<Bulan> <Tahun>".
        brb = (payload.get('bulan_rincian_biaya') or '').strip()
        i26_value = str(sheet['I26'].value or 'Yang menerima,      THNX')
        if brb and brb.lower() != 'pilih bulan':
            replacement = f"{brb.capitalize()} {tahun_berkas}"
        else:
            replacement = str(tahun_berkas)
        if 'THNX' in i26_value:
            sheet['I26'].value = i26_value.replace('THNX', replacement)
        elif brb and brb.lower() != 'pilih bulan':
            sheet['I26'].value = f"Yang menerima,      {replacement}"
        # D3: jika tanggal SPT kosong, bagian nomor dipaksa 8 spasi.
        nomor_d3 = ' ' * 8 if ttd is None else nomor_spt
        sheet['D3'].value = f"600/{nomor_d3}/SPPD/{tahun_berkas}"
        sheet['I31'].value = f"NIP. {nip}"
        # I30 = Nama Pegawai (bold & UPPERCASE)
        sheet['I30'].value = (name or '').upper()
        try:
            sheet['I30'].font = Font(bold=True, size=11)
            sheet['I31'].font = Font(size=11)
        except Exception:
            pass

        if sumber_dana == 'bidang':
            sheet['A30'].value = 'ANUGERAH MAULIDY, S.T.'
            sheet['A30'].font = Font(bold=True, size=11)
            sheet['A31'].value = 'NIP. 198412062023211013'
            sheet['A31'].font = Font(size=11)

        # A21 Terbilang uses total estimation similar to J20
        # Compute simple total = (G13*H13) + G14*H14 + G15*H15 + ... + G19*H19 (transport no days)
        def gv(addr):
            v = sheet[addr].value or 0
            try:
                return int(v)
            except Exception:
                try:
                    return float(v)
                except Exception:
                    return 0

        total = gv('G13') * (gv('H13') or 0) 
        total += gv('G14') * (gv('H14') or 0)
        total += gv('G15') * (gv('H15') or 0)
        total += gv('G16') * (gv('H16') or 0)
        total += gv('G17')
        total += gv('G18') * (gv('H18') or 0)
        total += gv('G19') * (gv('H19') or 0)
        sheet['A21'].value = f"Terbilang : {terbilang_rupiah(int(total))}"

    # Remove original base sheet if not needed
    try:
        del wb[base_sheet_name]
    except Exception:
        pass

    data = save_workbook_to_bytes(wb)
    filename = "Rincian Biaya.xlsx"
    mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return data, filename, mime


def _compute_tot_for_employee(name: str, total_hari: int, tujuan: str, detail_tujuan: str, extras: list) -> int:
    base = panggil_biaya_dinas(tujuan, detail_tujuan) * int(total_hari)
    total = base
    # Sum extras for this name
    for it in extras:
        if (it.get('nama') or '') != name:
            continue
        jenis = (it.get('jenis') or '').lower()
        amount = int(it.get('jumlah') or 0)
        hari = int(it.get('hari') or 0)
        if jenis == 'biaya transportasi':
            total += amount
        else:
            total += amount * (hari or 0)
    return int(total)


def generate_pengeluaran_riil(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_pengeluaran_riil.docx'
    doc = Document(template_path)
    # Semua output: Arial 10pt
    set_document_font(doc, 'Arial', 10)

    nomor_spt = (payload.get('nomor_spt') or ' ').strip() or ' '
    ttd = _parse_date(payload['tanggal_spt']) if payload.get('tanggal_spt') else _parse_date(payload['tanggal_pergi'])
    t_mulai = _parse_date(payload['tanggal_pergi'])
    t_selesai = _parse_date(payload['sampai_tanggal'])
    total_hari = int(payload.get('total_hari') or 1)
    tujuan = payload.get('tujuan') or ''
    detail_tujuan = payload.get('detail_tujuan') or ''
    perihal = payload.get('perihal') or ' '
    employees = _get_limited_employees(payload)
    extras: List[Dict[str, Any]] = payload.get('pengeluaran_tambahan') or []

    # Remove extra rows beyond count
    n = len(employees)
    tokens_to_remove = []
    for i in _unused_employee_slots(n):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}', f'TOTPEG{i}']
    remove_table_rows_with_tokens(doc, tokens_to_remove)

    mapping: Dict[str, str] = {}
    kumtot = 0
    for idx, emp in enumerate(employees, start=1):
        name = emp.get('nama') or ''
        nip = emp.get('nip') or ''
        info = get_employee_by_name(name) or {}
        jab = info.get('jabatan', '')
        pang = info.get('pangkat_gol', '')
        toti = _compute_tot_for_employee(name, total_hari, tujuan, detail_tujuan, extras)
        kumtot += toti
        mapping.update({
            f'PEG{idx}': name,
            f'NIPPEG{idx}': nip,
            f'TOTPEG{idx}': f"{toti:,}".replace(',', '.'),
            f'JABPEG{idx}': jab,
            f'PANGPEG{idx}': pang,
        })

    mapping.update({
        'KUMTOT': f"{kumtot:,}".replace(',', '.'),
        'TERTOT': terbilang_rupiah(kumtot),
        'NOMSUR': nomor_spt,
        'TANGSUR': format_date_id_title(ttd),
        'TUJSUR': perihal,
        'SAMSUR': format_date_range_id(t_mulai, t_selesai),
    })

    replace_placeholders_docx(doc, mapping, font_name='Arial', font_size_pt=10)

    # Bold KUMTOT and TERTOT wherever they appear (often in their own cells)
    def _bold_value(val: str):
        if not val:
            return
        from docx.text.paragraph import Paragraph
        for block in iter_block_items(doc):
            if isinstance(block, Paragraph):
                p = block
                txt = p.text or ''
                if val in txt:
                    pre, _, post = txt.partition(val)
                    p.clear()
                    if pre:
                        r1 = p.add_run(pre); r1.font.name = 'Arial'
                    rb = p.add_run(val); rb.font.bold = True; rb.font.name = 'Arial'
                    if post:
                        r2 = p.add_run(post); r2.font.name = 'Arial'

    _bold_value(mapping.get('KUMTOT'))
    _bold_value(mapping.get('TERTOT'))

    bio = io.BytesIO()
    doc.save(bio)
    filename = "Daftar Pengeluaran Riil.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime


def generate_spt(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_spt.docx'
    doc = Document(template_path)
    set_document_font(doc, 'Arial')

    t_mulai = _parse_date(payload['tanggal_pergi'])
    t_selesai = _parse_date(payload['sampai_tanggal'])
    total_hari = int(payload.get('total_hari') or 1)
    dasar = payload.get('dasar') or ' '
    perihal = payload.get('perihal') or ' '
    tahun_berkas = _parse_year(payload.get('tahun_berkas'), fallback=t_mulai)
    employees = _get_limited_employees(payload)

    # Remove extra rows
    n = len(employees)
    tokens_to_remove = []
    for i in _unused_employee_slots(n):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}', f'JABPEG{i}', f'JABBEG{i}', f'PANGPEG{i}']
    remove_table_rows_with_tokens(doc, tokens_to_remove)

    bulan_spt_raw = payload.get('bulan_spt')
    if not bulan_spt_raw or str(bulan_spt_raw).strip().lower() == 'kosongkan':
        bulan_spt = ' ' * 12
    else:
        bulan_spt = str(bulan_spt_raw).strip()

    kad_choice = (payload.get('kepala_dinas') or 'efandri').strip().lower()
    kad_map = {
        'plt_efandri': {
            'X1e': 'Plt.',
            'Namkad': 'Efandri, S.T., M.M.',
            'Pangkad': 'Pembina /IV.a',
            'Nikad': 'NIP 197307082006041015',
            'LWQ': 'KEPALA DINAS PEKERJAAN UMUM DAN PENATAAN RUANG KOTA PRABUMULIH',
            'NXX': '600',
        },
        'efandri': {
            'X1e': '',
            'Namkad': 'Efandri, S.T., M.M.',
            'Pangkad': 'Pembina /IV.a',
            'Nikad': 'NIP 197307082006041015',
            'LWQ': 'KEPALA DINAS PEKERJAAN UMUM DAN PENATAAN RUANG KOTA PRABUMULIH',
            'NXX': '600',
        },
        'oben': {
            'X1e': '',
            'Namkad': 'H. Beni Akbari, S.T., M.M.',
            'Pangkad': 'Pembina Utama Muda/ IV.c',
            'Nikad': 'NIP 197901082003121006',
            'LWQ': 'KEPALA DINAS PEKERJAAN UMUM DAN PENATAAN RUANG KOTA PRABUMULIH',
            'NXX': '600',
        },
        'anggi_kabid': {
            'X1e': '',
            'Namkad': 'Verzi Anggi Akbar, S.T., M.Si.',
            'Pangkad': 'Penata Tingkat I / III.d',
            'Nikad': 'NIP 198509112009031001',
            'LWQ': 'KEPALA BIDANG CIPTA KARYA DINAS PEKERJAAN UMUM DAN PENATAAN RUANG KOTA PRABUMULIH',
            'NXX': '640',
        },
        'efandri_ivb': {
            'X1e': '',
            'Namkad': 'Efandri, S.T., M.M.',
            'Pangkad': 'Pembina Tingkat I /IV.b',
            'Nikad': 'NIP 197307082006041015',
            'LWQ': 'KEPALA DINAS PEKERJAAN UMUM DAN PENATAAN RUANG KOTA PRABUMULIH',
            'NXX': '600',
        },
    }
    kad_data = kad_map.get(kad_choice, kad_map['efandri'])
    x1e = kad_data['X1e']
    namkad = kad_data['Namkad']
    pangkad = kad_data['Pangkad']
    nikad = kad_data['Nikad']
    lwq = kad_data['LWQ']
    nxx = kad_data['NXX']

    mapping: Dict[str, str] = {
        'DASAR1': dasar,
        'PERIHAL1': perihal,
        'TOTHAR1': f"{total_hari} ({terbilang_id(total_hari)}) hari",
        'TANGHAR1': format_date_range_id(t_mulai, t_selesai),
        'BULBUL': bulan_spt,
        'THNX': str(tahun_berkas),
        'X1e': x1e,
        'Namkad': namkad,
        'Pangkad': pangkad,
        'Nikad': nikad,
        'LWQ': lwq,
        'NXX': nxx,
    }

    for idx, emp in enumerate(employees, start=1):
        name = emp.get('nama') or ''
        nip = emp.get('nip') or ''
        info = get_employee_by_name(name) or {}
        jab = info.get('jabatan', '')
        pang = info.get('pangkat_gol', '')
        mapping.update({
            f'PEG{idx}': name,
            f'NIPPEG{idx}': nip,
            f'JABPEG{idx}': jab,   # versi token benar
            f'JABBEG{idx}': jab,   # template memiliki salah ketik JABBEG
            f'PANGPEG{idx}': pang,
        })

    # Output font size adjusted to 10.5 pt as requested
    replace_placeholders_docx(doc, mapping, font_name='Bookman Old Style', font_size_pt=10.5)
    _apply_text_style(doc, namkad, bold=True)
    _apply_text_style(doc, x1e, bold=True)
    _apply_text_style(doc, lwq, bold=True)

    bio = io.BytesIO()
    doc.save(bio)
    filename = "SPT.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime


def generate_sppd_hal_1(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_sppd_hal_1.docx'
    doc = Document(template_path)
    set_document_font(doc, 'Bookman Old Style', 10)

    signature_paragraphs = _find_paragraphs_with_token(doc, 'NAMKPA1')

    t_mulai = _parse_date(payload['tanggal_pergi'])
    t_selesai = _parse_date(payload['sampai_tanggal'])
    total_hari = int(payload.get('total_hari') or 1)
    tujuan = payload.get('tujuan') or ''
    perihal = payload.get('perihal') or ' '
    employees = _get_limited_employees(payload)
    kpa_raw = payload.get('kpa') or 'Kepala Bidang Cipta Karya'
    kpa_sel = kpa_raw.lower()
    tahun_berkas = _parse_year(payload.get('tahun_berkas'), fallback=t_mulai)

    kpa_map = {
        'kepala bidang cipta karya': ('Verzi Anggi Akbar, S.T., M.Si.', 'Penata Tk.I/III.d', '198509112009031001'),
        'kepala dinas': ('H. Beni Akbari, S.T., M.M.', 'Pembina Utama Muda/IV.c', '197901082003121000'),
        'sekretaris dinas': ('Lenggo Geni, S.T., M.Si.', 'Pembina /IV.a', '197807182007012000'),
        'kabid lama': ('Diandes Khaidir, ST, M.Si', 'Pembina /IV.a', '197512302005011003'),
    }
    namkpa, pangkpa, nipkpa = kpa_map.get(kpa_sel, ('', '', ''))

    kpa_display = kpa_raw
    if kpa_sel == 'kabid lama':
        kpa_display = 'Kepala Bidang Cipta Karya'

    # Cleanup when pegawai kurang dari slot template: remove only the lines (paragraphs) with tokens in cells,
    # NOT entire rows, so headers/other columns stay.
    n = len(employees)
    tokens_to_remove = []
    for i in _unused_employee_slots(n):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}', f'TANGLAHPEG{i}']
    try:
        from gen_utils import remove_cell_paragraphs_with_tokens
        remove_cell_paragraphs_with_tokens(doc, tokens_to_remove)
    except Exception:
        pass

    bulan_spt_raw = payload.get('bulan_spt')
    if not bulan_spt_raw or str(bulan_spt_raw).strip().lower() == 'kosongkan':
        bulan_spt = ' ' * 12
    else:
        bulan_spt = str(bulan_spt_raw).strip()
    mapping: Dict[str, str] = {
        'KPA1': kpa_display,
        'NAMKPA1': namkpa,
        'PANGKPA1': pangkpa,
        'NIPKPA1': nipkpa,
        'PERIHAL1': perihal,
        'TUJUAN1': tujuan,
        'LAMPER': f"{total_hari} ({terbilang_id(total_hari)}) Hari",
        'TANGBER': format_date_id_title(t_mulai),
        'TANGTIB': format_date_id_title(t_selesai),
        'BULBUL': bulan_spt,
        'THNX': str(tahun_berkas),
    }

    for idx, emp in enumerate(employees, start=1):
        name = emp.get('nama') or ''
        nip = emp.get('nip') or ''
        info = get_employee_by_name(name) or {}
        jab = info.get('jabatan', '')
        pang = info.get('pangkat_gol', '')
        mapping.update({
            f'PEG{idx}': name,
            f'NIPPEG{idx}': nip,
            f'JABPEG{idx}': jab,
            f'PANGPEG{idx}': pang,
        })
        if idx >= 2:
            mapping[f'TANGLAHPEG{idx}'] = birthdate_from_nip(nip)

    replace_placeholders_docx(doc, mapping, font_name='Bookman Old Style', font_size_pt=10)

    if namkpa and signature_paragraphs:
        for para in signature_paragraphs:
            if not para.runs:
                continue
            for run in para.runs:
                run.font.bold = True
                run.font.size = Pt(10)

    bio = io.BytesIO()
    doc.save(bio)
    filename = "SPPD Halaman 1.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime


def generate_permohonan_rutin(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_permohonan_rutin.docx'
    doc = Document(template_path)
    # Semua output: Calibri 12pt
    set_document_font(doc, 'Calibri', 12)

    nomor_spt = (payload.get('nomor_spt') or ' ').strip() or ' '
    t_mulai = _parse_date(payload['tanggal_pergi'])
    t_selesai = _parse_date(payload['sampai_tanggal'])
    total_hari = int(payload.get('total_hari') or 1)
    tujuan = payload.get('tujuan') or ''
    perihal = payload.get('perihal') or ' '
    employees = _get_limited_employees(payload)
    extras: List[Dict[str, Any]] = payload.get('pengeluaran_tambahan') or []

    # Remove extra rows if template has table rows for extra pegs
    n = len(employees)
    tokens_to_remove = []
    for i in _unused_employee_slots(n):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}', f'TOTPEG{i}']
    remove_table_rows_with_tokens(doc, tokens_to_remove)

    kumtot = 0
    mapping: Dict[str, str] = {
        'PERIHAL1': perihal,
        'TUJPER1': tujuan,
        'NOMSUR': nomor_spt,
        'TOTHAR1': f"{total_hari} ({terbilang_id(total_hari)}) Hari",
        'TANGHAR1': format_date_range_id(t_mulai, t_selesai),
    }
    if employees:
        mapping['PEG1'] = employees[0].get('nama') or ''

    for idx, emp in enumerate(employees, start=1):
        name = emp.get('nama') or ''
        toti = _compute_tot_for_employee(name, total_hari, tujuan, payload.get('detail_tujuan') or '', extras)
        kumtot += toti
        mapping[f'TOTPEG{idx}'] = f"{toti:,}".replace(',', '.')

    mapping['KUMTOT'] = f"{kumtot:,}".replace(',', '.')
    mapping['TERTOT'] = terbilang_id(kumtot)

    replace_placeholders_docx(doc, mapping, font_name='Calibri', font_size_pt=12)

    bio = io.BytesIO()
    doc.save(bio)
    filename = "Permohonan KPA Dana Rutin.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime


def _remove_table_borders(doc: Document) -> None:
    """Override inherited table borders as well as direct cell/row borders."""
    for table in doc.tables:
        properties = table._tbl.tblPr
        borders = properties.find(qn('w:tblBorders'))
        if borders is None:
            borders = OxmlElement('w:tblBorders')
            properties.append(borders)
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV', 'start', 'end'):
            border = borders.find(qn(f'w:{edge}'))
            if border is None:
                border = OxmlElement(f'w:{edge}')
                borders.append(border)
            border.set(qn('w:val'), 'nil')
    for borders in doc.element.xpath('.//w:tblBorders | .//w:tcBorders'):
        for border in borders:
            border.set(qn('w:val'), 'nil')


def _remove_unused_report_employees(doc: Document, count: int) -> None:
    """A template row can hold several employees; preserve every used slot."""
    unused = set(_unused_employee_slots(count))
    for table in doc.tables:
        for row in list(table.rows):
            slots = {int(slot) for slot in re.findall(r'PEG(\d+)', ' '.join(cell.text for cell in row.cells))}
            if slots and slots.issubset(unused):
                table._tbl.remove(row._tr)
                continue
            for cell in row.cells:
                for paragraph in list(cell.paragraphs):
                    slots = {int(slot) for slot in re.findall(r'PEG(\d+)', paragraph.text)}
                    if slots and slots.issubset(unused):
                        cell._tc.remove(paragraph._p)
                if not cell.paragraphs:
                    cell.add_paragraph()


def generate_nota_dinas(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_nota_dinas.docx'
    doc = Document(template_path)
    _remove_table_borders(doc)
    # Semua output: Times New Roman, 11pt
    set_document_font(doc, 'Times New Roman', 11)

    perihal = payload.get('perihal') or ' '
    employees = _get_limited_employees(payload)
    pakai_kabid_lama_raw = (payload.get('pakai_kabid_lama') or 'tidak').strip().lower()
    pakai_kabid_lama = pakai_kabid_lama_raw in {'ya', 'y', 'iya', 'true', '1'}

    # Date range + total hari
    total_raw = payload.get('total_hari')
    try:
        total_hari = int(total_raw)
    except (TypeError, ValueError):
        total_hari = 0
    if total_hari <= 0:
        total_hari = 1

    start_date = None
    end_date = None
    start_raw = (payload.get('tanggal_pergi') or '').strip()
    end_raw = (payload.get('sampai_tanggal') or '').strip()
    if start_raw:
        try:
            start_date = _parse_date(start_raw)
        except Exception:
            start_date = None
    if end_raw:
        try:
            end_date = _parse_date(end_raw)
        except Exception:
            end_date = None
    if start_date and not end_date:
        end_date = start_date
    if end_date and not start_date:
        start_date = end_date
    tahun_berkas = _parse_year(payload.get('tahun_berkas'), fallback=start_date or end_date)

    n = len(employees)
    tokens_to_remove = []
    for i in _unused_employee_slots(n):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}', f'JABPEG{i}', f'PANGPEG{i}']
    remove_table_rows_with_tokens(doc, tokens_to_remove)

    mapping: Dict[str, str] = {'PERIHAL1': perihal}
    for idx, emp in enumerate(employees, start=1):
        name = emp.get('nama') or ''
        nip = emp.get('nip') or ''
        info = get_employee_by_name(name) or {}
        jab = info.get('jabatan', '')
        pang = info.get('pangkat_gol', '')
        mapping.update({
            f'PEG{idx}': name,
            f'NIPPEG{idx}': nip,
            f'JABPEG{idx}': jab,
            f'PANGPEG{idx}': pang,
        })

    # Handle Kabid signature block tokens
    if pakai_kabid_lama:
        target_name = 'Diandes Khaidir, S.T, M.Si'
        target_pang = 'Pembina / IV.a'
        target_nip = 'NIP 197512302005011003'
    else:
        target_name = 'Verzi Anggi Akbar, S.T., M.Si.'
        target_pang = 'Penata Tk.I/III.d'
        target_nip = 'NIP 198509112009031001'

    mapping.update({
        'NAPKAB': target_name,
        'PANGKAB': target_pang,
        'NIPKAB': target_nip,
        'TOTHAR1': f"{total_hari} ({terbilang_id(total_hari)}) hari",
        'TANGHAR1': format_date_range_id(start_date, end_date) if start_date and end_date else '',
        'THNX': str(tahun_berkas),
    })

    replace_placeholders_docx(doc, mapping, font_name='Times New Roman', font_size_pt=11)
    _apply_text_style(doc, target_name, bold=True)

    bio = io.BytesIO()
    doc.save(bio)
    filename = "Nota Dinas.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime


def generate_laporan_perjalanan(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_laporan_perjalanan_dinas.docx'
    doc = Document(template_path)
    set_document_font(doc, 'Bookman Old Style', 11)

    nomor_spt = (payload.get('nomor_spt') or ' ').strip() or ' '
    t_mulai = _parse_date(payload['tanggal_pergi'])
    t_selesai = _parse_date(payload['sampai_tanggal'])
    tahun_berkas = _parse_year(payload.get('tahun_berkas'), fallback=t_mulai or t_selesai)
    total_hari = int(payload.get('total_hari') or 1)
    tujuan = payload.get('tujuan') or ''
    dasar = payload.get('dasar') or ' '
    perihal = payload.get('perihal') or ' '
    tempat = payload.get('tempat') or ' '
    employees = _get_limited_employees(payload)

    _remove_unused_report_employees(doc, len(employees))

    mapping: Dict[str, str] = {
        'DASAR1': dasar,
        'PERIHAL1': perihal,
        'TOTHAR1': f"{total_hari} ({terbilang_id(total_hari)}) Hari",
        'TANGHAR1': format_date_range_id(t_mulai, t_selesai),
        'TUJUAN1': tujuan,
        'TEMPAT1': tempat,
        'TUJTEM': tempat,  # beberapa template mungkin pakai token ini
        'BULBUL': MONTHS_ID[t_selesai.month-1] if t_selesai else '',
        'THNX': str(tahun_berkas),
    }
    for idx, emp in enumerate(employees, start=1):
        name = emp.get('nama') or ''
        nip = emp.get('nip') or ''
        info = get_employee_by_name(name) or {}
        mapping[f'PEG{idx}'] = name
        mapping[f'NIPPEG{idx}'] = nip
        pang = info.get('pangkat_gol', '')
        jab = info.get('jabatan', '')
        mapping[f'PANGPEG{idx}'] = pang or ''
        mapping[f'JABPEG{idx}'] = jab or ''

    # Points (optional): POIN1..6
    points = payload.get('poin_list') or []
    for i in range(1, 7):
        mapping[f'POIN{i}'] = (points[i-1] if i-1 < len(points) else '')

    replace_placeholders_docx(doc, mapping, font_name='Bookman Old Style', font_size_pt=11)

    photo_items = _normalize_photo_payload_items(payload)

    def _resolve_photo_bytes(item: Dict[str, str]) -> Optional[bytes]:
        from_payload = _decode_base64_image(item.get('data_url'))
        if from_payload:
            return _prepare_image_bytes_for_docx(from_payload)
        return _prepare_image_bytes_for_docx(resolve_public_image_bytes(item.get('link')))

    def _render_photo_paragraph(paragraph: Paragraph, img_bytes: Optional[bytes], token: str) -> Paragraph:
        text = (paragraph.text or '').replace(token, '').strip()
        for run in paragraph.runs:
            run.text = ''
        if img_bytes:
            paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            paragraph.add_run().add_picture(io.BytesIO(img_bytes), width=Cm(9.5))
        if text:
            paragraph.add_run(text)
        return paragraph

    photo_paragraphs = {
        token: (_find_paragraphs_with_token(doc, token) or [None])[0]
        for token in ('FOT1', 'FOT2', 'FOT3')
    }
    anchor_paragraph = photo_paragraphs.get('FOT3') or photo_paragraphs.get('FOT2') or photo_paragraphs.get('FOT1')

    for idx, token in enumerate(('FOT1', 'FOT2', 'FOT3')):
        paragraph = photo_paragraphs.get(token)
        if paragraph is None:
            continue
        item = photo_items[idx] if idx < len(photo_items) else None
        rendered = _render_photo_paragraph(paragraph, _resolve_photo_bytes(item) if item else None, token)
        anchor_paragraph = rendered or anchor_paragraph

    if anchor_paragraph is None:
        anchor_paragraph = doc.paragraphs[-1] if doc.paragraphs else doc.add_paragraph()

    for item in photo_items[3:]:
        img_bytes = _resolve_photo_bytes(item)
        if not img_bytes:
            continue
        anchor_paragraph = _insert_paragraph_after(anchor_paragraph)
        anchor_paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        anchor_paragraph.add_run().add_picture(io.BytesIO(img_bytes), width=Cm(9.5))

    bio = io.BytesIO()
    doc.save(bio)
    filename = "Laporan Perjalanan Dinas.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime
