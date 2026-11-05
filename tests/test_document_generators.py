import io
import re
import unittest
from unittest.mock import patch

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from generators import generate_laporan_perjalanan, generate_nota_dinas, _remove_table_borders


def payload(count):
    return {
        'tanggal_pergi': '2026-11-05', 'sampai_tanggal': '2026-11-06',
        'total_hari': 2, 'nomor_spt': '090/123/2026',
        'tujuan': 'Palembang', 'tempat': 'Palembang',
        'perihal': 'Koordinasi pelaksanaan kegiatan',
        'dasar': 'Surat Perintah Tugas Nomor 090/123/2026',
        'poin_list': ['Melaksanakan koordinasi dan pembahasan kegiatan.'],
        'pegawai_list': [{'nama': f'Peserta Uji {i:02d}', 'nip': f'1990010120200110{i:02d}'}
                         for i in range(1, count + 1)],
    }


class DocumentGeneratorTests(unittest.TestCase):
    @patch('generators.get_employee_by_name', return_value={'pangkat_gol': 'Penata / III.c', 'jabatan': 'Staf'})
    def test_report_keeps_every_employee_for_all_supported_counts(self, _lookup):
        for count in range(1, 9):
            with self.subTest(count=count):
                data = payload(count)
                output, _, _ = generate_laporan_perjalanan(data)
                doc = Document(io.BytesIO(output))
                text = '\n'.join(doc.element.xpath('.//w:t/text()'))
                for employee in data['pegawai_list']:
                    self.assertIn(employee['nama'], text)
                    self.assertIn(employee['nip'], text)
                self.assertIsNone(re.search(r'(?:NIP|PANG|JAB)?PEG\d+', text))
                for unused in range(count + 1, 9):
                    self.assertNotIn(f'Peserta Uji {unused:02d}', text)

    @patch('generators.get_employee_by_name', return_value={})
    def test_nota_dinas_has_no_visible_table_borders_and_keeps_employees(self, _lookup):
        for count in (1, 6, 8):
            with self.subTest(count=count):
                data = payload(count)
                output, _, _ = generate_nota_dinas(data)
                doc = Document(io.BytesIO(output))
                text = '\n'.join(doc.element.xpath('.//w:t/text()'))
                for employee in data['pegawai_list']:
                    self.assertIn(employee['nama'], text)
                for table in doc.tables:
                    borders = table._tbl.tblPr.find(qn('w:tblBorders'))
                    self.assertIsNotNone(borders)
                    for edge in ('top', 'bottom', 'left', 'right', 'insideH', 'insideV'):
                        self.assertEqual(borders.find(qn('w:' + edge)).get(qn('w:val')), 'nil')
                for borders in doc.element.xpath('.//w:tblBorders | .//w:tcBorders'):
                    self.assertTrue(all(edge.get(qn('w:val')) == 'nil' for edge in borders))

    def test_border_removal_overrides_table_style_and_direct_cell_border(self):
        doc = Document()
        table = doc.add_table(rows=1, cols=1)
        table.style = 'Table Grid'
        borders = OxmlElement('w:tcBorders')
        edge = OxmlElement('w:bottom')
        edge.set(qn('w:val'), 'single')
        borders.append(edge)
        table.cell(0, 0)._tc.get_or_add_tcPr().append(borders)
        _remove_table_borders(doc)
        self.assertEqual(edge.get(qn('w:val')), 'nil')
        self.assertEqual(table._tbl.tblPr.find(qn('w:tblBorders')).find(qn('w:insideH')).get(qn('w:val')), 'nil')


if __name__ == '__main__':
    unittest.main()
