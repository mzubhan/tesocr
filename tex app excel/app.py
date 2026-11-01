import io
import zipfile
import re
from flask import Flask, request, send_file, send_from_directory
from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, column_index_from_string, coordinate_from_string
from openpyxl.utils.datetime import to_excel
from xml.sax.saxutils import escape, unescape

# This sets up the Flask application to serve static files (like index.html, style.css, script.js)
# from the current directory.
app = Flask(__name__, static_url_path='', static_folder='.')

@app.route('/')
def index():
    """Serves the main page of the application."""
    return send_from_directory('.', 'index.html')

@app.route('/convert', methods=['POST'])
def convert_file():
    """
    Handles file upload, removes only formulas that link to other sheets or files,
    and attempts to preserve images and shapes.
    """
    if 'file' not in request.files:
        return 'No file part in the request', 400
    
    file = request.files['file']
    if file.filename == '':
        return 'No file selected', 400

    if file:
        try:
            file_bytes = file.read()
            file_stream = io.BytesIO(file_bytes)

            # 1. Load workbook dengan formula (untuk deteksi target)
            wb_formulas = load_workbook(filename=file_stream, data_only=False)
            
            # 2. Load workbook dengan value (untuk ambil nilai hasil hitungan)
            file_stream.seek(0)
            wb_values = load_workbook(filename=file_stream, data_only=True)
            
            # Ambil mode konversi dari request (default: external)
            conversion_mode = request.form.get('conversionMode', 'external')

            # Mapping: sheet_name -> { coordinate: value }
            cells_to_update = {}

            sheets_to_process = wb_formulas.sheetnames

            for sheet_name in sheets_to_process:
                if sheet_name not in wb_values.sheetnames:
                    continue
                    
                ws_form = wb_formulas[sheet_name]
                ws_val = wb_values[sheet_name]
                sheet_updates = {}
                
                for row in ws_form.iter_rows():
                    for cell in row:
                        if cell.data_type == 'f':
                            f_text = str(cell.value) if cell.value else ""
                            should_remove = False

                            if conversion_mode == 'all':
                                # Hapus SEMUA formula
                                should_remove = True
                            elif '!' in f_text or ('[' in f_text and ']' in f_text):
                                # Hanya hapus formula external/sheet lain
                                should_remove = True

                            if should_remove:
                                try:
                                    val = ws_val[cell.coordinate].value
                                    sheet_updates[cell.coordinate] = val
                                except Exception:
                                    pass
                
                if sheet_updates:
                    cells_to_update[sheet_name] = sheet_updates

            wb_formulas.close()
            wb_values.close()
            
            # 3. Bedah ZIP: Edit XML secara langsung untuk mempertahankan Shape/Gambar
            output_buffer = io.BytesIO()
            
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zin:
                with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zout:
                    
                    # Baca mapping sheet name -> filename
                    workbook_xml = zin.read('xl/workbook.xml').decode('utf-8')
                    rels_xml = zin.read('xl/_rels/workbook.xml.rels').decode('utf-8')

                    # Regex untuk parsing relasi dan nama sheet
                    rid_file_map = {}
                    # Handle kutip satu (') atau dua ("), dan spasi di sekitar sama dengan
                    for match in re.finditer(r'<(?:\w+:)?Relationship\s+[^>]*>', rels_xml):
                        rid_match = re.search(r'Id\s*=\s*(["\'])(.*?)\1', match.group(0))
                        target_match = re.search(r'Target\s*=\s*(["\'])(.*?)\1', match.group(0))
                        if rid_match and target_match:
                            rid = rid_match.group(2)
                            target = target_match.group(2)
                            if target.startswith('/'): target = target[1:]
                            elif target.startswith('worksheets/'): target = 'xl/' + target
                            rid_file_map[rid] = target

                    sheet_file_map = {}
                    for match in re.finditer(r'<(?:\w+:)?sheet\s+[^>]*>', workbook_xml):
                        name_match = re.search(r'name\s*=\s*(["\'])(.*?)\1', match.group(0))
                        # Cari r:id atau id (jika namespace default)
                        rid_match = re.search(r'(?:r:)?id\s*=\s*(["\'])(.*?)\1', match.group(0))
                        if name_match and rid_match:
                            # Decode nama sheet (misal: "Rugi &amp; Laba" -> "Rugi & Laba")
                            name = unescape(name_match.group(2))
                            rid = rid_match.group(2)
                            if rid in rid_file_map:
                                sheet_file_map[name] = rid_file_map[rid]

                    for item in zin.infolist():
                        # Hapus calcChain sepenuhnya agar Excel rebuild calculation chain (mencegah corrupt)
                        if 'calcChain' in item.filename:
                            continue
                        if item.filename == '[Content_Types].xml':
                            xml = zin.read(item.filename).decode('utf-8')
                            xml = re.sub(r'<Override\s+[^>]*PartName="/xl/calcChain\.xml"[^>]*?>', '', xml)
                            zout.writestr(item.filename, xml.encode('utf-8'))
                            continue
                        if item.filename == 'xl/_rels/workbook.xml.rels':
                            xml = zin.read(item.filename).decode('utf-8')
                            xml = re.sub(r'<Relationship\s+[^>]*Target="[^"]*calcChain\.xml"[^>]*?>', '', xml)
                            zout.writestr(item.filename, xml.encode('utf-8'))
                            continue

                        # Proses Sheet XML
                        target_sheet = next((name for name, fname in sheet_file_map.items() if item.filename == fname), None)
                        
                        if target_sheet and target_sheet in cells_to_update:
                            xml_str = zin.read(item.filename).decode('utf-8')
                            updates = cells_to_update[target_sheet]

                            # Class untuk melacak posisi baris dan kolom saat parsing XML
                            class XMLProcessor:
                                def __init__(self):
                                    self.current_row = 0
                                    self.current_col = 0

                                def process(self, match):
                                    # Group 1: Row Tag (<row ...>)
                                    if match.group(1):
                                        row_tag = match.group(1)
                                        self.current_row += 1
                                        # Cek jika ada atribut r="1"
                                        r_match = re.search(r'r\s*=\s*["\'](\d+)["\']', row_tag)
                                        if r_match:
                                            self.current_row = int(r_match.group(1))
                                        self.current_col = 0 # Reset kolom setiap ganti baris
                                        return row_tag

                                    # Group 2: Self-closing Cell (<c ... />) - Tidak ada konten, cuma update posisi
                                    if match.group(2):
                                        self._update_pos(match.group(2))
                                        return match.group(2)

                                    # Group 3: Cell dengan konten (<c ...>...</c>)
                                    full_cell = match.group(3)
                                    start_tag = re.match(r'<(?:\w+:)?c[^>]*>', full_cell).group(0)
                                    end_tag = full_cell[len(full_cell) - full_cell.rfind('<'):] if '</' in full_cell else '' # Simplifikasi end tag
                                    
                                    self._update_pos(start_tag)
                                    
                                    # Hitung koordinat saat ini (misal: A1)
                                    col_letter = get_column_letter(self.current_col)
                                    coord = f"{col_letter}{self.current_row}"
                                    
                                    if coord in updates:
                                        val = updates[coord]
                                        # Ambil style 's' jika ada
                                        style_match = re.search(r's\s*=\s*(["\'])(.*?)\1', start_tag)
                                        s_attr = f' s="{style_match.group(2)}"' if style_match else ''
                                        
                                        # Generate XML baru
                                        end_tag_clean = re.search(r'</(?:\w+:)?c\s*>', full_cell).group(0)
                                        if val is None: return f'<c r="{coord}"{s_attr}/>'
                                        if isinstance(val, bool): return f'<c r="{coord}"{s_attr} t="b"><v>{"1" if val else "0"}</v>{end_tag_clean}'
                                        if isinstance(val, (int, float)): return f'<c r="{coord}"{s_attr}><v>{val}</v>{end_tag_clean}'
                                        if hasattr(val, 'strftime'): return f'<c r="{coord}"{s_attr}><v>{to_excel(val)}</v>{end_tag_clean}'
                                        return f'<c r="{coord}"{s_attr} t="inlineStr"><is><t>{escape(str(val))}</t></is>{end_tag_clean}'
                                    
                                    return full_cell

                                def _update_pos(self, tag):
                                    self.current_col += 1
                                    # Jika ada atribut r="A1", update posisi pointer
                                    r_match = re.search(r'(?:r|r:r)\s*=\s*["\']([A-Za-z0-9]+)["\']', tag)
                                    if r_match:
                                        col_str, row_str = coordinate_from_string(r_match.group(1))
                                        self.current_row = row_str
                                        self.current_col = column_index_from_string(col_str)
                                
                            processor = XMLProcessor()
                            # Regex: (Row) OR (Cell Self-Closing) OR (Cell Content)
                            pattern = r'(<(?:\w+:)?row\b[^>]*>)|(<(?:\w+:)?c\b[^>]*/>)|(<(?:\w+:)?c\b[^>]*>.*?</(?:\w+:)?c\s*>)'
                            new_xml = re.sub(pattern, processor.process, xml_str, flags=re.DOTALL)
                            zout.writestr(item.filename, new_xml.encode('utf-8'))
                        else:
                            zout.writestr(item, zin.read(item.filename))
            
            output_buffer.seek(0)

            original_name = file.filename.rsplit('.', 1)[0]
            new_filename = f"{original_name} TR.xlsx"
            
            return send_file(
                output_buffer,
                as_attachment=True,
                download_name=new_filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except Exception as e:
            print(f"An error occurred: {e}")
            return str(e), 500

    return 'Invalid file or error during processing', 500

if __name__ == '__main__':
    # You can run this script directly using 'python app.py'
    # The application will be available at http://127.0.0.1:5000
    print("Starting Flask server...")
    print("Access the application at http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
