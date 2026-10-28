"""ログイン（個人ID＋パスワード＋TOTP の多要素認証）・セッション・権限（F-01／CL No.22・23）

  初回: 社員が1人もいなければ「初期設定」で管理者を1人作る
  ログイン: ID＋パスワード → 確認コード（認証アプリの6桁）。認証アプリ未登録なら、その場で登録してからログイン
  セッション: Cookie（HttpOnly・SameSite=Strict）。DB にはトークンのハッシュだけを置く。9時間で失効
"""
import base64, hashlib, hmac, secrets, struct, threading, time, urllib.parse
from . import db, audit
from .config import ROLES, PERMS, PURPOSES, PURPOSES_OTHER, SESSION_HOURS

ISSUER = "hokenchat"
_tickets, _fails = {}, {}
_lock = threading.Lock()


class AuthError(Exception):
    def __init__(self, msg, code=401):
        super().__init__(msg)
        self.code = code


# ---------- パスワード ----------
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300_000)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, salt, h = stored.split("$")
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 300_000).hex(), h)
    except Exception:
        return False


def pw_policy(pw):
    if len(pw) < 10:
        return "パスワードは10文字以上にしてください"
    if pw.isdigit() or pw.isalpha():
        return "パスワードは英字と数字を混ぜてください"
    return None


# ---------- TOTP（RFC 6238。Google Authenticator・Microsoft Authenticator などで使える） ----------
def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret, t=None, step=30, digits=6):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    c = int((t or time.time()) // step)
    h = hmac.new(key, struct.pack(">Q", c), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def check_totp(secret, code):
    code = "".join(ch for ch in str(code) if ch.isdigit())
    now = time.time()
    return any(hmac.compare_digest(totp(secret, now + d * 30), code) for d in (-1, 0, 1))


def otpauth_uri(login, secret):
    return f"otpauth://totp/{urllib.parse.quote(ISSUER + ':' + login)}?secret={secret}&issuer={ISSUER}&digits=6&period=30"


# ---------- 社員 ----------
def staff_count():
    return db.one("SELECT count(*) AS n FROM staff")["n"]


def create_staff(login, name, role, password, *, must_change=True):
    login, name = login.strip(), name.strip()
    if role not in ROLES:
        raise AuthError("ロールが不正です", 400)
    if not login or not name:
        raise AuthError("IDと氏名を入れてください", 400)
    if e := pw_policy(password):
        raise AuthError(e, 400)
    if db.one("SELECT 1 FROM staff WHERE login=?", login):
        raise AuthError("そのIDはすでに使われています", 400)
    return db.run("INSERT INTO staff(login,name,role,pw_hash,must_change) VALUES(?,?,?,?,?)",
                  login, name, role, hash_pw(password), 1 if must_change else 0)


def public(s):
    return {k: s[k] for k in ("id", "login", "name", "role", "mfa_enabled", "must_change", "active", "created_at", "last_login")} \
        | {"role_name": ROLES.get(s["role"], s["role"])}


def list_staff():
    return [public(s) for s in db.rows("SELECT * FROM staff ORDER BY id")]


def update_staff(sid, *, role=None, active=None, reset_mfa=False, reset_pw=None):
    s = db.one("SELECT * FROM staff WHERE id=?", sid)
    if not s:
        raise AuthError("社員が見つかりません", 404)
    if role is not None:
        if role not in ROLES:
            raise AuthError("ロールが不正です", 400)
        db.run("UPDATE staff SET role=? WHERE id=?", role, sid)
    if active is not None:
        db.run("UPDATE staff SET active=? WHERE id=?", 1 if active else 0, sid)
        if not active:
            db.run("DELETE FROM sessions WHERE staff_id=?", sid)
    if reset_mfa:
        db.run("UPDATE staff SET mfa_enabled=0, mfa_secret=NULL WHERE id=?", sid)
        db.run("DELETE FROM sessions WHERE staff_id=?", sid)
    if reset_pw:
        if e := pw_policy(reset_pw):
            raise AuthError(e, 400)
        db.run("UPDATE staff SET pw_hash=?, must_change=1 WHERE id=?", hash_pw(reset_pw), sid)
        db.run("DELETE FROM sessions WHERE staff_id=?", sid)
    # 管理者がいなくなる変更は止める
    if not db.one("SELECT 1 FROM staff WHERE role='admin' AND active=1"):
        db.run("UPDATE staff SET role=?, active=? WHERE id=?", s["role"], s["active"], sid)
        raise AuthError("有効な管理者が1人もいなくなるため、変更できません", 400)


def change_password(staff, old, new):
    s = db.one("SELECT * FROM staff WHERE id=?", staff["id"])
    if not check_pw(old, s["pw_hash"]):
        raise AuthError("今のパスワードが違います", 400)
    if e := pw_policy(new):
        raise AuthError(e, 400)
    if old == new:
        raise AuthError("新しいパスワードは今と違うものにしてください", 400)
    db.run("UPDATE staff SET pw_hash=?, must_change=0 WHERE id=?", hash_pw(new), s["id"])


# ---------- ログイン ----------
def _locked(login):
    f = _fails.get(login)
    return f and f[0] >= 5 and time.time() - f[1] < 900  # 5回失敗で15分ロック


def login_password(login, password):
    with _lock:
        if _locked(login):
            raise AuthError("失敗が続いたため15分間ロックしています")
    s = db.one("SELECT * FROM staff WHERE login=? AND active=1", login.strip())
    if not s or not check_pw(password, s["pw_hash"]):
        with _lock:
            n = _fails.get(login, (0, 0))[0] + 1
            _fails[login] = (n, time.time())
        audit.log("login_fail", channel="auth", result="password")
        raise AuthError("IDまたはパスワードが違います")
    ticket = secrets.token_urlsafe(24)
    enroll = not s["mfa_enabled"]
    secret = new_secret() if enroll else s["mfa_secret"]
    with _lock:
        _tickets[ticket] = {"sid": s["id"], "secret": secret, "enroll": enroll, "exp": time.time() + 300}
    out = {"ticket": ticket, "enroll": enroll, "name": s["name"]}
    if enroll:
        out |= {"secret": secret, "uri": otpauth_uri(s["login"], secret)}
    return out


def login_code(ticket, code):
    with _lock:
        t = _tickets.get(ticket)
    if not t or t["exp"] < time.time():
        raise AuthError("時間切れです。もう一度ログインしてください")
    if not check_totp(t["secret"], code):
        audit.log("login_fail", channel="auth", result="mfa", staff={"id": t["sid"]})
        raise AuthError("確認コードが違います（端末の時刻がずれていないかも確認してください）")
    with _lock:
        _tickets.pop(ticket, None)
        s = db.one("SELECT login FROM staff WHERE id=?", t["sid"])
        _fails.pop(s["login"] if s else "", None)
    if t["enroll"]:
        db.run("UPDATE staff SET mfa_secret=?, mfa_enabled=1 WHERE id=?", t["secret"], t["sid"])
    token = secrets.token_urlsafe(32)
    db.run("DELETE FROM sessions WHERE expires < datetime('now','localtime')")
    db.run(f"INSERT INTO sessions VALUES(?,?,NULL,datetime('now','localtime','+{SESSION_HOURS} hours'))", _h(token), t["sid"])
    db.run("UPDATE staff SET last_login=datetime('now','localtime') WHERE id=?", t["sid"])
    audit.log("login", channel="auth", staff={"id": t["sid"]})
    return token


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session(token):
    """有効なセッションなら社員（＋現在の利用目的）を返す"""
    if not token:
        return None
    r = db.one("""SELECT s.*, x.purpose FROM sessions x JOIN staff s ON s.id=x.staff_id
                  WHERE x.token_hash=? AND x.expires > datetime('now','localtime') AND s.active=1""", _h(token))
    if not r:
        return None
    return {"id": r["id"], "login": r["login"], "name": r["name"], "role": r["role"], "purpose": r["purpose"],
            "must_change": r["must_change"], "token_hash": _h(token)}


def logout(token):
    db.run("DELETE FROM sessions WHERE token_hash=?", _h(token or ""))


def set_purpose(staff, purpose):
    if purpose and purpose not in PURPOSES and purpose not in PURPOSES_OTHER:
        raise AuthError("利用目的が不正です", 400)
    db.run("UPDATE sessions SET purpose=? WHERE token_hash=?", purpose or None, staff["token_hash"])
    audit.log("purpose", channel="auth", staff=staff, purpose=purpose or "")


def can(staff, perm):
    return bool(staff) and staff["role"] in PERMS.get(perm, set())


def require(staff, perm):
    if not staff:
        raise AuthError("ログインしてください")
    if staff.get("must_change") and perm != "self":
        raise AuthError("最初にパスワードを変更してください", 403)
    if perm != "self" and not can(staff, perm):
        raise AuthError("この操作の権限がありません", 403)


def require_customer_purpose(staff):
    """顧客情報を扱う処理は、利用目的タグの選択を必須にする（F-02）"""
    p = staff.get("purpose")
    if p not in PURPOSES:
        raise AuthError("顧客情報を使う前に、画面上部で利用目的を選んでください", 428)
    return PURPOSES[p]
