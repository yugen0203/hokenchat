"""ローカルLLM（Ollama、127.0.0.1 のみ）。外部の AI サービスには接続しない（注意点2(1)①）

停止スイッチ（F-13）：管理者が止めると、以後の AI 呼び出しはすべて AIStopped になる。業務は AI なしで続けられる（N-09）
"""
import json, urllib.request
from . import db
from .config import OLLAMA, CHAT_MODEL, EMBED_MODEL


class AIStopped(Exception):
    pass


def enabled():
    return db.setting("ai_enabled", "1") == "1"


def _post(path, body):
    if not enabled():
        raise AIStopped("AIは管理者により停止中です")
    req = urllib.request.Request(OLLAMA + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


# keep_alive=-1: モデルをメモリに置いたままにする（外れると次の1回目に50〜70秒かかる）
def embed(texts):
    return _post("/api/embed", {"model": EMBED_MODEL, "input": texts, "keep_alive": -1})["embeddings"]


def chat(system, user, schema=None, max_tokens=None):
    """temperature 0・思考なし。schema を渡すと JSON スキーマで出力を固定する（選択肢以外を出せない）"""
    opts = {"temperature": 0} | ({"num_predict": max_tokens} if max_tokens else {})
    body = {"model": CHAT_MODEL, "stream": False, "think": False, "keep_alive": -1, "options": opts,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if schema:
        body["format"] = schema
    return _post("/api/chat", body)["message"]["content"]


def chat_json(system, user, schema):
    try:
        return json.loads(chat(system, user, schema))
    except json.JSONDecodeError:
        return {}


def alive():
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=3)
        return True
    except Exception:
        return False


def warmup():
    """起動直後に両方のモデルを読み込んでおく（1回目の質問を待たせない）"""
    try:
        embed(["準備"])
        chat("はいとだけ答える", "準備", max_tokens=1)
        print("モデル読み込み完了")
    except Exception as e:
        print("モデル読み込み失敗:", e)
