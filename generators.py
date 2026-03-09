from typing import Dict, Any, List, Tuple, Optional
import io
from datetime import datetime, date

from docx import Document
from docx.text.paragraph import Paragraph
from docx.shared import Cm, Pt
import base64
from openpyxl import load_workbook
from openpyxl.styles import Font

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
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []
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
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []
    extras: List[Dict[str, Any]] = payload.get('pengeluaran_tambahan') or []

    # Remove extra rows beyond count
    n = len(employees)
    tokens_to_remove = []
    for i in range(n+1, 7):
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
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []

    # Remove extra rows
    n = len(employees)
    tokens_to_remove = []
    for i in range(n+1, 7):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}', f'JABPEG{i}', f'JABBEG{i}', f'PANGPEG{i}']
    remove_table_rows_with_tokens(doc, tokens_to_remove)

    bulan_spt_raw = payload.get('bulan_spt')
    if not bulan_spt_raw or str(bulan_spt_raw).strip().lower() == 'kosongkan':
        bulan_spt = ' ' * 12
    else:
        bulan_spt = str(bulan_spt_raw).strip()

    kad_choice = (payload.get('kepala_dinas') or 'efandri').strip().lower()
    if kad_choice == 'efandri':
        x1e = ''
        namkad = 'Efandri, S.T., M.M.'
        pangkad = 'Pembina /IV.a'
        nikad = 'NIP 197307082006041015'
    elif kad_choice == 'oben':
        x1e = ''
        namkad = 'H. Beni Akbari, S.T., M.M.'
        pangkad = 'Pembina Utama Muda/ IV.c'
        nikad = 'NIP 197901082003121006'
    else:
        x1e = 'Plt.'
        namkad = 'Efandri, S.T., M.M.'
        pangkad = 'Pembina /IV.a'
        nikad = 'NIP 197307082006041015'

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
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []
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

    # Cleanup when pegawai < 6: remove only the lines (paragraphs) with tokens in cells,
    # NOT entire rows, so headers/other columns stay.
    n = len(employees)
    tokens_to_remove = []
    for i in range(n+1, 7):
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
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []
    extras: List[Dict[str, Any]] = payload.get('pengeluaran_tambahan') or []

    # Remove extra rows if template has table rows for extra pegs
    n = len(employees)
    tokens_to_remove = []
    for i in range(n+1, 7):
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


def generate_nota_dinas(payload: Dict[str, Any]) -> Tuple[bytes, str, str]:
    template_path = 'kode_files/kode_nota_dinas.docx'
    doc = Document(template_path)
    # Semua output: Times New Roman, 11pt
    set_document_font(doc, 'Times New Roman', 11)

    perihal = payload.get('perihal') or ' '
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []
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
    for i in range(n+1, 7):
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
    employees: List[Dict[str, Any]] = payload.get('pegawai_list') or []

    n = len(employees)
    tokens_to_remove = []
    for i in range(n+1, 7):
        tokens_to_remove += [f'PEG{i}', f'NIPPEG{i}']
    remove_table_rows_with_tokens(doc, tokens_to_remove)

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

    # Handle photos: expect base64 strings in payload['fotos'] dict: fot1, fot2, fot3
    fotos = payload.get('fotos') or {}
    def _insert_photo(token: str, b64data: str):
        if not b64data:
            return
        try:
            if ',' in b64data:
                b64data = b64data.split(',',1)[1]
            img_bytes = base64.b64decode(b64data)
        except Exception:
            return
        # Find paragraph containing the token
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        if token in p.text:
                            # Clear token, then insert picture in same paragraph
                            text = p.text.replace(token, '')
                            for r in p.runs:
                                r.text = ''
                            run = p.add_run()
                            run.add_picture(io.BytesIO(img_bytes), width=Cm(9.5))
                            if text.strip():
                                p.add_run(text)
                            return
        for p in doc.paragraphs:
            if token in p.text:
                text = p.text.replace(token, '')
                for r in p.runs:
                    r.text = ''
                run = p.add_run()
                run.add_picture(io.BytesIO(img_bytes), width=Cm(9.5))
                if text.strip():
                    p.add_run(text)
                return

    _insert_photo('FOT1', fotos.get('fot1'))
    _insert_photo('FOT2', fotos.get('fot2'))
    _insert_photo('FOT3', fotos.get('fot3'))

    bio = io.BytesIO()
    doc.save(bio)
    filename = "Laporan Perjalanan Dinas.docx"
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return bio.getvalue(), filename, mime
