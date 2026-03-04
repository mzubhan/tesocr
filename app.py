import os
import urllib.parse
import hashlib
import logging
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from flask import Flask, send_from_directory, jsonify, request, send_file, session, redirect, url_for, render_template
import io
try:
    import psycopg2
except ImportError:
    psycopg2 = None
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_draft(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    draft_id = str(raw.get('id') or '').strip()
    if not draft_id:
        return None
    title = str(raw.get('title') or '').strip() or 'Draft Tanpa Judul'
    created_at = str(raw.get('createdAt') or raw.get('created_at') or '').strip() or _utc_now_iso()
    updated_at = str(raw.get('updatedAt') or raw.get('updated_at') or '').strip() or created_at
    workspace = raw.get('workspace')
    if not isinstance(workspace, dict):
        workspace = {}
    return {
        'id': draft_id,
        'title': title,
        'createdAt': created_at,
        'updatedAt': updated_at,
        'workspace': workspace,
    }


class DraftStore:
    """Simple draft persistence with SQLite default and PostgreSQL optional via DATABASE_URL."""

    def __init__(self):
        raw_db_url = (os.environ.get('DRAFTS_DATABASE_URL') or os.environ.get('DATABASE_URL') or '').strip()
        if raw_db_url.startswith('postgres://'):
            raw_db_url = 'postgresql://' + raw_db_url[len('postgres://'):]
        self.db_url = raw_db_url
        self.sqlite_path = os.environ.get('DRAFTS_SQLITE_PATH', 'drafts_local.db')
        self.backend = 'postgres' if self.db_url else 'sqlite'
        try:
            self._ensure_schema()
        except Exception:
            if self.backend != 'postgres':
                raise
            # Jangan mematikan app saat koneksi cloud DB gagal.
            logging.exception('Gagal inisialisasi draft store PostgreSQL, fallback ke SQLite lokal.')
            self.backend = 'sqlite'
            self.db_url = ''
            self._ensure_schema()

    def _connect(self):
        if self.backend == 'postgres':
            if psycopg2 is None:
                raise RuntimeError('psycopg2-binary belum terpasang, tidak bisa pakai DATABASE_URL.')
            return psycopg2.connect(self.db_url)
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        ddl = (
            "CREATE TABLE IF NOT EXISTS drafts ("
            "id TEXT PRIMARY KEY,"
            "title TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "workspace TEXT NOT NULL"
            ");"
        )
        with self._connect() as conn:
            with conn.cursor() if self.backend == 'postgres' else _nullcontext(conn.cursor()) as cur:
                cur.execute(ddl)
            conn.commit()

    def list_drafts(self) -> List[Dict[str, Any]]:
        sql = "SELECT id, title, created_at, updated_at, workspace FROM drafts ORDER BY updated_at DESC"
        with self._connect() as conn:
            with conn.cursor() if self.backend == 'postgres' else _nullcontext(conn.cursor()) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        drafts = []
        for row in rows:
            if self.backend == 'postgres':
                draft_id, title, created_at, updated_at, workspace_raw = row
            else:
                draft_id = row['id']
                title = row['title']
                created_at = row['created_at']
                updated_at = row['updated_at']
                workspace_raw = row['workspace']
            try:
                workspace = json.loads(workspace_raw) if workspace_raw else {}
                if not isinstance(workspace, dict):
                    workspace = {}
            except Exception:
                workspace = {}
            drafts.append({
                'id': str(draft_id),
                'title': str(title or '').strip() or 'Draft Tanpa Judul',
                'createdAt': str(created_at or ''),
                'updatedAt': str(updated_at or created_at or ''),
                'workspace': workspace,
            })
        return drafts

    def replace_all(self, drafts: List[Dict[str, Any]]) -> int:
        clean: List[Dict[str, Any]] = []
        for item in drafts or []:
            norm = _normalize_draft(item)
            if norm is not None:
                clean.append(norm)

        with self._connect() as conn:
            with conn.cursor() if self.backend == 'postgres' else _nullcontext(conn.cursor()) as cur:
                cur.execute("DELETE FROM drafts")
                if clean:
                    if self.backend == 'postgres':
                        cur.executemany(
                            "INSERT INTO drafts (id, title, created_at, updated_at, workspace) VALUES (%s, %s, %s, %s, %s)",
                            [
                                (
                                    d['id'],
                                    d['title'],
                                    d['createdAt'],
                                    d['updatedAt'],
                                    json.dumps(d['workspace'], ensure_ascii=False),
                                )
                                for d in clean
                            ],
                        )
                    else:
                        cur.executemany(
                            "INSERT INTO drafts (id, title, created_at, updated_at, workspace) VALUES (?, ?, ?, ?, ?)",
                            [
                                (
                                    d['id'],
                                    d['title'],
                                    d['createdAt'],
                                    d['updatedAt'],
                                    json.dumps(d['workspace'], ensure_ascii=False),
                                )
                                for d in clean
                            ],
                        )
            conn.commit()
        return len(clean)


class _nullcontext:
    """Tiny context manager helper to keep cursor handling consistent across backends."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        try:
            self.value.close()
        except Exception:
            pass


draft_store = DraftStore()


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


@app.get('/api/drafts')
def api_drafts_list():
    try:
        drafts = draft_store.list_drafts()
        return jsonify({'drafts': drafts, 'backend': draft_store.backend})
    except Exception as exc:
        logging.exception('Gagal membaca draft cloud')
        return jsonify({'error': f'Gagal membaca draft: {exc}'}), 500


@app.post('/api/drafts/sync')
def api_drafts_sync():
    payload = request.get_json(force=True, silent=True) or {}
    drafts = payload.get('drafts')
    if not isinstance(drafts, list):
        return jsonify({'error': 'Payload drafts harus berupa array.'}), 400
    try:
        count = draft_store.replace_all(drafts)
        return jsonify({'ok': True, 'count': count, 'backend': draft_store.backend})
    except Exception as exc:
        logging.exception('Gagal sinkron draft cloud')
        return jsonify({'error': f'Gagal sinkron draft: {exc}'}), 500


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
