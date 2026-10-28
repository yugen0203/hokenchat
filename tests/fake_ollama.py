"""テスト用の「にせ Ollama」（127.0.0.1:11434）。AI モデルなしで画面と処理の流れを確かめるためのもの

  埋め込み: 文字の出現回数による簡易ベクトル（意味は分からないが、同じ言葉を含む条が上に来る）
  分類    : キーワードで決める／RAG の行選び：1番目の資料の1行目／項目の抜き出し：正規表現
本物の精度は eval.py（本物の Ollama）で測る。使い方: python tests/fake_ollama.py
"""
import json, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VOCAB = list("保険自動車火災地震申込書控年事故請求住所変更解約証券再発行満期契約見積営業時間郵送規程条支払免責補償車両タイヤ台風浸水承認USB")
KEYS = [("住所", "住所変更"), ("引っ越", "住所変更"), ("電話番号が変", "住所変更"), ("買い替", "車両入替"), ("乗り換", "車両入替"),
        ("何時", "営業時間"), ("営業", "営業時間"), ("土曜", "営業時間"), ("ぶつけ", "事故受付"), ("当て逃げ", "事故受付"), ("水漏れ", "事故受付"),
        ("書類", "事故必要書類"), ("やめ", "解約"), ("解約", "解約"), ("証券", "証券再発行"), ("満期", "契約照会"), ("保険料", "契約照会"),
        ("入りたい", "商品相談"), ("見積", "商品相談"), ("おすすめ", "商品相談")]


def embed(t):
    return [float(t.count(c)) + 0.01 for c in VOCAB]


def extract(text, keys):
    out = {k: "" for k in keys}
    m = re.search(r"〒?\s*(\d{3}-?\d{4})", text)
    if m and "postal" in out:
        out["postal"] = m.group(1)
    m = re.search(r"(0\d{1,4}-\d{1,4}-\d{3,4})", text)
    if m and "phone" in out:
        out["phone"] = m.group(1)
    m = re.search(r"([^\s　（(「]{1,6}[\s　][^\s　（(]{1,6})（([ァ-ヴー]+)）", text)
    if m:
        for k in ("name", "contractor"):
            if k in out:
                out[k] = m.group(1)
        if "kana" in out:
            out["kana"] = m.group(2)
    m = re.search(r"((?:東京都|大阪府|京都府|北海道|..県)?[^\s　]*?[市区町村][^\s　]+(?:[\s　][^\s　]*(?:ビル|マンション|ハイツ)[^\s　]*)?)", text)
    if m and "address" in out:
        out["address"] = m.group(1)
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m and "start" in out:
        out["start"] = f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    for k, pat in (("car_name", r"(プリウス|アクア|フィット|ノート|N-BOX)"), ("car_model", r"（([A-Z0-9]{3,8})）"),
                   ("driver_limit", r"(家族限定|本人限定|夫婦限定|限定なし)"), ("driver_age", r"(\d{2}歳以上)"), ("vehicle_ins", r"車両保険は(一般|エコノミー|なし)"),
                   ("kind", r"(自動車|火災)保険")):
        m = re.search(pat, text)
        if m and k in out:
            out[k] = m.group(1)
    if "title" in out:
        out["title"] = text.strip()[:20]
    return out


class H(BaseHTTPRequestHandler):
    def _j(self, o):
        b = json.dumps(o, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._j({"models": [{"name": "qwen3:8b"}, {"name": "bge-m3"}]})

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            inp = b["input"] if isinstance(b["input"], list) else [b["input"]]
            return self._j({"embeddings": [embed(t) for t in inp]})
        user = b["messages"][-1]["content"]
        fmt = b.get("format")
        if not fmt:
            return self._j({"message": {"content": "1-1"}})
        props = fmt.get("properties", {})
        if "intent" in props:
            intent = next((v for k, v in KEYS if k in user), "その他")
            return self._j({"message": {"content": json.dumps({"intent": intent}, ensure_ascii=False)}})
        if list(props) == ["kind"]:
            return self._j({"message": {"content": json.dumps({"kind": "火災" if "火災" in user else "自動車"}, ensure_ascii=False)}})
        return self._j({"message": {"content": json.dumps(extract(user, list(props)), ensure_ascii=False)}})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("にせ Ollama: http://127.0.0.1:11434")
    ThreadingHTTPServer(("127.0.0.1", 11434), H).serve_forever()
