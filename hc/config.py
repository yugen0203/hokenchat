"""パス・定数。挙動を変える値はここに集める"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# HOKENCHAT_DATA でデータの置き場所を変えられる（テストで一時フォルダを使うため）
DATA = Path(os.environ.get("HOKENCHAT_DATA") or ROOT / "data")
SEED_DOCS = ROOT / "data" / "docs"          # 初期登録の約款・規程（Markdown）
UPLOADS = DATA / "uploads"                  # 登録文書の原本（約款・規程。顧客情報は入らない）
INBOX = DATA / "inbox"                      # フォルダ監視：ここに置いたファイルを「承認待ち」で取り込む
VAULT = DATA / "vault"                      # 申込書控え（暗号化）
LINE_FILES = DATA / "line_files"            # LINE で届いた画像・PDF（暗号化。AI は読まない）
KEYS = DATA / "keys"                        # 暗号鍵（バックアップ対象から外す）
DB = DATA / "hokenchat.db"
TOOLS = ROOT / "tools"
TEMPLATES_JSON = ROOT / "data" / "line_templates.json"
QUOTE_JSON = ROOT / "data" / "quote_templates.json"

HOST, PORT = "127.0.0.1", 8765              # 127.0.0.1 以外で待ち受けない
PUSH_URL = "http://127.0.0.1:8767/push"     # LINE 受付サーバーの送信口（社内のみ）
OLLAMA = "http://127.0.0.1:11434"
CHAT_MODEL = os.environ.get("HOKENCHAT_CHAT_MODEL", "qwen3:8b")
EMBED_MODEL = os.environ.get("HOKENCHAT_EMBED_MODEL", "bge-m3")
MIN_SCORE = 0.5   # ponytail: 固定しきい値。本番は実データの評価で調整する
TOP_K = 3

# ---- ロール（F-01）。管理者＝生成AI責任者を兼ねる（承認・停止スイッチ・ログ点検） ----
ROLES = {"admin": "管理者", "agent": "募集人", "clerk": "事務員"}
ALL = set(ROLES)
PERMS = {
    "chat": ALL, "line": ALL, "line.attach": {"admin", "clerk", "agent"},
    "docs.view": ALL, "docs.upload": ALL, "docs.approve": {"admin"}, "docs.delete": {"admin"},
    "customer": ALL, "mailing": ALL, "quote": ALL, "task": ALL,
    "vault.list": ALL, "vault.register": ALL, "vault.view": {"admin", "clerk"},
    "vault.request": ALL, "vault.approve": {"admin"}, "vault.run": {"admin"},
    "audit.view": {"admin"}, "staff.manage": {"admin"}, "ai.switch": {"admin"},
}

# ---- 利用目的タグ（F-02）。目的ごとに見られる顧客DBの項目を絞る ----
CUSTOMER_FIELDS = ["name", "kana", "postal", "address", "phone", "email", "note"]
PURPOSES = {
    "契約管理": CUSTOMER_FIELDS + ["policies"],
    "送付状作成": ["name", "kana", "postal", "address"],
    "更改案内": ["name", "kana", "postal", "address", "phone", "policies"],
    "事故対応": ["name", "kana", "phone", "policies"],
    "見積依頼": ["name", "kana", "policies"],
    "お客さま対応": ["name", "kana", "phone", "email", "policies"],
}
PURPOSES_OTHER = ["約款・規程の検索", "文書管理", "社内事務"]  # 顧客情報を使わない目的

# ---- 申込書控えの保存期間（代表決定 2026/10/23。起算日＝登録日、満了日＝応当日） ----
RETENTION_YEARS = {"自動車": 3, "火災": 5, "その他": 3}
UNSET_KIND = "未設定"
DELETE_HOUR = 2          # 日次ジョブ（午前2:00）
NOTICE_DAYS = (14, 1)    # 満了14日前と前日に事前通知
HOLD_REVIEW_MONTHS = 6   # リーガルホールドの見直し間隔
HOLD_REASONS = {"H1": "紛争・訴訟中", "H2": "事故対応中", "H3": "保険会社からの照会中", "H9": "その他（責任者判断）"}

LINE_RETENTION_DAYS = 90       # トーク・添付（N-16）
TASK_RETENTION_DAYS = 365      # 完了したタスク（N-16）
QUOTE_RETENTION_DAYS = 365     # 成約しなかった見積依頼書（N-16）
SESSION_HOURS = 9
