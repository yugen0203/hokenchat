"""郵送：送付状・封筒（長3・角2）・宛名ラベル（D-07／F-06、UC-01、社内事務規程 第1条・第2条）

  宛名は顧客DBから差し込み、手入力で転記しない。作成者とは別の担当者が照合（ダブルチェック）してから発送する。
  PDF の作成・印刷は監査ログに記録する（CL No.30）。文面はテンプレートで、AI は使わない。
  AI が作った文面を含める場合は「AI表記」を付けられる（注意点2(3)③、F-11）
"""
import datetime as dt, json
import pymupdf
from . import db, audit

FONT = "japan"  # PyMuPDF 内蔵の日本語フォント（外部のフォントファイル不要）
MM = 72 / 25.4
SENDER_DEFAULT = {"agency_name": "サンプル保険代理店（架空）", "agency_postal": "530-0001",
                  "agency_address": "大阪府大阪市北区梅田0丁目0番0号（架空）", "agency_tel": "0120-000-001", "agency_staff": ""}
GREET = "拝啓　時下ますますご清祥のこととお喜び申し上げます。平素は格別のお引き立てを賜り、厚く御礼申し上げます。"
CLOSE = "ご不明な点がございましたら、下記までお気軽にお問い合わせください。\n今後ともよろしくお願い申し上げます。"
DOC_KINDS = {
    "更改のご案内": {"lead": "さて、ご契約中の保険が満期を迎えますので、更改のご案内をお送りいたします。同封の書類をご確認のうえ、ご署名・ご捺印の箇所にご記入いただき、返信用封筒にてご返送ください。",
                     "items": ["更改のご案内", "重要事項説明書", "申込書（ご署名欄に付箋）", "返信用封筒"]},
    "保険証券の送付": {"lead": "さて、このたびはご契約いただき誠にありがとうございます。保険証券をお送りいたしますので、内容をご確認のうえ大切に保管してください。",
                       "items": ["保険証券", "ご契約のしおり（約款）"]},
    "申込書控えの送付": {"lead": "さて、先日お手続きいただきました申込書のお客さま控えをお送りいたします。大切に保管してください。",
                         "items": ["申込書（お客さま控え）", "重要事項説明書"]},
    "保険金請求書類の送付": {"lead": "さて、このたびの事故につきまして、保険金のご請求に必要な書類をお送りいたします。同封のご案内に沿ってご記入のうえ、返信用封筒にてご返送ください。",
                             "items": ["保険金請求書", "事故状況報告書", "必要書類のご案内", "返信用封筒"]},
    "各種手続き書類の送付": {"lead": "さて、ご依頼いただきましたお手続きの書類をお送りいたします。ご記入のうえ、返信用封筒にてご返送ください。",
                             "items": ["異動・変更の申請書", "返信用封筒"]},
}
ENVELOPES = {"長3": (120, 235), "角2": (240, 332)}
AI_NOTE = "※この文書の一部は、生成AIを利用して作成した文面を含みます（内容は担当者が確認済みです）。"


def sender():
    return {k: db.setting(k, v) for k, v in SENDER_DEFAULT.items()}


def set_sender(b):
    for k in SENDER_DEFAULT:
        if k in b:
            db.set_setting(k, str(b[k]).strip()[:120])


def _date_ja(d=None):
    d = d or dt.date.today()
    y = d.year - 2018
    return f"令和{'元' if y == 1 else y}年{d.month}月{d.day}日"


# ---------- 文字の配置 ----------
# 内蔵の日本語フォントは半角の英数字の幅が不ぞろいになるので、印字の前に全角にそろえる
ZEN = str.maketrans({chr(c): chr(c + 0xFEE0) for c in range(0x21, 0x7F)} | {" ": "　"})


def _text(page, x, y, s, size=10.5, color=(0, 0, 0)):
    page.insert_text((x, y), str(s).translate(ZEN), fontname=FONT, fontsize=size, color=color)


def _box(page, rect, s, size=10.5, align=0):
    s = str(s).translate(ZEN)
    r = pymupdf.Rect(*rect)
    while page.insert_textbox(r, s, fontname=FONT, fontsize=size, align=align, lineheight=1.5) < 0 and size > 6:
        size -= 0.5  # 入りきらなければ少し小さくする
    return size


KANJI = str.maketrans("0123456789０１２３４５６７８９-－‐ー―", "〇一二三四五六七八九〇一二三四五六七八九｜｜｜｜｜")
VERT = str.maketrans("（）()「」[]［］", "︵︶︵︶﹁﹂﹇﹈﹇﹈")


def _vertical(page, x, y, s, size, gap=1.08, bottom=None):
    """縦書き：1文字ずつ下へ置く（x は列の中心、y は上端）。数字は漢数字、ハイフンは縦棒にする"""
    s = s.translate(KANJI).translate(VERT)
    step = size * gap
    if bottom and len(s) * step > bottom - y:  # 入りきらなければ詰める
        step = (bottom - y) / max(len(s), 1)
        size = min(size, step / 1.02)
    for i, ch in enumerate(s):
        if ch == " " or ch == "　":
            continue
        _text(page, x - size / 2, y + step * i + size * 0.88, ch, size)
    return y + step * len(s)


def _split_address(addr):
    """住所を2列に分ける（番地の後ろに建物名があれば2列目へ）"""
    for sep in ("　", " "):
        if sep in addr.strip():
            a, b = addr.strip().split(sep, 1)
            return a, b.strip()
    return addr, ""


# ---------- 送付状（A4） ----------
def cover_letter(doc, c, kind, items, ai_note=False, note=""):
    s, k = sender(), DOC_KINDS.get(kind, DOC_KINDS["各種手続き書類の送付"])
    p = doc.new_page(width=595, height=842)
    _text(p, 440, 60, _date_ja(), 10.5)
    _text(p, 60, 90, f"〒{c.get('postal', '')}", 10.5)
    _box(p, (60, 96, 330, 140), c.get("address", ""), 10.5)
    _text(p, 60, 168, f"{c.get('name', '')}　様", 15)
    _box(p, (330, 92, 548, 175), f"{s['agency_name']}\n〒{s['agency_postal']}\n{s['agency_address']}\nTEL {s['agency_tel']}"
         + (f"\n担当：{s['agency_staff']}" if s["agency_staff"] else ""), 9.5)
    _box(p, (60, 200, 535, 230), "書類送付のご案内", 18, align=1)
    _box(p, (60, 250, 535, 420), f"{GREET}\n{k['lead']}\n{CLOSE}" + (f"\n\n{note}" if note else ""), 10.5)
    _box(p, (450, 420, 535, 440), "敬具", 10.5, align=2)
    _box(p, (60, 460, 535, 480), "記", 11, align=1)
    _text(p, 70, 505, "同封書類", 11)
    y = 528
    for it in items:
        p.draw_rect(pymupdf.Rect(80, y - 9, 89, y), color=(0, 0, 0), width=0.6)
        _text(p, 98, y, it, 10.5)
        y += 20
    _box(p, (450, y + 4, 535, y + 24), "以上", 10.5, align=2)
    # 社内用のダブルチェック欄（規程 第2条）
    p.draw_rect(pymupdf.Rect(60, 735, 535, 790), color=(0.5, 0.5, 0.5), width=0.5)
    _text(p, 66, 750, "［社内用］作成者：　　　　　　照合者：　　　　　　宛名・同封物・文面を照合　　発送日：", 8.5, (0.35, 0.35, 0.35))
    if ai_note:
        _box(p, (60, 800, 535, 830), AI_NOTE, 8, align=0)


# ---------- 封筒 ----------
def envelope(doc, c, size="長3"):
    s = sender()
    w, h = (v * MM for v in ENVELOPES[size])
    p = doc.new_page(width=w, height=h)
    postal = "".join(ch for ch in c.get("postal", "") if ch.isdigit())
    if size == "長3":
        # 郵便番号枠（右上、7桁）と縦書きの宛名
        x0, y0 = w - 13 * MM - 7 * 6.7 * MM, 12 * MM
        for i in range(7):
            x = x0 + i * 6.7 * MM + (1.5 * MM if i >= 3 else 0)
            p.draw_rect(pymupdf.Rect(x, y0, x + 5.7 * MM, y0 + 8 * MM), color=(0.85, 0.2, 0.2), width=0.6)
            if i < len(postal):
                _text(p, x + 1.3 * MM, y0 + 6.3 * MM, postal[i], 12)
        a1, a2 = _split_address(c.get("address", ""))
        top, bottom = 32 * MM, h - 20 * MM
        _vertical(p, w - 22 * MM, top, a1, 13, bottom=bottom)
        if a2:
            _vertical(p, w - 31 * MM, top + 8 * MM, a2, 12, bottom=bottom)
        _vertical(p, w / 2, 48 * MM, f"{c.get('name', '')}　様", 22, gap=1.15, bottom=bottom)
        # 裏面：差出人（縦書き、左下）
        b = doc.new_page(width=w, height=h)
        _vertical(b, 32 * MM, h * 0.52, s["agency_address"], 9.5, bottom=h - 18 * MM)
        _vertical(b, 22 * MM, h * 0.52, s["agency_name"], 11, bottom=h - 18 * MM)
        _text(b, 14 * MM, h - 10 * MM, f"〒{s['agency_postal']}　TEL {s['agency_tel']}", 7.5)
    else:
        # 角2：横書き
        _text(p, 30 * MM, 40 * MM, f"〒{c.get('postal', '')}", 14)
        _box(p, (30 * MM, 46 * MM, w - 30 * MM, 80 * MM), c.get("address", ""), 15)
        _box(p, (30 * MM, 100 * MM, w - 30 * MM, 130 * MM), f"{c.get('name', '')}　様", 24, align=1)
        _box(p, (30 * MM, 150 * MM, w - 30 * MM, 165 * MM), "書類在中", 14, align=1)
        _box(p, (w - 110 * MM, h - 45 * MM, w - 15 * MM, h - 12 * MM),
             f"〒{s['agency_postal']}\n{s['agency_address']}\n{s['agency_name']}\nTEL {s['agency_tel']}", 9)


# ---------- 宛名ラベル（A4 12面：86.4×42.3mm） ----------
def labels(doc, cs):
    lw, lh, mx, my, gx = 86.4 * MM, 42.3 * MM, 18.6 * MM, 21.5 * MM, 2.5 * MM
    for i, c in enumerate(cs):
        if i % 12 == 0:
            p = doc.new_page(width=595, height=842)
        r, col = divmod(i % 12, 2)
        x, y = mx + col * (lw + gx), my + r * lh
        _text(p, x + 5 * MM, y + 8 * MM, f"〒{c.get('postal', '')}", 9.5)
        _box(p, (x + 5 * MM, y + 10 * MM, x + lw - 4 * MM, y + 24 * MM), c.get("address", ""), 9.5)
        _box(p, (x + 5 * MM, y + 25 * MM, x + lw - 4 * MM, y + 37 * MM), f"{c.get('name', '')}　様", 13, align=1)


# ---------- 記録（作成 → 照合 → 発送） ----------
def _customers(ids):
    cs = []
    for i in ids:
        c = db.one("SELECT id,name,kana,postal,address FROM customer WHERE id=?", int(i))
        if c:
            cs.append(c)
    return cs


def create(b, staff):
    ids = b.get("customer_ids") or []
    kind = b.get("doc_kind") if b.get("doc_kind") in DOC_KINDS else "各種手続き書類の送付"
    items = [str(x).strip()[:60] for x in (b.get("items") or DOC_KINDS[kind]["items"]) if str(x).strip()]
    env = b.get("envelope") if b.get("envelope") in (*ENVELOPES, "ラベル", "なし") else "長3"
    cs = _customers(ids)
    if not cs:
        return {"error": "宛先の顧客を選んでください"}
    missing = [c["name"] for c in cs if not (c["postal"] and c["address"])]
    if missing:
        return {"error": "郵便番号または住所が未登録の顧客がいます：" + "、".join(missing)}
    out = []
    for c in cs:
        out.append(db.run("INSERT INTO mailing(customer_id,doc_kind,items,body,envelope,created_by) VALUES(?,?,?,?,?,?)",
                          c["id"], kind, json.dumps(items, ensure_ascii=False), str(b.get("note") or "")[:500], env, staff["id"]))
    audit.log("mailing_create", staff=staff, channel="mailing", refs=",".join(f"m{i}" for i in out), result=kind)
    return {"ok": True, "ids": out}


def listing(staff, limit=100):
    return db.rows("""SELECT m.*, c.name AS customer_name, a.name AS created_name, b.name AS checked_name FROM mailing m
                      LEFT JOIN customer c ON c.id=m.customer_id LEFT JOIN staff a ON a.id=m.created_by
                      LEFT JOIN staff b ON b.id=m.checked_by ORDER BY m.id DESC LIMIT ?""", limit)


def check(mid, staff):
    m = db.one("SELECT * FROM mailing WHERE id=?", mid)
    if not m:
        return {"error": "見つかりません"}
    if m["created_by"] == staff["id"]:
        return {"error": "作成者とは別の担当者が照合してください（社内事務規程 第2条）"}
    if m["status"] != "確認待ち":
        return {"error": "確認待ちではありません"}
    db.run("UPDATE mailing SET status='確認済', checked_by=?, checked_at=datetime('now','localtime') WHERE id=?", staff["id"], mid)
    audit.log("mailing_check", staff=staff, channel="mailing", refs=f"m{mid}")
    return {"ok": True}


def mark_sent(mid, staff):
    m = db.one("SELECT * FROM mailing WHERE id=?", mid)
    if not m or m["status"] != "確認済":
        return {"error": "照合（確認済）が済んだものだけ発送済にできます"}
    db.run("UPDATE mailing SET status='発送済' WHERE id=?", mid)
    audit.log("mailing_sent", staff=staff, channel="mailing", refs=f"m{mid}")
    return {"ok": True}


def pdf(ids, what, staff, ai_note=False):
    """what: letter（送付状）／envelope（封筒）／labels（宛名ラベル）"""
    ms = [m for m in (db.one("SELECT * FROM mailing WHERE id=?", int(i)) for i in ids) if m]
    if not ms:
        return None
    doc = pymupdf.open()
    cs = {c["id"]: c for c in _customers([m["customer_id"] for m in ms])}
    if what == "labels":
        labels(doc, [cs[m["customer_id"]] for m in ms if m["customer_id"] in cs])
    for m in ms:
        c = cs.get(m["customer_id"])
        if not c or what == "labels":
            continue
        if what == "letter":
            cover_letter(doc, c, m["doc_kind"], json.loads(m["items"] or "[]"), ai_note, m["body"] or "")
        elif what == "envelope":
            envelope(doc, c, m["envelope"] if m["envelope"] in ENVELOPES else "長3")
    if not len(doc):
        return None
    data = doc.tobytes()
    audit.log("mailing_print", staff=staff, channel="mailing", refs=",".join(f"m{m['id']}" for m in ms), result=what)
    return data
