"""見積依頼フォームと書類アップロードの使い切りリンク（要件定義書 第2.0版 R-07・R-10）

  LINE で「@ボット 見積」「@ボット 書類 No.12」→ 期限付き・1回限りのリンクを返す
  リンクは受付サーバーの /f/<token> から開く（外から届く2つめの入口）。ページは JavaScript を使わない普通のフォーム
  車検証・免許証は LINE に流さず、暗号化して保管し、タスクに結び付ける。AI は読まない
  token はハッシュだけを DB に持つ（DB が漏れてもリンクは作れない）
"""
import datetime as dt, email, email.policy, hashlib, html, re, secrets
from . import db, audit, crypto, line, linetask
from .guard import guard
from .config import FORM_DAYS, TASK_FILES

MAX_FILE, MAX_TOTAL = 10_000_000, 30_000_000
CHOICES = ["必要", "不要", "相談したい"]
SIGS = [(b"%PDF", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png")]


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _mime(data):
    for sig, mime in SIGS:
        if data.startswith(sig):
            return mime
    return "image/heic" if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1") else None


def issue(kind, staff, group_id, task_id=None):
    token = secrets.token_urlsafe(24)
    db.run("INSERT INTO form_link(token_hash,kind,staff_id,group_id,task_id,expires) VALUES(?,?,?,?,?,?)", _hash(token), kind,
           staff["id"], group_id, task_id, (dt.datetime.now() + dt.timedelta(days=FORM_DAYS)).strftime("%Y-%m-%d %H:%M:%S"))
    audit.log("form_issue", staff=staff, channel="form", result=kind, refs=f"t{task_id}" if task_id else "")
    return token


def issue_text(kind, staff, group, task_id=None):
    """LINE で返す文。公開URL（受付サーバーのトンネル）が未設定なら案内だけ"""
    base = (db.setting("public_url") or "").rstrip("/")
    if not base:
        return "アップロード用の公開URLが設定されていません。管理者が hokenchat の［タスク］画面で設定してください。"
    if kind == "upload":
        t = db.one("SELECT * FROM task WHERE id=?", task_id)
        if not t or t["group_name"] != group["task_group"]:
            return f"No.{task_id} はこのグループの依頼にありません。"
        url = f"{base}/f/{issue('upload', staff, group['group_id'], task_id)}"
        return (f"No.{task_id} の書類（車検証・免許証など）のアップロード用リンクです。{FORM_DAYS}日間・1回だけ使えます。"
                f"お客さまや車屋さんにそのまま送れます。\n{url}")
    url = f"{base}/f/{issue('quote', staff, group['group_id'])}"
    return f"見積依頼フォームです（{FORM_DAYS}日間・1回だけ使えます）。送ると［{group['task_group']}］のタスクになります。\n{url}"


def _link(token):
    r = db.one("SELECT * FROM form_link WHERE token_hash=?", _hash(token or ""))
    if not r or r["used_at"] or r["expires"] < linetask.now():
        return None
    return r


PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title><style>
body{{font:16px/1.7 "Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;background:#f5f6f8;color:#1d2330;margin:0;padding:16px}}
main{{max-width:560px;margin:0 auto;background:#fff;border:1px solid #e3e6ec;border-radius:12px;padding:20px}}
h1{{font-size:20px;margin:0 0 4px}} p.sub{{color:#5d6676;font-size:14px;margin:0 0 16px}}
label{{display:block;margin:14px 0 4px;font-weight:600;font-size:14px}} .req::after{{content:" *";color:#b42318}}
input[type=text],input[type=date],textarea{{width:100%;box-sizing:border-box;font:inherit;padding:10px;border:1px solid #cfd4dc;border-radius:8px}}
textarea{{min-height:80px}} .opts{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:15px}} .opts label{{font-weight:400;margin:0}}
fieldset{{border:1px solid #e3e6ec;border-radius:10px;padding:4px 14px 12px;margin:14px 0}} legend{{font-size:14px;color:#5d6676}}
button{{margin-top:20px;width:100%;padding:14px;font:inherit;font-weight:600;color:#fff;background:#1f4fd1;border:0;border-radius:10px}}
.note{{font-size:13px;color:#5d6676;background:#eef2ff;border-radius:8px;padding:10px 12px;margin-top:14px}}
.err{{color:#b42318;font-weight:600}}</style></head><body><main>{body}</main></body></html>"""


def _page(title, body):
    return PAGE.format(title=html.escape(title), body=body)


def _radios(name, value=""):
    return "<div class='opts'>" + "".join(f"<label><input type='radio' name='{name}' value='{c}' {'checked' if c == value else ''}> {c}</label>"
                                         for c in CHOICES) + "</div>"


def page(token, error=""):
    r = _link(token)
    if not r:
        return 404, _page("リンクが無効です", "<h1>このリンクは使えません</h1><p class='sub'>期限が切れたか、すでに使われています。担当者に新しいリンクをお願いしてください。</p>")
    err = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    files = """<label>車検証（写真またはPDF）</label><input type="file" name="shaken" accept="image/*,application/pdf">
      <label>免許証（写真またはPDF）</label><input type="file" name="menkyo" accept="image/*,application/pdf">
      <label>その他の書類</label><input type="file" name="other" accept="image/*,application/pdf">"""
    if r["kind"] == "upload":
        t = db.one("SELECT id FROM task WHERE id=?", r["task_id"])
        body = f"""<h1>書類のアップロード</h1><p class="sub">受付番号 No.{t['id'] if t else '-'}。写真は文字がはっきり読めるように撮ってください。</p>{err}
          <form method="post" enctype="multipart/form-data">{files}
          <div class="note">送った書類は暗号化して保管し、担当者だけが見られます。LINE には送らないでください。</div>
          <button>送る</button></form>"""
        return 200, _page("書類のアップロード", body)
    body = f"""<h1>自動車保険 見積依頼</h1><p class="sub">営業 → 事務への見積依頼です。送るとタスクになり、担当グループの全員に届きます。</p>{err}
      <form method="post" enctype="multipart/form-data">
      <label class="req">依頼の種類</label><div class="opts"><label><input type="radio" name="kind" value="新規" checked> 新規</label>
        <label><input type="radio" name="kind" value="入替"> 車両入替</label></div>
      <label class="req">契約者（お名前・法人名）</label><input type="text" name="name" maxlength="60" required>
      <label>始期日</label><input type="date" name="start">
      <label class="req">車名（新しい車）</label><input type="text" name="car" maxlength="40" placeholder="例：ハイエース" required>
      <label>型式</label><input type="text" name="model" maxlength="40" placeholder="例：GDH201V">
      <label>入替の場合：入れ替える今の車</label><input type="text" name="old_car" maxlength="80" placeholder="例：プロボックス 登録番号の下4桁 1234">
      <fieldset><legend>補償</legend><label>車両保険</label>{_radios('vehicle')}<label>弁護士費用特約</label>{_radios('lawyer')}
        <label>代車費用特約</label>{_radios('rental')}</fieldset>
      {files}
      <label>その他・要望</label><textarea name="memo" maxlength="1000"></textarea>
      <div class="note">新規は車検証と免許証、入替は新しい車の車検証が必要です。今ない場合は、あとで「書類 No.〇〇」のリンクで送れます。
        病歴・マイナンバー・カード番号は書かないでください。</div>
      <button>見積を依頼する</button></form>"""
    return 200, _page("見積依頼", body)


def _parse(ctype, raw):
    msg = email.message_from_bytes(f"Content-Type: {ctype}\r\n\r\n".encode() + raw, policy=email.policy.HTTP)
    fields, files = {}, []
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            if data:
                files.append((name, data))
        else:
            fields[name] = data.decode("utf-8", "replace").strip()
    return fields, files


LABELS = {"shaken": "車検証", "menkyo": "免許証", "other": "その他の書類"}


def _store(tid, files):
    for name, data in files:
        ref = crypto.encrypt_file(data, TASK_FILES)
        db.run("INSERT INTO task_file(task_id,label,mime,size,ref) VALUES(?,?,?,?,?)", tid, LABELS.get(name, "書類"), _mime(data), len(data), ref)


def submit(token, ctype, raw):
    r = _link(token)
    if not r:
        return page(token)
    fields, files = _parse(ctype, raw)
    if len(files) > 3 or sum(len(d) for _, d in files) > MAX_TOTAL or any(len(d) > MAX_FILE for _, d in files):
        return page(token, "ファイルが大きすぎます（1つ10MBまで、合計30MBまで）")
    if any(not _mime(d) for _, d in files):
        return page(token, "送れるのは写真（JPEG・PNG・HEIC）か PDF だけです")
    staff = db.one("SELECT * FROM staff WHERE id=?", r["staff_id"])
    group = db.one("SELECT * FROM line_group WHERE group_id=?", r["group_id"])
    if r["kind"] == "upload":
        if not files:
            return page(token, "ファイルを選んでください")
        t = db.one("SELECT * FROM task WHERE id=?", r["task_id"])
        if not t:
            return page(token, "受付番号が見つかりません。担当者にお問い合わせください")
        _store(t["id"], files)
        linetask.event(t["id"], "file", "書類が届きました：" + "・".join(LABELS.get(n, "書類") for n, _ in files))
        notice = f"No.{t['id']}「{t['title']}」に書類が届きました（{len(files)}件）。hokenchat の［タスク］で確認できます。"
        tid = t["id"]
    else:
        f = {k: fields.get(k, "")[:1000] for k in ("kind", "name", "start", "car", "model", "old_car", "vehicle", "lawyer", "rental", "memo")}
        if f["kind"] not in ("新規", "入替") or not f["name"] or not f["car"]:
            return page(token, "依頼の種類・契約者・車名を入れてください")
        if f["start"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["start"]):
            return page(token, "始期日の形が正しくありません")
        if g := next((guard(v) for v in f.values() if guard(v)), None):
            return page(token, f"送れない情報が含まれています（{g}）")
        if not group or not group["task_group"]:
            return page(token, "受付先のグループが見つかりません。担当者にお問い合わせください")
        surname = re.split(r"[\s　]", f["name"])[0][:10]  # LINE に流すのは名字（法人は先頭の語）だけ
        title = f"見積 {f['kind']} {surname} {f['car']}"[:40]
        need = ["車検証", "免許証"] if f["kind"] == "新規" else ["車検証"]
        got = {LABELS.get(n) for n, _ in files}
        missing = [x for x in need if x not in got]
        detail = "\n".join(x for x in (
            f"種類：{f['kind']}", f"契約者：{f['name']}", f"始期日：{f['start'] or '未定'}", f"車名：{f['car']}　型式：{f['model'] or '—'}",
            f"入れ替える車：{f['old_car']}" if f["kind"] == "入替" else "",
            f"車両保険：{f['vehicle'] or '未選択'}／弁護士費用特約：{f['lawyer'] or '未選択'}／代車費用特約：{f['rental'] or '未選択'}",
            f"要望：{f['memo']}" if f["memo"] else "", f"未着の書類：{'・'.join(missing)}" if missing else "") if x)
        tid = db.run("""INSERT INTO task(title,detail,created_by,group_name,source,line_group,last_activity) VALUES(?,?,?,?,'line',?,?)""",
                     title, detail, staff["id"], group["task_group"], group["group_id"], linetask.now())
        linetask.event(tid, "create", "見積依頼フォームから", staff)
        _store(tid, files)
        audit.log("task_create", staff=staff, channel="form", refs=f"t{tid}", result="quote")
        notice = (f"No.{tid}「{title}」の見積依頼を受け付けました（{staff['name']}さん）。"
                  + (f"\n未着の書類：{'・'.join(missing)}。「@ボット 書類 No.{tid}」でアップロード用リンクを出せます。" if missing else ""))
    db.run("UPDATE form_link SET used_at=? WHERE token_hash=?", linetask.now(), _hash(token))
    try:
        line.push(r["group_id"], notice)
    except Exception as e:
        print("受付の通知を LINE に送れませんでした:", str(e)[:120])
    return 200, _page("受け付けました", f"<h1>受け付けました</h1><p class='sub'>受付番号 No.{tid}。この画面は閉じてかまいません。</p>")


def file_of(fid, staff):
    f = db.one("SELECT * FROM task_file WHERE id=?", fid)
    if not f:
        return None, None
    audit.log("task_file_view", staff=staff, channel="task", refs=f"f{fid}")
    return crypto.decrypt_file(f["ref"], TASK_FILES), f["mime"] or "application/octet-stream"
