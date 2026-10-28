"""SQLite の接続とスキーマ。試作は SQLite、本番は PostgreSQL＋pgvector を想定（設計書 第5章）"""
import sqlite3, threading
from .config import DATA, DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

-- 社員・権限・セッション（F-01）
CREATE TABLE IF NOT EXISTS staff(id INTEGER PRIMARY KEY, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
  pw_hash TEXT NOT NULL, mfa_secret TEXT, mfa_enabled INTEGER DEFAULT 0, must_change INTEGER DEFAULT 1, active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime')), last_login TEXT);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, staff_id INTEGER, purpose TEXT, expires TEXT);

-- 監査ログ（F-12）。本文は持たない。prev/hash のチェーンで改ざんを検知する
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, ts TEXT, staff_id INTEGER, channel TEXT, action TEXT, purpose TEXT,
  result TEXT, refs TEXT, guard TEXT, ms INTEGER, prev TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now','localtime')), level TEXT,
  title TEXT, body TEXT, roles TEXT, key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS notif_read(nid INTEGER, staff_id INTEGER, PRIMARY KEY(nid, staff_id));

-- 文書（RAG の対象）。版ごとに承認し、承認済みの最新版だけを検索する（D-03／D-04）
CREATE TABLE IF NOT EXISTS document(id INTEGER PRIMARY KEY, title TEXT UNIQUE, insurer TEXT);
CREATE TABLE IF NOT EXISTS document_version(id INTEGER PRIMARY KEY, document_id INTEGER, title TEXT, insurer TEXT, version TEXT,
  revised TEXT, source TEXT, status TEXT, file_hash TEXT, orig TEXT, uploaded_by INTEGER, approved_by INTEGER, approved_at TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS chunk(id INTEGER PRIMARY KEY, version_id INTEGER, article TEXT, body TEXT, vec TEXT);

-- 公式LINE
CREATE TABLE IF NOT EXISTS line_users(user_id TEXT PRIMARY KEY, name TEXT, mode TEXT DEFAULT 'ai', customer_id INTEGER);
CREATE TABLE IF NOT EXISTS line_msgs(id INTEGER PRIMARY KEY, user_id TEXT, sender TEXT, text TEXT, note TEXT,
  ts TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS line_attach(id INTEGER PRIMARY KEY, msg_id INTEGER, user_id TEXT, kind TEXT, mime TEXT, size INTEGER,
  ref TEXT, ts TEXT DEFAULT (datetime('now','localtime')));

-- 顧客DB（F-09）。要配慮情報・マイナンバー・カード番号の項目は作らない
CREATE TABLE IF NOT EXISTS customer(id INTEGER PRIMARY KEY, name TEXT, kana TEXT, postal TEXT, address TEXT, phone TEXT, email TEXT,
  note TEXT, created_by INTEGER, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT);
CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY, customer_id INTEGER, insurer TEXT, kind TEXT, policy_no TEXT, start TEXT, expiry TEXT);

-- 文書保管庫：申込書控え（F-08／F-14〜F-18）
CREATE TABLE IF NOT EXISTS application_copy(id INTEGER PRIMARY KEY, customer_id INTEGER, title TEXT, kinds TEXT, registered TEXT,
  expires TEXT, legal_hold INTEGER DEFAULT 0, hold_reason TEXT, hold_review TEXT, ref TEXT, mime TEXT, size INTEGER,
  created_by INTEGER, notice14 INTEGER DEFAULT 0, notice1 INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS copy_request(id INTEGER PRIMARY KEY, copy_id INTEGER, type TEXT, payload TEXT, requested_by INTEGER,
  ts TEXT DEFAULT (datetime('now','localtime')), status TEXT DEFAULT 'pending', decided_by INTEGER, decided_at TEXT);
CREATE TABLE IF NOT EXISTS kind_change_log(id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now','localtime')), copy_id INTEGER,
  before TEXT, after TEXT, requested_by INTEGER, approved_by INTEGER);
-- 削除ログ：日時・文書ID・実行者IDのみ（個人データは記録しない。F-17）。expires は件数照合用
CREATE TABLE IF NOT EXISTS deletion_log(id INTEGER PRIMARY KEY, copy_id INTEGER, expires TEXT,
  ts TEXT DEFAULT (datetime('now','localtime')), executor TEXT, job_id TEXT, check_ok INTEGER);

-- 業務ツール
CREATE TABLE IF NOT EXISTS mailing(id INTEGER PRIMARY KEY, customer_id INTEGER, doc_kind TEXT, items TEXT, body TEXT,
  envelope TEXT, created_by INTEGER, created_at TEXT DEFAULT (datetime('now','localtime')), checked_by INTEGER, checked_at TEXT,
  status TEXT DEFAULT '確認待ち');
CREATE TABLE IF NOT EXISTS quote(id INTEGER PRIMARY KEY, kind TEXT, customer_id INTEGER, fields TEXT, status TEXT,
  created_by INTEGER, approved_by INTEGER, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT,
  reconcile TEXT, contract_copy_id INTEGER);
CREATE TABLE IF NOT EXISTS task(id INTEGER PRIMARY KEY, title TEXT, detail TEXT, assignee INTEGER, due TEXT,
  status TEXT DEFAULT '未着手', created_by INTEGER, created_at TEXT DEFAULT (datetime('now','localtime')), done_at TEXT);
"""

_lock = threading.Lock()
_ready = False


def connect():
    """呼び出しごとに新しい接続を返す（スレッドをまたがない）。最初の1回だけスキーマを整える"""
    global _ready
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    if not _ready:
        with _lock:
            if not _ready:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                _migrate(con)
                con.commit()
                _ready = True
    return con


def _cols(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _migrate(con):
    """試作 第0.1版の DB から上げる（line_users に customer_id がない等）"""
    if "customer_id" not in _cols(con, "line_users"):
        con.execute("ALTER TABLE line_users ADD COLUMN customer_id INTEGER")


def reset_for_tests():
    global _ready
    _ready = False


def rows(sql, *args):
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def one(sql, *args):
    con = connect()
    try:
        r = con.execute(sql, args).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def run(sql, *args):
    con = connect()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", key)
    return r["value"] if r else default


def set_setting(key, value):
    run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, str(value))
