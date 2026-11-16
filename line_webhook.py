"""公式LINE の受付サーバー（中継）

  受信: LINE → 公開URL（試作はトンネル） → 127.0.0.1:8766 → hokenchat /api/line/incoming（1:1）・/api/line/group（社内グループ）
  送信: hokenchat の画面（担当者） → 127.0.0.1:8767/push → LINE（プッシュメッセージ）
  フォーム: 外 → 8766 の /f/<token> → hokenchat /api/form/*（見積依頼・書類アップロードの使い切りリンク）

外から届くのは 8766 の /line/webhook と /f/ の2つだけ。8767 はトンネルに載せない社内専用の口。
LINE とインターネット越しに話すのはこの中継だけで、hokenchat 本体（AI・データ）は外に出ない（本番は DMZ の受付サーバーに相当）。
設定: proto/.env に次の2行を書く（チャットや画面には貼らない）
  LINE_CHANNEL_SECRET=...
  LINE_CHANNEL_ACCESS_TOKEN=...
起動: python3 line_webhook.py      自己テスト: python3 line_webhook.py --selftest
"""
import base64, hashlib, hmac, json, re, sys, threading, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
APP = "http://127.0.0.1:8765/api/line/incoming"
APP_GROUP = "http://127.0.0.1:8765/api/line/group"
APP_FORM = "http://127.0.0.1:8765/api/form/"
MAX_FORM = 32_000_000
LINE_API = "https://api.line.me/v2/bot"
LINE_DATA = "https://api-data.line.me/v2/bot"   # 画像・ファイルの中身を取る口
import os  # noqa: E402
# 本体が作る合言葉（同じ PC の中だけで使う）。本体と同じく HOKENCHAT_DATA でデータの置き場所を変えられる
TOKEN_FILE = Path(os.environ.get("HOKENCHAT_DATA") or ROOT / "data") / "keys" / "internal.token"
MAX_FILE = 10_000_000
FALLBACK = "お問い合わせありがとうございます。内容を確認し、担当者からご連絡します。お急ぎの場合は0120-000-001（平日9:00〜17:00）へお電話ください。"
names = {}  # ponytail: 表示名のキャッシュはメモリだけ。再起動したら取り直す


def load_env():
    env = {}
    for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
        if "=" in l and not l.lstrip().startswith("#"):
            k, v = l.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def verify(secret, body, sig):
    """LINE からの通知か（なりすましでないか）を署名で確かめる"""
    mac = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return bool(sig) and hmac.compare_digest(mac, sig)


def call(url, data=None, token=None, internal=False):
    h = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {token}"} if token else {})
    if internal:
        h["X-Internal-Token"] = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
    req = urllib.request.Request(url, json.dumps(data).encode() if data is not None else None, h)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return json.loads(body) if body else {}


def name_of(uid, token):
    if uid not in names:
        try:
            names[uid] = call(f"{LINE_API}/profile/{uid}", token=token).get("displayName", "")
        except Exception:
            names[uid] = ""
    return names[uid]


def content(mid, token):
    """お客さまが送った画像・PDFの中身を LINE から取る（中身は本体で暗号化して保管。ここでは保存しない）"""
    req = urllib.request.Request(f"{LINE_DATA}/message/{mid}/content", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_FILE + 1)
        return (data, r.headers.get("Content-Type", "").split(";")[0]) if len(data) <= MAX_FILE else (b"", "")


def strip_mentions(msg):
    """ボットへのメンションの文字（@名前）を本文から除く。LINE の index は文字の位置（ponytail: 絵文字の位置ずれは気にしない）"""
    text, me = msg.get("text", ""), False
    for m in sorted(msg.get("mention", {}).get("mentionees", []), key=lambda m: -m.get("index", 0)):
        if m.get("isSelf"):
            me = True
            text = text[:m["index"]] + text[m["index"] + m["length"]:]
    return me, text.strip()


def group_name(gid, token):
    try:
        return call(f"{LINE_API}/group/{gid}/summary", token=token).get("groupName", "")
    except Exception:
        return ""


def handle_group(ev, token):
    """社内のグループ（営業・事務）でのやり取り。タスク化は本体が判断する"""
    src, msg = ev.get("source", {}), ev.get("message", {})
    kind = ev.get("type")
    if kind not in ("join", "leave", "message") or (kind == "message" and msg.get("type") != "text"):
        return
    me, cmd = strip_mentions(msg) if kind == "message" else (False, "")
    b = {"type": kind, "group_id": src.get("groupId"), "user_id": src.get("userId"), "msg_id": msg.get("id"),
         "text": msg.get("text", ""), "command": cmd, "mention_self": me, "quoted_id": msg.get("quotedMessageId")}
    if kind == "join":
        b["group_name"] = group_name(b["group_id"], token)
    print("グループ:", kind, "メンション" if me else "")  # 本文は出さない
    try:
        reply = call(APP_GROUP, b, internal=True).get("reply")
    except Exception as e:
        print("hokenchat本体のエラー:", str(e)[:200])
        reply = "hokenchat が止まっています。依頼は登録されていません。しばらくしてからもう一度メンションしてください。" if me else None
    if reply and "replyToken" in ev:
        try:
            call(f"{LINE_API}/message/reply", {"replyToken": ev["replyToken"], "messages": [{"type": "text", "text": reply[:4900]}]}, token)
        except Exception as e:
            print("返信に失敗:", e)


def handle(events, token):
    for ev in events:
        if ev.get("source", {}).get("type") == "group":
            handle_group(ev, token)
            continue
        uid = ev.get("source", {}).get("userId")
        kind = ev.get("type")
        if not uid or ev.get("source", {}).get("type") != "user" or kind not in ("message", "follow"):
            continue  # 1:1 トーク・グループ以外（複数人トーク等）とその他のイベントは扱わない
        msg = ev.get("message", {})
        print("受信:", kind, msg.get("type", ""))  # 種類だけ。本文は出さない
        b = {"user_id": uid, "name": name_of(uid, token), "type": "follow" if kind == "follow" else msg.get("type"),
             "text": msg.get("text", "")}
        if b["type"] in ("image", "file"):
            try:
                data, mime = content(msg["id"], token)
                b |= {"content_b64": base64.b64encode(data).decode(), "mime": mime, "file_name": msg.get("fileName", "")}
            except Exception as e:
                print("画像・ファイルの取得に失敗:", str(e)[:200])
        try:
            reply = call(APP, b, internal=True).get("reply")
        except Exception as e:
            print("hokenchat本体のエラー:", str(e)[:200])
            reply = FALLBACK if kind == "message" else None  # 本体が止まっていても、お客さまを無言にしない
        if reply and "replyToken" in ev:
            try:
                call(f"{LINE_API}/message/reply", {"replyToken": ev["replyToken"], "messages": [{"type": "text", "text": reply}]}, token)
                print("返信しました")
            except Exception as e:
                print("返信に失敗:", e)


def form_relay(method, token, body=b"", ctype=""):
    """/f/<token> を本体に取り次ぐ。本体は外に出さず、受付サーバーだけが合言葉で話す"""
    h = {"X-Internal-Token": TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""}
    if method == "GET":
        req = urllib.request.Request(APP_FORM + "page?t=" + urllib.parse.quote(token), headers=h)
    else:
        req = urllib.request.Request(APP_FORM + "submit", body, h | {"X-Form-Token": token, "X-Form-Type": ctype, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


class Webhook(BaseHTTPRequestHandler):
    """外（LINE・フォームを開く人）から届く口"""
    def _form(self, method):
        token = self.path[3:].split("?")[0]
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token) or n > MAX_FORM:
            self.send_response(404); self.end_headers(); return
        try:
            page = form_relay(method, token, self.rfile.read(n) if n else b"", self.headers.get("Content-Type", ""))
            code = 200
        except Exception as e:
            print("フォームの取り次ぎに失敗:", str(e)[:200])
            page, code = "<!doctype html><meta charset='utf-8'><p>ただいま受け付けられません。時間をおいてもう一度お試しください。</p>".encode(), 503
        self.send_response(code)
        for k, v in (("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                     ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                     ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        if self.path.startswith("/f/"):
            return self._form("POST")
        env = load_env()
        n = int(self.headers.get("Content-Length", 0))
        if self.path != "/line/webhook" or n > 1_000_000:
            self.send_response(404); self.end_headers(); return
        body = self.rfile.read(n)
        if not verify(env.get("LINE_CHANNEL_SECRET", ""), body, self.headers.get("X-Line-Signature")):
            print("署名が不正なリクエストを拒否")
            self.send_response(401); self.end_headers(); return
        # LINE には先に 200 を返し、処理は裏で行う（公式の推奨）
        self.send_response(200); self.end_headers()
        threading.Thread(target=handle, args=(json.loads(body).get("events", []), env.get("LINE_CHANNEL_ACCESS_TOKEN", "")),
                         daemon=True).start()

    def do_GET(self):
        if self.path.startswith("/f/"):
            return self._form("GET")
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


class Push(BaseHTTPRequestHandler):
    """社内（hokenchat 本体）から送る口。トンネルには載せない
    同じ PC の他のプログラムやブラウザのページから勝手に送られないよう、本体との合言葉と JSON を必須にする"""
    def do_POST(self):
        want = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
        if (not want or not hmac.compare_digest(self.headers.get("X-Internal-Token", ""), want)
                or not self.headers.get("Content-Type", "").startswith("application/json")):
            print("合言葉のない送信の依頼を拒否")
            self.send_response(403); self.end_headers(); return
        try:
            b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            call(f"{LINE_API}/message/push", {"to": b["to"], "messages": [{"type": "text", "text": b["text"][:2000]}]},
                 load_env().get("LINE_CHANNEL_ACCESS_TOKEN", ""))
            print("担当者のメッセージを送信しました")
            self.send_response(200)
        except Exception as e:
            print("送信に失敗:", e)
            self.send_response(502)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        b = b'{"events":[]}'
        good = base64.b64encode(hmac.new(b"s3cret", b, hashlib.sha256).digest()).decode()
        assert verify("s3cret", b, good)
        assert not verify("s3cret", b + b" ", good)
        assert not verify("other", b, good)
        assert not verify("s3cret", b, None)
        me, cmd = strip_mentions({"text": "@hokenchat 一覧", "mention": {"mentionees": [{"index": 0, "length": 10, "isSelf": True}]}})
        assert me and cmd == "一覧", cmd
        assert strip_mentions({"text": "@田中 よろしく", "mention": {"mentionees": [{"index": 0, "length": 3}]}}) == (False, "@田中 よろしく")
        sys.exit(print("署名検証: OK"))
    env = load_env()
    missing = [k for k in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN") if not env.get(k)]
    print("未設定:", missing) if missing else print("LINE の設定: OK")
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8767), Push).serve_forever, daemon=True).start()
    print("受信口: http://127.0.0.1:8766/line/webhook（トンネルで公開）／送信口: http://127.0.0.1:8767/push（社内のみ）")
    ThreadingHTTPServer(("127.0.0.1", 8766), Webhook).serve_forever()
