"""監査ログ（F-12／N-05）と社内通知

監査ログは「誰が・いつ・何の目的で・何をしたか」だけを残し、質問やメッセージの本文は保存しない。
各行は1つ前の行のハッシュを含めてハッシュ化する（ハッシュチェーン）。途中の行を書き換える・消すと verify() で検出できる。
"""
import hashlib, json, threading, time
from . import db

_lock = threading.Lock()
FIELDS = ["ts", "staff_id", "channel", "action", "purpose", "result", "refs", "guard", "ms"]


def _digest(prev, rec):
    return hashlib.sha256((prev + json.dumps([rec[k] for k in FIELDS], ensure_ascii=False)).encode()).hexdigest()


def log(action, *, staff=None, channel="", purpose="", result="", refs="", guard="", t0=None):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "staff_id": (staff or {}).get("id"), "channel": channel, "action": action,
           "purpose": purpose or (staff or {}).get("purpose") or "", "result": str(result)[:200], "refs": str(refs)[:300],
           "guard": guard or "", "ms": int((time.time() - t0) * 1000) if t0 else 0}
    with _lock:
        con = db.connect()
        last = con.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = last[0] if last else "GENESIS"
        con.execute("INSERT INTO audit_log(ts,staff_id,channel,action,purpose,result,refs,guard,ms,prev,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (*[rec[k] for k in FIELDS], prev, _digest(prev, rec)))
        con.commit()
        con.close()


def verify():
    """チェーンを先頭から検証する。壊れていれば最初に壊れた行の id を返す"""
    prev, n = "GENESIS", 0
    for r in db.rows("SELECT * FROM audit_log ORDER BY id"):
        n += 1
        if r["prev"] != prev or _digest(prev, r) != r["hash"]:
            return {"ok": False, "broken_at": r["id"], "checked": n}
        prev = r["hash"]
    return {"ok": True, "checked": n}


def recent(limit=200, action=None):
    q = "SELECT a.*, s.name AS staff_name FROM audit_log a LEFT JOIN staff s ON s.id=a.staff_id"
    args = []
    if action:
        q += " WHERE a.action=?"
        args.append(action)
    return db.rows(q + " ORDER BY a.id DESC LIMIT ?", *args, int(limit))


# ---------- 社内通知（AI 画面の通知。外部メールは使わない：F-15） ----------
def notify(title, body="", *, level="info", roles="admin", key=None):
    """key を渡すと同じ通知を二重に出さない"""
    try:
        db.run("INSERT INTO notifications(level,title,body,roles,key) VALUES(?,?,?,?,?)", level, title, body, roles, key)
    except Exception:
        pass  # key 重複＝通知済み


def notifications(staff, limit=50):
    rs = db.rows("""SELECT n.*, (SELECT 1 FROM notif_read r WHERE r.nid=n.id AND r.staff_id=?) AS read
                    FROM notifications n ORDER BY n.id DESC LIMIT ?""", staff["id"], limit)
    return [r for r in rs if r["roles"] == "all" or staff["role"] in r["roles"].split(",")]


def mark_read(staff, ids):
    for i in ids:
        db.run("INSERT OR IGNORE INTO notif_read VALUES(?,?)", int(i), staff["id"])
