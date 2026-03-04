import os
import urllib.parse
import hashlib
import logging
from datetime import timedelta
from flask import Flask, send_from_directory, jsonify, request, send_file, session, redirect, url_for, render_template
import io
from generators import (
    generate_rincian_biaya,
    generate_pengeluaran_riil,
    generate_spt,
    generate_sppd_hal_1,
    generate_permohonan_rutin,
    generate_nota_dinas,
    generate_laporan_perjalanan,
)
from gen_utils import search_employees
from spt_parser import scan_spt_file

# Serve the current folder as static so existing assets keep working
# Load .env if present (simple parser; no external deps)
def _load_env(path: str = '.env'):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#') or '=' not in s:
                    continue
                k, v = s.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except FileNotFoundError:
        pass

_load_env()

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('APP_SECRET', 'dev-secret-change-me')
APP_PASSWORD = os.environ.get('APP_PASSWORD', '')
SESSION_TTL_HOURS = int(os.environ.get('SESSION_TTL_HOURS', '12'))
app.permanent_session_lifetime = timedelta(hours=SESSION_TTL_HOURS)


def _password_fingerprint() -> str:
    secret = os.environ.get('APP_SECRET', '')
    raw = f"{APP_PASSWORD}|{secret}".encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


@app.before_request
def _gate_all_requests():
    # Only enforce when a password is configured
    if not APP_PASSWORD:
        return
    path = request.path or '/'
    # Allowlist: login routes, health check, and favicon
    if path.startswith('/login') or path.startswith('/healthz') or path == '/favicon.ico':
        return
    if session.get('auth') is True and session.get('auth_fp') == _password_fingerprint():
        return
    # For API calls, return JSON 401 instead of redirect loop
    if path.startswith('/api/'):
        return jsonify({'error': 'unauthorized', 'login': '/login'}), 401
    # Redirect to login preserving destination
    nxt = urllib.parse.quote(path)
    return redirect(f'/login?next={nxt}')


@app.get('/')
def root():
    return send_from_directory('.', 'index.html')


@app.get('/healthz')
def healthz():
    return {'status': 'ok'}


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        next_url = request.args.get('next') or request.form.get('next') or '/'
        if APP_PASSWORD and password == APP_PASSWORD:
            session.permanent = True
            session['auth'] = True
            session['auth_fp'] = _password_fingerprint()
            return redirect(next_url)
        # If no password configured, allow
        if not APP_PASSWORD:
            session.permanent = True
            session['auth'] = True
            session['auth_fp'] = _password_fingerprint()
            return redirect(next_url)
        # Wrong password
        return render_template('login.html', error='Password salah', next_url=next_url)
    # GET
    next_url = request.args.get('next', '/')
    # If session exists but fingerprint changed (password/secret rotated), force re-login
    if session.get('auth') is True:
        if session.get('auth_fp') == _password_fingerprint():
            return redirect(next_url or '/')
        # stale session, clear and show login
        session.clear()
    return render_template('login.html', error=None, next_url=next_url)


@app.get('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.get('/api/employees')
def api_employees():
    q = request.args.get('q', '')
    items = search_employees(q)
    return jsonify(items)


@app.post('/api/scan/spt')
def api_scan_spt():
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return jsonify({'error': 'Harap pilih file SPT terlebih dahulu.'}), 400
    data = uploaded.read()
    if not data:
        return jsonify({'error': 'File kosong atau tidak terbaca.'}), 400
    try:
        result = scan_spt_file(data, uploaded.filename)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        logging.exception('Gagal memindai SPT')
        return jsonify({'error': 'Gagal memindai SPT. Periksa file dan coba lagi.'}), 500
    return jsonify(result)


@app.post('/api/generate/<doc_type>')
def api_generate(doc_type: str):
    try:
        payload = request.get_json(force=True, silent=True) or {}
        if doc_type == 'rincian_biaya':
            data, filename, mime = generate_rincian_biaya(payload)
        elif doc_type == 'pengeluaran_riil':
            data, filename, mime = generate_pengeluaran_riil(payload)
        elif doc_type == 'spt':
            data, filename, mime = generate_spt(payload)
        elif doc_type == 'sppd_hal_1':
            data, filename, mime = generate_sppd_hal_1(payload)
        elif doc_type == 'permohonan_rutin':
            data, filename, mime = generate_permohonan_rutin(payload)
        elif doc_type == 'nota_dinas':
            data, filename, mime = generate_nota_dinas(payload)
        elif doc_type == 'laporan':
            data, filename, mime = generate_laporan_perjalanan(payload)
        else:
            return jsonify({'error': f'Unknown doc_type {doc_type}'}), 400
        return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name=filename)
    except Exception as e:
        logging.exception('Generate failed for %s', doc_type)
        return jsonify({'error': str(e), 'type': doc_type}), 500


if __name__ == '__main__':
    # Development server
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # disable caching during dev
    app.run(host='127.0.0.1', port=5000, debug=True)
