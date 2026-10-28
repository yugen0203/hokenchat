"""フェーズ2の自動テスト（AI モデル不要。LLM はテスト用の偽物に置き換える）

  実行: python tests/test_phase2.py      （一時フォルダにデータを作り、終わったら消す）
  本物の Ollama での精度は eval.py で測る
"""
import base64, datetime as dt, json, os, shutil, sys, tempfile, threading, unittest, urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="hokenchat-test-")
os.environ["HOKENCHAT_DATA"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hc import llm  # noqa: E402

VOCAB = list("保険自動車火災地震申込書控年事故請求住所変更解約証券傷害入院")


def fake_chat(system, user, schema=None, max_tokens=None):
    if not schema:
        return "1-1"
    props = schema["properties"]
    if "intent" in props:
        return json.dumps({"intent": "住所変更" if "住所" in user else "事故受付" if "ぶつけ" in user else "その他"}, ensure_ascii=False)
    out = {k: "" for k in props}
    if "name" in out:
        out |= {"name": "架空 花子", "postal": "530-0001", "phone": "06-0000-0000", "address": "大阪府大阪市北区梅田1-2-3"}
    if "contractor" in out:
        out |= {"contractor": "架空 一郎", "start": "2026-12-01", "car_name": "プリウス", "car_model": "ZZZ99"}  # 型式は原文にない＝作り話
    if "kind" in out and len(out) == 1:
        out["kind"] = "自動車"
    return json.dumps(out, ensure_ascii=False)


_real_post = llm._post


def fake_post(path, body):
    """Ollama の代わり。停止スイッチの判定は本物の llm._post と同じく先に行う"""
    if not llm.enabled():
        raise llm.AIStopped("AIは管理者により停止中です")
    if path == "/api/embed":
        return {"embeddings": [[float(t.count(c)) + 0.01 for c in VOCAB] for t in body["input"]]}
    m = body["messages"]
    return {"message": {"content": fake_chat(m[0]["content"], m[-1]["content"], body.get("format"))}}


llm._post = fake_post
import app  # noqa: E402
from hc import auth, audit, crypto, db, rag, vault  # noqa: E402
from hc.config import VAULT  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
BASE = f"http://127.0.0.1:{srv.server_port}"
threading.Thread(target=srv.serve_forever, daemon=True).start()


class Client:
    def __init__(self):
        self.cookie = ""

    def req(self, path, body=None, *, raw=None, headers=None, csrf=True):
        h = {"Cookie": self.cookie} | ({"X-HC": "1"} if csrf else {}) | {k: urllib.parse.quote(v) for k, v in (headers or {}).items()}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if body is not None:
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(BASE + path, data, h, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r) as x:
                if c := x.headers.get("Set-Cookie"):
                    self.cookie = c.split(";")[0]
                ct = x.headers.get("Content-Type", "")
                return x.status, (json.loads(x.read()) if "json" in ct else x.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def login(self, login, pw):
        s, t = self.req("/api/login", {"login": login, "password": pw})
        assert s == 200, t
        secret = t.get("secret") or auth.db.one("SELECT mfa_secret FROM staff WHERE login=?", login)["mfa_secret"]
        s, r = self.req("/api/login/code", {"ticket": t["ticket"], "code": auth.totp(secret)})
        assert s == 200, r
        return self


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rag.seed()
        c = Client()
        assert c.req("/api/setup", {"login": "admin", "name": "管理 太郎", "password": "Admin12345"})[0] == 200
        cls.admin = Client().login("admin", "Admin12345")
        assert cls.admin.req("/api/staff/create", {"login": "jimu", "name": "事務 花子", "role": "clerk", "password": "Karipass99"})[0] == 200
        j = Client().login("jimu", "Karipass99")
        assert j.req("/api/password", {"old": "Karipass99", "new": "Jimu12345x"})[0] == 200
        cls.jimu = j

    # ---- 認証・権限 ----
    def test_auth(self):
        self.assertEqual(Client().req("/api/setup", {"login": "x", "name": "x", "password": "Xx12345678"})[0], 400)  # 2回目の初期設定は不可
        self.assertEqual(Client().req("/api/internal", {"text": "a"})[0], 401)                                         # 未ログイン
        self.assertEqual(self.admin.req("/api/internal", {"text": "a"}, csrf=False)[0], 403)                          # CSRF ヘッダなし
        self.assertEqual(self.jimu.req("/api/audit")[0], 403)                                                          # 事務員は監査ログ不可
        s, t = Client().req("/api/login", {"login": "admin", "password": "Admin12345"})
        self.assertEqual(Client().req("/api/login/code", {"ticket": t["ticket"], "code": "000000"})[0], 401)          # 確認コード違い
        self.assertTrue(auth.check_totp("JBSWY3DPEHPK3PXP", auth.totp("JBSWY3DPEHPK3PXP")))                     # RFC 6238

    def test_line_internal_token(self):
        body = {"user_id": "U1", "name": "テスト", "type": "text", "text": "住所を変えたい"}
        self.assertEqual(Client().req("/api/line/incoming", body, csrf=False)[0], 403)
        s, r = Client().req("/api/line/incoming", body, csrf=False, headers={"X-Internal-Token": app.internal_token()})
        self.assertIn("ご住所", r["reply"])
        img = {"user_id": "U1", "name": "テスト", "type": "image", "mime": "image/png", "content_b64": base64.b64encode(b"\x89PNGfake").decode()}
        Client().req("/api/line/incoming", img, csrf=False, headers={"X-Internal-Token": app.internal_token()})
        ms = self.admin.req("/api/line/msgs?user=U1")[1]
        aid = [m for m in ms if m["attach_id"]][-1]["attach_id"]
        self.assertEqual(self.admin.req(f"/api/line/attach?id={aid}")[1], b"\x89PNGfake")
        self.assertNotIn(b"PNGfake", next(Path(TMP, "line_files").glob("*.bin")).read_bytes())  # 暗号化されている
        self.assertEqual(self.admin.req("/api/line/draft", {"user_id": "U1"})[1]["intent"], "住所変更")

    # ---- 利用目的タグ ----
    def test_purpose(self):
        c = Client().login("admin", "Admin12345")
        self.assertEqual(c.req("/api/customers")[0], 428)                       # 目的なしは不可
        c.req("/api/purpose", {"purpose": "契約管理"})
        s, r = c.req("/api/customers/save", {"name": "架空 次郎", "postal": "5300001", "address": "大阪府", "phone": "06-1111-2222",
                                             "policies": [{"kind": "自動車", "expiry": "2027-04-01"}]})
        self.assertEqual(s, 200, r)
        self.assertEqual(c.req("/api/customers/save", {"name": "架空 三郎", "note": "持病あり"})[0], 400)  # 要配慮情報はメモにも不可
        c.req("/api/purpose", {"purpose": "見積依頼"})
        rec = c.req(f"/api/customer?id={r['id']}")[1]
        self.assertNotIn("address", rec)                                        # 見積依頼では住所は見えない
        self.assertIn("policies", rec)
        self.assertEqual(c.req("/api/customers/save", {"id": r["id"], "name": "x"})[0], 428)  # 修正は「契約管理」だけ
        c.req("/api/purpose", {"purpose": "契約管理"})
        self.assertEqual(c.req("/api/customers/extract", {"text": "マイナンバー 1234 5678 9018"})[1].get("blocked"), "マイナンバーらしき番号")
        d = c.req("/api/customers/extract", {"text": "架空 花子 〒530-0001 大阪府大阪市北区梅田1-2-3"})[1]
        self.assertEqual(d["customer"]["phone"], "")                            # 原文にない電話番号は捨てる

    # ---- 文書の版管理 ----
    def test_docs_versions(self):
        txt = "第1条（目的）\n傷害保険の目的。\n第2条（入院保険金）\n入院保険金は180日以内の入院に支払う。".encode()
        s, r = self.jimu.req("/api/docs/upload", raw=txt, headers={"X-Filename": "a.txt", "X-Title": "傷害約款", "X-Version": "v1"})
        self.assertEqual(r["status"], "pending")
        self.assertFalse(any(h["doc"] == "傷害約款" for h in rag.search("傷害 入院")))  # 承認前は検索されない
        self.assertEqual(self.jimu.req("/api/docs/approve", {"id": r["id"]})[0], 403)     # 事務員は承認できない
        self.admin.req("/api/docs/approve", {"id": r["id"]})
        self.assertTrue(any(h["doc"] == "傷害約款" and h["version"] == "v1" for h in rag.search("傷害 入院")))
        s, r2 = self.admin.req("/api/docs/upload", raw=txt.replace(b"180", b"270"), headers={"X-Filename": "a.txt", "X-Title": "傷害約款", "X-Version": "v2"})
        self.admin.req("/api/docs/approve", {"id": r2["id"]})
        vs = {h["version"] for h in rag.search("傷害 入院") if h["doc"] == "傷害約款"}
        self.assertEqual(vs, {"v2"})                                             # 旧版は検索から外れる
        s, r3 = self.admin.req("/api/docs/upload", raw=b"card 4111 1111 1111 1111", headers={"X-Filename": "b.txt", "X-Title": "x"})
        self.assertEqual(s, 400)                                                 # カード番号を含む資料は登録しない

    # ---- 文書保管庫 ----
    def test_vault(self):
        st = {"id": 1}
        r = vault.register("a.pdf", b"%PDF-secret-1", None, "控え", ["自動車", "火災"], st, registered=dt.date(2024, 2, 29))
        self.assertEqual(r["expires"], "2029-02-28")                             # 複数種目は最長（火災5年）、2/29 の応当日
        old = vault.register("b.pdf", b"%PDF-secret-2", None, "控え2", ["自動車"], st, registered=dt.date(2023, 10, 1))
        unset = vault.register("c.pdf", b"%PDF-secret-3", None, "控え3", [], st, registered=dt.date(2020, 1, 1))
        held = vault.register("d.pdf", b"%PDF-secret-4", None, "控え4", ["その他"], st, registered=dt.date(2022, 1, 1))
        ref = db.one("SELECT ref FROM application_copy WHERE id=?", old["id"])["ref"]
        self.assertNotIn(b"secret-2", (VAULT / f"{ref}.bin").read_bytes())       # 暗号化
        # ホールド：本人の承認は不可、別の人なら可
        s, q = self.jimu.req("/api/vault/request", {"copy_id": held["id"], "type": "hold", "reason": "H2"})
        self.assertEqual(self.jimu.req("/api/vault/decide", {"id": q["id"], "approve": True})[0], 403)
        s, q2 = self.admin.req("/api/vault/request", {"copy_id": old["id"], "type": "kind", "kinds": ["火災"]})
        self.assertIn("自分の申請", self.admin.req("/api/vault/decide", {"id": q2["id"], "approve": True})[1]["error"])
        self.admin.req("/api/vault/decide", {"id": q["id"], "approve": True})
        self.admin.req("/api/vault/decide", {"id": q2["id"], "approve": False})
        # 14日前の通知 → 満了日の削除
        out = vault.run_daily(now=dt.date(2026, 9, 20))
        self.assertGreaterEqual(out["notice"], 1)
        out = vault.run_daily(now=dt.date(2026, 10, 2))
        self.assertEqual(out["deleted"], 1)
        self.assertFalse(crypto.has_key(ref))                                    # 鍵を破棄（暗号化消去）
        self.assertFalse((VAULT / f"{ref}.bin").exists())
        self.assertTrue(db.one("SELECT 1 FROM application_copy WHERE id=?", unset["id"]))  # 未設定は消さない
        self.assertTrue(db.one("SELECT 1 FROM application_copy WHERE id=?", held["id"]))   # ホールド中は消さない
        log = db.one("SELECT * FROM deletion_log WHERE copy_id=?", old["id"])
        self.assertEqual(set(log) - {"id"}, {"copy_id", "expires", "ts", "executor", "job_id", "check_ok"})  # 個人データは持たない
        rec = vault.reconcile("2026-10")
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(self.jimu.req("/api/vault/run", {})[0], 403)

    # ---- 郵送 ----
    def test_mailing(self):
        c = Client().login("admin", "Admin12345")
        c.req("/api/purpose", {"purpose": "契約管理"})
        cid = c.req("/api/customers/save", {"name": "架空 花子", "postal": "530-0001", "address": "大阪府大阪市北区梅田1-2-3　サンプルビル5階"})[1]["id"]
        c.req("/api/purpose", {"purpose": "送付状作成"})
        m = c.req("/api/mailing/create", {"customer_ids": [cid], "doc_kind": "更改のご案内", "envelope": "長3"})[1]
        for what in ("letter", "envelope", "labels"):
            s, pdf = c.req(f"/api/mailing/pdf?ids={m['ids'][0]}&what={what}")
            self.assertTrue(pdf.startswith(b"%PDF"), what)
        self.assertIn("別の担当者", c.req("/api/mailing/check", {"id": m["ids"][0]})[1]["error"])  # 作成者は照合できない
        self.jimu.req("/api/purpose", {"purpose": "送付状作成"})
        self.assertEqual(self.jimu.req("/api/mailing/check", {"id": m["ids"][0]})[0], 200)

    # ---- 見積依頼書 ----
    def test_quotes(self):
        q = self.admin.req("/api/quotes/draft", {"text": "架空一郎さん 12月1日始期 プリウスの見積", "kind": "自動車"})[1]
        self.assertEqual(q["fields"]["car_model"], "")                          # 原文にない型式は捨てる
        self.assertEqual(q["fields"]["start"], "2026-12-01")
        self.assertIn("型式", q["missing"])
        self.assertEqual(self.admin.req("/api/quotes/approve", {"id": q["id"]})[0], 400)
        fields = {f["key"]: (q["fields"][f["key"]] or "テスト値") for f in q["template"]}
        self.admin.req("/api/quotes/update", {"id": q["id"], "fields": fields})
        self.assertEqual(self.admin.req("/api/quotes/approve", {"id": q["id"]})[1]["status"], "承認済")
        self.assertTrue(self.admin.req(f"/api/quotes/sheet?id={q['id']}")[1].startswith(b"%PDF"))
        r = self.admin.req("/api/quotes/reconcile", {"id": q["id"], "text": " ".join(fields.values())})[1]
        self.assertEqual(r["status"], "突合済")
        r = self.admin.req("/api/quotes/reconcile", {"id": q["id"], "text": "まったく別の内容"})[1]
        self.assertEqual(r["status"], "差異あり")

    # ---- 監査ログ・停止スイッチ ----
    def test_zz_audit_and_switch(self):
        self.assertTrue(self.admin.req("/api/audit/verify")[1]["ok"])
        rows = json.dumps(self.admin.req("/api/audit?limit=1000")[1], ensure_ascii=False)
        self.assertNotIn("住所を変えたい", rows)                                 # 本文は残さない
        self.assertNotIn("1234 5678 9018", rows)
        self.admin.req("/api/ai/switch", {"enabled": False})
        self.assertEqual(self.admin.req("/api/internal", {"text": "地震"})[0], 503)
        s, r = Client().req("/api/line/incoming", {"user_id": "U2", "type": "text", "text": "こんにちは"}, csrf=False,
                            headers={"X-Internal-Token": app.internal_token()})
        self.assertTrue(r["reply"])                                              # 停止中もお客さまを無言にしない
        self.admin.req("/api/ai/switch", {"enabled": True})
        db.run("UPDATE audit_log SET result='書き換え' WHERE id=2")
        self.assertFalse(self.admin.req("/api/audit/verify")[1]["ok"])            # 改ざんを検出


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
