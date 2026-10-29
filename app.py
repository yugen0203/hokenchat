"""hokenchat 本体（第0.2版）：127.0.0.1:8765 で待ち受ける社内AIアシスタント

  画面        index.html ＋ static/app.js・app.css（外部CDNは使わない）
  処理        hc/ パッケージ（構成は hc/__init__.py を参照）
  LLM         Ollama（127.0.0.1:11434）。外部の AI サービスは使わない
  データ      data/（DB・暗号化した保管庫・鍵）。GitHub には上げない（.gitignore）

起動: python app.py（ふつうはアイコン＝launcher.py から）
初期文書の取り込みだけ: python app.py --ingest-only
"""
import json, os, secrets, sys, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hc import config, db, auth, audit, llm, rag, line, vault, customers, mailing, quotes, tasks, jobs
from hc.auth import AuthError, require, require_customer_purpose
from hc.config import ROOT, KEYS

COOKIE = "hc_session"
ROUTES = {}


def route(method, path, perm=None, *, purpose=False, raw=False):
    """perm=None はログイン不要。purpose=True は顧客情報を使う処理（利用目的タグが必須）"""
    def deco(fn):
        ROUTES[(method, path)] = (perm, purpose, raw, fn)
        return fn
    return deco


class File:
    def __init__(self, data, ctype, name=None, inline=True):
        self.data, self.ctype, self.name, self.inline = data, ctype, name, inline


def internal_token():
    """LINE 受付サーバーと本体の間の合言葉（同じ PC の中だけで使う）。ブラウザからの偽の受信を防ぐ"""
    KEYS.mkdir(parents=True, exist_ok=True)
    f = KEYS / "internal.token"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        try:
            os.chmod(f, 0o600)  # 本人だけが読める（Windows では無視される）
        except OSError:
            pass
    return f.read_text(encoding="utf-8").strip()


def meta():
    return {"roles": config.ROLES, "purposes": list(config.PURPOSES), "purposes_other": config.PURPOSES_OTHER,
            "purpose_fields": config.PURPOSES, "kinds": vault.KINDS, "retention": config.RETENTION_YEARS,
            "hold_reasons": config.HOLD_REASONS, "doc_kinds": {k: v["items"] for k, v in mailing.DOC_KINDS.items()},
            "envelopes": [*mailing.ENVELOPES, "ラベル", "なし"], "quote_kinds": list(quotes.templates()),
            "task_statuses": tasks.STATUSES, "labels": customers.LABELS}


# ================= 認証 =================
@route("GET", "/api/health")
def _health(r):
    return {"llm": llm.alive(), "ai_enabled": llm.enabled(), "model": config.CHAT_MODEL}


@route("GET", "/api/me")
def _me(r):
    s = r.staff
    return {"setup_needed": auth.staff_count() == 0,
            "staff": s and {k: s[k] for k in ("id", "login", "name", "role", "purpose", "must_change")}
            | {"role_name": config.ROLES[s["role"]], "perms": [p for p in config.PERMS if auth.can(s, p)]},
            "meta": meta() if s else None}


@route("POST", "/api/setup")
def _setup(r):
    if auth.staff_count():
        raise AuthError("初期設定は済んでいます", 400)
    b = r.body
    auth.create_staff(b.get("login", ""), b.get("name", ""), "admin", b.get("password", ""), must_change=False)
    audit.log("setup", channel="auth", result="first admin")
    return {"ok": True}


@route("POST", "/api/login")
def _login(r):
    return auth.login_password(r.body.get("login", ""), r.body.get("password", ""))


@route("POST", "/api/login/code")
def _login_code(r):
    token = auth.login_code(r.body.get("ticket", ""), r.body.get("code", ""))
    r.set_cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={config.SESSION_HOURS * 3600}"
    return {"ok": True}


@route("POST", "/api/logout")
def _logout(r):
    if r.staff:
        audit.log("logout", channel="auth", staff=r.staff)
    auth.logout(r.token)
    r.set_cookie = f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    return {"ok": True}


@route("POST", "/api/password", "self")
def _password(r):
    auth.change_password(r.staff, r.body.get("old", ""), r.body.get("new", ""))
    audit.log("password_change", channel="auth", staff=r.staff)
    return {"ok": True}


@route("POST", "/api/purpose", "chat")
def _purpose(r):
    auth.set_purpose(r.staff, r.body.get("purpose") or "")
    return {"ok": True, "purpose": r.body.get("purpose") or None}


@route("GET", "/api/notifications", "chat")
def _notifs(r):
    return audit.notifications(r.staff)


@route("POST", "/api/notifications/read", "chat")
def _notif_read(r):
    audit.mark_read(r.staff, r.body.get("ids") or [])
    return {"ok": True}


@route("GET", "/api/staff/names", "task")
def _staff_names(r):
    return db.rows("SELECT id, name, role FROM staff WHERE active=1 ORDER BY id")


# ================= 社内チャット =================
@route("POST", "/api/search", "chat")
def _search(r):
    t = str(r.body.get("text") or "").strip()[:2000]
    return rag.quick(t, r.staff) if t else {"sources": []}


@route("POST", "/api/internal", "chat")
def _internal(r):
    t = str(r.body.get("text") or "").strip()[:2000]
    return rag.internal(t, r.staff) if t else {"answer": ""}


# ================= 登録文書 =================
@route("GET", "/api/docs", "docs.view")
def _docs(r):
    return rag.versions()


@route("GET", "/api/docs/preview", "docs.view")
def _docs_preview(r):
    return rag.preview(int(r.q("id")))


@route("POST", "/api/docs/upload", "docs.upload", raw=True)
def _docs_upload(r):
    return rag.register(r.h("X-Filename"), r.raw, r.h("X-Title"), r.h("X-Version"), r.h("X-Insurer"), r.staff)


@route("POST", "/api/docs/approve", "docs.approve")
def _docs_approve(r):
    return rag.approve(int(r.body["id"]), r.staff)


@route("POST", "/api/docs/reject", "docs.approve")
def _docs_reject(r):
    return rag.reject(int(r.body["id"]), r.staff)


@route("POST", "/api/docs/edit", "docs.approve")
def _docs_edit(r):
    return rag.edit_pending(int(r.body["id"]), r.body, r.staff)


@route("POST", "/api/docs/remove", "docs.delete")
def _docs_remove(r):
    return rag.remove_document(int(r.body["id"]), r.staff)


@route("POST", "/api/docs/scan", "docs.approve")
def _docs_scan(r):
    return {"ok": True, "added": rag.scan_inbox(), "folder": str(config.INBOX)}


# ================= 公式LINE =================
@route("POST", "/api/line", "chat")
def _line_test(r):
    return line.respond(str(r.body.get("text") or "")[:2000], r.staff)


@route("POST", "/api/line/incoming", "internal")
def _line_in(r):
    return line.incoming(r.body)


@route("GET", "/api/line/users", "line")
def _line_users(r):
    return line.users()


@route("GET", "/api/line/msgs", "line")
def _line_msgs(r):
    return line.msgs(r.q("user"))


@route("POST", "/api/line/send", "line")
def _line_send(r):
    return line.send(r.body, r.staff)


@route("POST", "/api/line/mode", "line")
def _line_mode(r):
    return line.set_mode(r.body, r.staff)


@route("POST", "/api/line/draft", "line")
def _line_draft(r):
    return line.draft(r.body["user_id"], r.staff)


@route("POST", "/api/line/link", "line", purpose=True)
def _line_link(r):
    if r.staff.get("purpose") != "お客さま対応":
        raise AuthError("LINEのお客さまとひも付けるには、利用目的「お客さま対応」を選んでください", 428)
    return line.link(r.body, r.staff)


@route("GET", "/api/line/attach", "line.attach")
def _line_attach(r):
    data, mime = line.attachment(int(r.q("id")), r.staff)
    return File(data, mime) if data else {"error": "見つかりません"}


# ================= 顧客DB =================
@route("GET", "/api/customers", "customer", purpose=True)
def _customers(r):
    return customers.search(r.q("q"), r.allowed, r.staff)


@route("GET", "/api/customer", "customer", purpose=True)
def _customer(r):
    return customers.get(int(r.q("id")), r.allowed, r.staff) or {"error": "見つかりません"}


@route("POST", "/api/customers/save", "customer", purpose=True)
def _customer_save(r):
    if r.staff["purpose"] != "契約管理":
        raise AuthError("顧客情報の登録・修正は、利用目的「契約管理」で行ってください", 428)
    return customers.save(r.body, r.staff)


@route("GET", "/api/customers/csv-template", "customer")
def _customer_csv_template(r):
    return File(customers.csv_template(), "text/csv; charset=utf-8", "hokenchat_customers_template.csv", inline=False)


@route("POST", "/api/customers/import", "customer", purpose=True, raw=True)
def _customer_import(r):
    if r.staff["purpose"] != "契約管理":
        raise AuthError("顧客情報の一括登録・更新は、利用目的「契約管理」で行ってください", 428)
    return customers.import_csv(r.raw, r.staff, commit=r.h("X-Commit") == "1")


@route("POST", "/api/customers/delete", "staff.manage", purpose=True)
def _customer_delete(r):
    return customers.delete(int(r.body["id"]), r.staff)


@route("POST", "/api/customers/extract", "customer", purpose=True)
def _customer_extract(r):
    return customers.extract(str(r.body.get("text") or ""), r.staff)


# ================= 文書保管庫 =================
@route("GET", "/api/vault", "vault.list")
def _vault(r):
    return vault.listing(r.staff, r.q("q"))


@route("POST", "/api/vault/upload", "vault.register", raw=True)
def _vault_upload(r):
    kinds = [k for k in r.h("X-Kinds").split(",") if k]
    cid = int(r.h("X-Customer")) if r.h("X-Customer").isdigit() else None
    return vault.register(r.h("X-Filename"), r.raw, cid, r.h("X-Title"), kinds, r.staff)


@route("GET", "/api/vault/file", "vault.view", purpose=True)
def _vault_file(r):
    data, mime = vault.view(int(r.q("id")), r.staff)
    return File(data, mime) if data else {"error": "見つかりません（削除済みの可能性があります）"}


@route("POST", "/api/vault/request", "vault.request")
def _vault_request(r):
    return vault.request(int(r.body["copy_id"]), r.body.get("type"), r.body, r.staff)


@route("GET", "/api/vault/requests", "vault.list")
def _vault_requests(r):
    return vault.requests(r.q("status", "pending"))


@route("POST", "/api/vault/decide", "vault.approve")
def _vault_decide(r):
    return vault.decide(int(r.body["id"]), bool(r.body.get("approve")), r.staff)


@route("POST", "/api/vault/run", "vault.run")
def _vault_run(r):
    return vault.run_daily(executor=f"staff:{r.staff['id']}")


@route("GET", "/api/vault/reconcile", "vault.approve")
def _vault_reconcile(r):
    return vault.reconcile(r.q("month") or time.strftime("%Y-%m"))


@route("GET", "/api/vault/logs", "vault.approve")
def _vault_logs(r):
    return vault.logs()


# ================= 郵送 =================
@route("GET", "/api/mailing", "mailing", purpose=True)
def _mailing(r):
    rs = mailing.listing(r.staff)
    if "name" not in r.allowed:
        for m in rs:
            m["customer_name"] = f"顧客ID {m['customer_id']}"
    return rs


@route("POST", "/api/mailing/create", "mailing", purpose=True)
def _mailing_create(r):
    if "address" not in r.allowed:
        raise AuthError("送付物の作成は、利用目的「送付状作成」または「更改案内」で行ってください", 428)
    return mailing.create(r.body, r.staff)


@route("POST", "/api/mailing/check", "mailing", purpose=True)
def _mailing_check(r):
    return mailing.check(int(r.body["id"]), r.staff)


@route("POST", "/api/mailing/sent", "mailing", purpose=True)
def _mailing_sent(r):
    return mailing.mark_sent(int(r.body["id"]), r.staff)


@route("GET", "/api/mailing/pdf", "mailing", purpose=True)
def _mailing_pdf(r):
    if "address" not in r.allowed:
        raise AuthError("印刷は、利用目的「送付状作成」または「更改案内」で行ってください", 428)
    ids = [i for i in r.q("ids").split(",") if i.isdigit()]
    what = r.q("what") if r.q("what") in ("letter", "envelope", "labels") else "letter"
    data = mailing.pdf(ids, what, r.staff, ai_note=r.q("ai") == "1")
    return File(data, "application/pdf", f"{what}.pdf") if data else {"error": "作成できるものがありません"}


@route("GET", "/api/settings/sender", "mailing")
def _sender(r):
    return mailing.sender()


@route("POST", "/api/settings/sender", "staff.manage")
def _sender_set(r):
    mailing.set_sender(r.body)
    audit.log("settings_sender", staff=r.staff, channel="admin")
    return mailing.sender()


# ================= 見積依頼書 =================
@route("GET", "/api/quotes", "quote", purpose=True)
def _quotes(r):
    return quotes.listing()


@route("GET", "/api/quote", "quote", purpose=True)
def _quote(r):
    return quotes.get(int(r.q("id")))


@route("POST", "/api/quotes/draft", "quote", purpose=True)
def _quote_draft(r):
    return quotes.draft(str(r.body.get("text") or ""), r.body.get("kind"), r.staff)


@route("POST", "/api/quotes/update", "quote", purpose=True)
def _quote_update(r):
    return quotes.update(int(r.body["id"]), r.body.get("fields") or {}, r.staff)


@route("POST", "/api/quotes/approve", "quote", purpose=True)
def _quote_approve(r):
    return quotes.approve(int(r.body["id"]), r.staff)


@route("POST", "/api/quotes/reconcile", "quote", purpose=True)
def _quote_reconcile(r):
    return quotes.reconcile(int(r.body["id"]), str(r.body.get("text") or ""), r.staff)


@route("POST", "/api/quotes/close", "quote", purpose=True)
def _quote_close(r):
    cid = r.body.get("copy_id")
    return quotes.close(int(r.body["id"]), bool(r.body.get("won")), int(cid) if str(cid or "").isdigit() else None, r.staff)


@route("GET", "/api/quotes/sheet", "quote", purpose=True)
def _quote_sheet(r):
    data = quotes.jnet_sheet(int(r.q("id")), r.staff)
    return File(data, "application/pdf", "jnet_input.pdf") if data else {"error": "承認済の見積依頼書だけ入力票を作れます"}


# ================= タスク =================
@route("GET", "/api/tasks", "task")
def _tasks(r):
    return {"tasks": tasks.listing(), "summary": {k: (len(v) if isinstance(v, list) else v) for k, v in tasks.summary().items()}}


@route("POST", "/api/tasks/create", "task")
def _task_create(r):
    return tasks.create(r.body, r.staff)


@route("POST", "/api/tasks/update", "task")
def _task_update(r):
    return tasks.update(int(r.body["id"]), r.body, r.staff)


@route("POST", "/api/tasks/propose", "task")
def _task_propose(r):
    return tasks.propose(str(r.body.get("text") or ""), r.staff)


# ================= 管理 =================
@route("GET", "/api/audit", "audit.view")
def _audit(r):
    return audit.recent(int(r.q("limit", "200")), r.q("action") or None)


@route("GET", "/api/audit/verify", "audit.view")
def _audit_verify(r):
    return audit.verify()


@route("GET", "/api/staff", "staff.manage")
def _staff(r):
    return auth.list_staff()


@route("POST", "/api/staff/create", "staff.manage")
def _staff_create(r):
    b = r.body
    sid = auth.create_staff(b.get("login", ""), b.get("name", ""), b.get("role", ""), b.get("password", ""))
    audit.log("staff_create", staff=r.staff, channel="admin", refs=f"s{sid}", result=b.get("role"))
    return {"ok": True, "id": sid}


@route("POST", "/api/staff/update", "staff.manage")
def _staff_update(r):
    b = r.body
    auth.update_staff(int(b["id"]), role=b.get("role"), active=b.get("active"), reset_mfa=bool(b.get("reset_mfa")),
                      reset_pw=b.get("reset_pw"))
    audit.log("staff_update", staff=r.staff, channel="admin", refs=f"s{b['id']}",
              result=",".join(k for k in ("role", "active", "reset_mfa", "reset_pw") if b.get(k) not in (None, False, "")))
    return {"ok": True}


@route("POST", "/api/ai/switch", "ai.switch")
def _ai_switch(r):
    on = bool(r.body.get("enabled"))
    db.set_setting("ai_enabled", "1" if on else "0")
    audit.log("ai_switch", staff=r.staff, channel="admin", result="on" if on else "off")
    if not on:
        audit.notify("AIの利用を停止しました", f"{r.staff['name']} が停止スイッチを押しました。業務は手作業で続けてください", level="error", roles="all")
    return {"ok": True, "enabled": on}


@route("GET", "/api/monthly", "audit.view")
def _monthly(r):
    """月次点検（CL No.24・30・79）：禁止情報の入力件数、ログの改ざんチェック、削除の件数照合、権限の棚卸し"""
    m = r.q("month") or time.strftime("%Y-%m")
    by = db.rows("""SELECT action, result, count(*) AS n FROM audit_log WHERE substr(ts,1,7)=? GROUP BY action, result ORDER BY n DESC""", m)
    blocked = db.rows("SELECT channel, guard, count(*) AS n FROM audit_log WHERE substr(ts,1,7)=? AND result='blocked' GROUP BY channel, guard", m)
    return {"month": m, "actions": by, "blocked": blocked, "chain": audit.verify(), "vault": vault.reconcile(m),
            "staff": auth.list_staff(), "ai_enabled": llm.enabled()}


# ================= HTTP =================
class Req:
    def __init__(self, h, staff, token, body, raw, query):
        self.h_, self.staff, self.token, self.body, self.raw, self.query = h, staff, token, body, raw, query
        self.set_cookie, self.allowed = None, []

    def q(self, k, default=""):
        return self.query.get(k, [default])[0]

    def h(self, k):
        return urllib.parse.unquote(self.h_.headers.get(k, "")).strip()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, File):
            data, ctype = body.data, body.ctype
            extra = (extra or {}) | {"Content-Disposition": f"{'inline' if body.inline else 'attachment'}; filename=\"{body.name or 'file'}\""}
        else:
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
                         "frame-src 'self' blob:; object-src 'self' blob:; connect-src 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def _static(self, path):
        if path == "/favicon.ico":
            return self._send(200, (ROOT / "assets" / "icon.png").read_bytes(), "image/png")
        if path in ("/", "/index.html"):
            return self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        f = (ROOT / path.lstrip("/")).resolve()
        if path.startswith("/static/") and f.is_relative_to(ROOT / "static") and f.is_file():
            ctype = {".css": "text/css", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2", ".svg": "image/svg+xml",
                     ".png": "image/png"}.get(f.suffix, "application/octet-stream")
            return self._send(200, f.read_bytes(), ctype)
        return self._send(404, {"error": "not found"})

    def _handle(self, method):
        # DNS リバインディング対策：「127.0.0.1」に見せかけた外部サイトからの通信を拒否する（Host を自分の名前に限る）
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self._send(421, {"error": "misdirected request"})
        u = urllib.parse.urlsplit(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path) if method == "GET" else self._send(404, {"error": "not found"})
        ent = ROUTES.get((method, u.path))
        if not ent:
            return self._send(404, {"error": "not found"})
        perm, purpose, raw, fn = ent
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n > 50_000_000:
                return self._send(413, {"error": "ファイルが大きすぎます（50MBまで）"})
            data = self.rfile.read(n) if n else b""
            if perm == "internal":
                if not secrets.compare_digest(self.headers.get("X-Internal-Token", ""), internal_token()):
                    return self._send(403, {"error": "forbidden"})
                staff, token = None, None
            else:
                # 画面以外（他のサイト等）からの POST を拒否する（CSRF 対策。SameSite=Strict と二重）
                if method == "POST" and self.headers.get("X-HC") != "1":
                    return self._send(403, {"error": "forbidden"})
                token = self._token()
                staff = auth.session(token)
                if perm:
                    require(staff, perm)
            r = Req(self, staff, token, {} if raw or not data else json.loads(data), data if raw else b"", urllib.parse.parse_qs(u.query))
            if purpose:
                r.allowed = require_customer_purpose(staff)
            out = fn(r)
            code = 400 if isinstance(out, dict) and out.get("error") and not out.get("blocked") else 200
            self._send(code, out, extra={"Set-Cookie": r.set_cookie} if r.set_cookie else None)
        except AuthError as e:
            self._send(e.code, {"error": str(e), "auth": e.code in (401, 403, 428)})
        except llm.AIStopped as e:
            self._send(503, {"error": str(e) + "。AIを使わない機能（検索候補以外の一覧・登録・印刷など）は引き続き使えます", "ai_stopped": True})
        except Exception as e:
            traceback.print_exc()
            msg = "ローカルLLMに接続できません（Ollama が止まっている可能性があります）" if "Connection refused" in str(e) or "urlopen" in str(e) else str(e)
            self._send(500, {"error": msg})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, *a):  # アクセスログに本文やクエリを出さない
        pass


def seed_when_ready():
    """初期文書の取り込み。Ollama がまだ起動していなければ、起動するまで待って取り込む"""
    for _ in range(120):
        try:
            rag.seed()
            return
        except Exception as e:
            print("初期文書の取り込みを待機中:", str(e)[:120])
            time.sleep(10)


def main():
    db.connect().close()
    internal_token()
    if "--ingest-only" in sys.argv:  # setup.py から呼ぶ
        rag.seed()
        print("登録文書:", db.one("SELECT count(*) AS n FROM chunk")["n"], "件")
        return
    threading.Thread(target=seed_when_ready, daemon=True).start()
    threading.Thread(target=llm.warmup, daemon=True).start()
    jobs.start()
    print(f"http://{config.HOST}:{config.PORT}")
    ThreadingHTTPServer((config.HOST, config.PORT), H).serve_forever()


if __name__ == "__main__":
    main()
