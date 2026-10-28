"""文書保管庫：申込書控え（要件定義書 5.4・5.5、F-08／F-14〜F-18、N-12／N-13）

  保存期間：自動車保険3年・火災保険5年・その他3年（代表決定 2026/10/23）
  起算日  ：文書保管庫への登録日（システムが自動で付け、変更できない）。満了日＝登録日からその年数が経った応当日
  複数種目：最も長い期間を適用。種目が未設定の文書は「要判断」で、人が設定するまで削除しない
  種目変更：担当者が申請 → 責任者（管理者）が承認。満了日は元の登録日から再計算し、変更ログを残す
  ホールド：紛争中・事故対応中は、申請 → 生成AI責任者（管理者）が承認した場合だけ削除を保留。6か月ごとに見直し
  日次ジョブ：午前2:00。事前通知（14日前・前日）→ 満了分を暗号化消去 → 残存チェック → 削除ログ（日時・文書ID・実行者IDのみ）
  原本は暗号化して保存し、LLM からは参照しない（申込書には告知欄などが含まれるため）
"""
import datetime as dt, json, secrets
from . import db, audit, crypto
from .config import VAULT, RETENTION_YEARS, UNSET_KIND, HOLD_REASONS, HOLD_REVIEW_MONTHS, PURPOSES

KINDS = list(RETENTION_YEARS)
ALLOWED = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".tif": "image/tiff", ".tiff": "image/tiff", ".heic": "image/heic"}


def today():
    return dt.date.today()


def add_years(d, n):
    """応当日。2/29 の n 年後に 2/29 がなければ 2/28（その月の末日）"""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(y, m, day)
        except ValueError:
            continue


def norm_kinds(kinds):
    ks = [k for k in (kinds or []) if k in KINDS]
    return sorted(set(ks), key=KINDS.index)


def expires_for(registered, kinds):
    ks = norm_kinds(kinds)
    if not ks:
        return None
    return add_years(registered, max(RETENTION_YEARS[k] for k in ks))


def _kinds_str(ks):
    return ",".join(ks) if ks else UNSET_KIND


def register(name, data, customer_id, title, kinds, staff, *, registered=None):
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED:
        return {"error": "PDF または画像（PNG・JPEG・TIFF・HEIC）を登録してください"}
    if not data:
        return {"error": "ファイルが空です"}
    if customer_id and not db.one("SELECT 1 FROM customer WHERE id=?", customer_id):
        return {"error": "顧客が見つかりません"}
    reg = registered or today()  # 登録日はシステムが付ける（画面からは指定できない）
    ks = norm_kinds(kinds)
    exp = expires_for(reg, ks)
    ref = crypto.encrypt_file(data, VAULT)
    cid = db.run("""INSERT INTO application_copy(customer_id,title,kinds,registered,expires,ref,mime,size,created_by)
                    VALUES(?,?,?,?,?,?,?,?,?)""", customer_id or None, (title or name)[:100], _kinds_str(ks), reg.isoformat(),
                 exp.isoformat() if exp else None, ref, ALLOWED[ext], len(data), staff["id"])
    audit.log("vault_register", staff=staff, channel="vault", refs=f"copy{cid}", result=_kinds_str(ks))
    return {"ok": True, "id": cid, "registered": reg.isoformat(), "expires": exp.isoformat() if exp else None,
            "kinds": _kinds_str(ks)}


def _days_left(exp, t):
    return (dt.date.fromisoformat(exp) - t).days if exp else None


def listing(staff, q=""):
    show_name = staff.get("purpose") in PURPOSES and "name" in PURPOSES[staff["purpose"]]
    t = today()
    out = []
    for r in db.rows("""SELECT a.*, c.name AS customer_name, s.name AS created_name,
                        (SELECT count(*) FROM copy_request q WHERE q.copy_id=a.id AND q.status='pending') AS pending
                        FROM application_copy a LEFT JOIN customer c ON c.id=a.customer_id LEFT JOIN staff s ON s.id=a.created_by
                        ORDER BY a.id DESC"""):
        r["customer"] = (r["customer_name"] or "（顧客未設定）") if show_name else (f"顧客ID {r['customer_id']}" if r["customer_id"] else "（顧客未設定）")
        r.pop("customer_name"); r.pop("ref")
        r["days_left"] = _days_left(r["expires"], t)
        r["state"] = "ホールド中" if r["legal_hold"] else ("要判断（種目未設定）" if r["kinds"] == UNSET_KIND else "保存中")
        if q and q not in (r["title"] or "") + r["customer"] + str(r["id"]):
            continue
        out.append(r)
    return out


def view(cid, staff):
    r = db.one("SELECT * FROM application_copy WHERE id=?", cid)
    if not r:
        return None, None
    audit.log("vault_view", staff=staff, channel="vault", refs=f"copy{cid}")
    return crypto.decrypt_file(r["ref"], VAULT), r["mime"]


# ---------- 申請と承認 ----------
def request(cid, typ, payload, staff):
    r = db.one("SELECT * FROM application_copy WHERE id=?", cid)
    if not r:
        return {"error": "文書が見つかりません"}
    if db.one("SELECT 1 FROM copy_request WHERE copy_id=? AND type=? AND status='pending'", cid, typ):
        return {"error": "同じ申請がすでに承認待ちです"}
    if typ == "kind":
        ks = norm_kinds(payload.get("kinds"))
        if not ks:
            return {"error": "変更後の種目を選んでください"}
        if _kinds_str(ks) == r["kinds"]:
            return {"error": "今の種目と同じです"}
        payload = {"kinds": ks}
    elif typ == "hold":
        if r["legal_hold"]:
            return {"error": "すでにホールド中です"}
        if payload.get("reason") not in HOLD_REASONS:
            return {"error": "保留の理由（分類コード）を選んでください"}
        payload = {"reason": payload["reason"]}
    elif typ == "unhold":
        if not r["legal_hold"]:
            return {"error": "ホールド中ではありません"}
        payload = {}
    else:
        return {"error": "申請の種類が不正です"}
    rid = db.run("INSERT INTO copy_request(copy_id,type,payload,requested_by) VALUES(?,?,?,?)", cid, typ, json.dumps(payload, ensure_ascii=False), staff["id"])
    audit.log("vault_request", staff=staff, channel="vault", refs=f"copy{cid}", result=typ)
    audit.notify("文書保管庫：承認待ちの申請があります", f"文書ID {cid}：{ {'kind': '種目変更', 'hold': 'リーガルホールド', 'unhold': 'ホールド解除'}[typ] }",
                 roles="admin", key=f"vreq:{rid}")
    return {"ok": True, "id": rid}


def requests(status="pending"):
    rs = db.rows("""SELECT q.*, s.name AS requested_name, d.name AS decided_name, a.kinds AS current_kinds, a.registered
                    FROM copy_request q LEFT JOIN staff s ON s.id=q.requested_by LEFT JOIN staff d ON d.id=q.decided_by
                    LEFT JOIN application_copy a ON a.id=q.copy_id WHERE (?='' OR q.status=?) ORDER BY q.id DESC LIMIT 200""", status, status)
    for r in rs:
        r["payload"] = json.loads(r["payload"] or "{}")
    return rs


def decide(rid, approve, staff, *, now=None):
    q = db.one("SELECT * FROM copy_request WHERE id=?", rid)
    if not q or q["status"] != "pending":
        return {"error": "承認待ちの申請ではありません"}
    if q["requested_by"] == staff["id"]:
        return {"error": "自分の申請は承認できません（申請者と承認者は別の人にする）"}
    r = db.one("SELECT * FROM application_copy WHERE id=?", q["copy_id"])
    db.run("UPDATE copy_request SET status=?, decided_by=?, decided_at=datetime('now','localtime') WHERE id=?",
           "approved" if approve else "rejected", staff["id"], rid)
    audit.log("vault_decide", staff=staff, channel="vault", refs=f"copy{q['copy_id']}", result=("承認:" if approve else "否認:") + q["type"])
    if not approve or not r:
        return {"ok": True}
    p = json.loads(q["payload"] or "{}")
    t = now or today()
    if q["type"] == "kind":
        exp = expires_for(dt.date.fromisoformat(r["registered"]), p["kinds"])  # 元の登録日から再計算
        db.run("UPDATE application_copy SET kinds=?, expires=?, notice14=0, notice1=0 WHERE id=?", _kinds_str(p["kinds"]),
               exp.isoformat() if exp else None, r["id"])
        # 変更後の満了日が過去・目前なら、事前通知（14日前・前日）を出せるよう削除を最低14日繰り延べる（F-15）
        floor = t + dt.timedelta(days=14)
        db.set_setting(f"defer:{r['id']}", floor.isoformat() if exp and exp < floor else "")
        db.run("INSERT INTO kind_change_log(copy_id,before,after,requested_by,approved_by) VALUES(?,?,?,?,?)",
               r["id"], r["kinds"], _kinds_str(p["kinds"]), q["requested_by"], staff["id"])
    elif q["type"] == "hold":
        db.run("UPDATE application_copy SET legal_hold=1, hold_reason=?, hold_review=? WHERE id=?",
               p["reason"], add_months(t, HOLD_REVIEW_MONTHS).isoformat(), r["id"])
    elif q["type"] == "unhold":
        db.run("UPDATE application_copy SET legal_hold=0, hold_reason=NULL, hold_review=NULL WHERE id=?", r["id"])
    return {"ok": True}


# ---------- 日次ジョブ ----------
def run_daily(*, now=None, executor="job"):
    """事前通知 → 満了分の暗号化消去 → 残存チェック → 削除ログ。何度実行しても同じ結果になる"""
    t = now or today()
    job = f"J{t:%Y%m%d}-{secrets.token_hex(3)}"
    out = {"job_id": job, "date": t.isoformat(), "notice": 0, "deleted": 0, "failed": 0, "held": 0, "unset": 0}
    copies = [dict(r) for r in db.rows("SELECT * FROM application_copy")]
    for r in copies:  # 削除してよい日＝満了日と繰り延べ日の遅いほう
        r["due"] = max(filter(None, [r["expires"], db.setting(f"defer:{r['id']}")]), default=None)
    resp = db.one("SELECT id FROM staff WHERE role='admin' AND active=1 ORDER BY id")
    if executor == "job" and resp:  # 自動ジョブは運用責任者（最初の有効な管理者）のIDも残す（F-17）
        executor = f"job（運用責任者ID {resp['id']}）"
    # 1) 事前通知（満了14日前と前日。文書ID・種目・登録日・満了日だけを載せる）
    for days, col in ((14, "notice14"), (1, "notice1")):
        due = [r for r in copies if r["due"] and not r["legal_hold"] and not r[col] and 0 < _days_left(r["due"], t) <= days]
        if due:
            body = "\n".join(f"文書ID {r['id']}／{r['kinds']}／登録 {r['registered']}／満了 {r['expires']}" for r in due)
            audit.notify(f"申込書控えの削除予定（{'前日' if days == 1 else '14日前'}）：{len(due)}件", body, roles="all",
                         key=f"notice{days}:{t}:{job}")
            for r in due:
                db.run(f"UPDATE application_copy SET {col}=1 WHERE id=?", r["id"])
            out["notice"] += len(due)
    # 2) 満了分の削除（ホールド中・種目未設定は削除しない）
    for r in copies:
        if r["legal_hold"]:
            out["held"] += 1
            if r["hold_review"] and r["hold_review"] <= t.isoformat():
                audit.notify("リーガルホールドの見直し期限です", f"文書ID {r['id']}（理由 {r['hold_reason']}：{HOLD_REASONS.get(r['hold_reason'], '')}）",
                             level="warn", roles="admin", key=f"review:{r['id']}:{r['hold_review']}")
            continue
        if not r["expires"]:
            out["unset"] += 1
            continue
        if r["due"] <= t.isoformat() and db.one("SELECT 1 FROM copy_request WHERE copy_id=? AND type='hold' AND status='pending'", r["id"]):
            # ホールドの申請が承認待ちのまま満了した：申請ごと消さず、判断が出るまで保留する
            out["held"] += 1
            audit.notify("リーガルホールドの申請が承認待ちのため、削除を保留しています", f"文書ID {r['id']}（満了 {r['expires']}）。申請を承認または否認してください",
                         level="warn", roles="admin", key=f"pendinghold:{r['id']}:{t}")
            continue
        if r["due"] <= t.isoformat():
            ok = crypto.erase(r["ref"], VAULT)
            db.run("DELETE FROM copy_request WHERE copy_id=? AND status='pending'", r["id"])
            db.run("DELETE FROM application_copy WHERE id=?", r["id"])
            db.set_setting(f"defer:{r['id']}", "")
            # 文書IDにひも付く派生データ（見積依頼書の成約ひも付け等）も同じ満了日で消す
            db.run("DELETE FROM quote WHERE contract_copy_id=?", r["id"])
            ok = ok and not db.one("SELECT 1 FROM application_copy WHERE id=?", r["id"])
            db.run("INSERT INTO deletion_log(copy_id,expires,executor,job_id,check_ok) VALUES(?,?,?,?,?)",
                   r["id"], r["expires"], executor, job, 1 if ok else 0)
            out["deleted" if ok else "failed"] += 1
    if out["failed"]:
        audit.notify("削除の残存チェックで失敗がありました", f"ジョブ {job}：{out['failed']}件。手動で確認してください", level="error",
                     roles="admin", key=f"fail:{job}")
    if out["unset"]:
        audit.notify(f"種目が未設定の申込書控え（要判断）：{out['unset']}件", "種目を設定するまで削除しません。文書保管庫で種目変更を申請してください",
                     level="warn", roles="admin", key=f"unset:{t}")
    db.set_setting("vault_last_run", t.isoformat())
    audit.log("vault_job", channel="job", result=f"削除{out['deleted']} 失敗{out['failed']} 通知{out['notice']}", refs=job)
    return out


def reconcile(month):
    """月次点検：「予定件数＝削除件数＋ホールド件数」を照合する（N-13）。month は 'YYYY-MM'"""
    t = today().isoformat()
    deleted = db.one("SELECT count(*) AS n FROM deletion_log WHERE substr(expires,1,7)=? AND check_ok=1", month)["n"]
    failed = db.one("SELECT count(*) AS n FROM deletion_log WHERE substr(expires,1,7)=? AND check_ok=0", month)["n"]
    held = db.one("SELECT count(*) AS n FROM application_copy WHERE substr(expires,1,7)=? AND legal_hold=1", month)["n"]
    leaked = db.one("SELECT count(*) AS n FROM application_copy WHERE substr(expires,1,7)=? AND legal_hold=0 AND expires<=?", month, t)["n"]
    waiting = db.one("SELECT count(*) AS n FROM application_copy WHERE substr(expires,1,7)=? AND legal_hold=0 AND expires>?", month, t)["n"]
    scheduled = deleted + failed + held + leaked + waiting
    return {"month": month, "scheduled": scheduled, "deleted": deleted, "held": held, "failed": failed, "leaked": leaked,
            "waiting": waiting, "ok": failed == 0 and leaked == 0 and scheduled == deleted + held + waiting}


def logs():
    return {"deletion": db.rows("SELECT * FROM deletion_log ORDER BY id DESC LIMIT 200"),
            "kind_change": db.rows("""SELECT k.*, a.name AS requested_name, b.name AS approved_name FROM kind_change_log k
                                      LEFT JOIN staff a ON a.id=k.requested_by LEFT JOIN staff b ON b.id=k.approved_by ORDER BY k.id DESC LIMIT 200"""),
            "last_run": db.setting("vault_last_run")}
