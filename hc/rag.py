"""登録文書（RAG の対象）と社内チャット

版管理（D-03）: 文書は「版」ごとに登録する。登録した版は「承認待ち」になり、管理者が承認すると検索の対象になる。
                同じ文書名の古い版は、新しい版の承認と同時に「旧版」になり、検索から外れる（注意点5(1)）
フォルダ監視（D-04）: data/inbox/ に置いたファイルを定期的に見て、新しいもの・中身が変わったものを「承認待ち」で取り込む
社内チャット: 検索 → LLM は「どの行が関係するか」の番号を選ぶだけ → 表示はコードが原文から取り出す（逆の結論を出さない）
"""
import hashlib, json, math, re, shutil, subprocess, sys, threading, time
from pathlib import Path
from . import db, llm, audit
from .guard import guard, number_guard
from .config import SEED_DOCS, UPLOADS, INBOX, TOOLS, MIN_SCORE, TOP_K

TEXT_EXT, PDF_EXT, IMG_EXT = {".txt", ".md"}, {".pdf"}, {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp"}
ARTICLE = re.compile(r"^\s*(第[0-9０-９一二三四五六七八九十百]+条(?:の[0-9０-９一二三四五六七八九十]+)?\s*[（(][^）)]{1,40}[）)])", re.M)
STATUS = {"pending": "承認待ち", "active": "検索対象", "retired": "旧版", "rejected": "差し戻し"}


def read_md(f):
    head, body = Path(f).read_text(encoding="utf-8").split("---", 2)[1:]
    meta = dict(l.split(": ", 1) for l in head.strip().splitlines() if ": " in l)
    return meta, [(a.strip(), t.strip()) for a, t, *_ in (p.split("\n", 1) + [""] for p in re.split(r"^## ", body, flags=re.M)[1:]) if t.strip()]


# ---------- 文字の取り出し・分割 ----------
def ocr(path):
    """OS 標準の文字認識（端末内で処理し、外部には送らない）。Mac＝Vision、Windows＝Windows.Media.Ocr"""
    if sys.platform == "darwin":
        exe = TOOLS / "ocr"
        if not exe.exists():  # 初回だけビルドする（Xcode のコマンドラインツールが必要）
            subprocess.run(["swiftc", "-O", str(TOOLS / "ocr.swift"), "-o", str(exe)], check=True, timeout=600)
        cmd = [str(exe), str(path)]
    elif sys.platform == "win32":
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / "ocr.ps1"), str(path)]
    else:
        raise RuntimeError("この OS では画像の文字認識に対応していません（Mac と Windows のみ）")
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode:
        raise RuntimeError("文字認識に失敗しました：" + r.stderr.decode("utf-8", "replace")[-300:])
    t = r.stdout.decode("utf-8", "replace")
    return re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", t)  # Windows の OCR は日本語の間に空白を入れるので詰める


def extract(path):
    """ファイルから文字を取り出す。PDF はページを \\f で区切って返す"""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        t = path.read_text(encoding="utf-8", errors="replace")
        return (t.split("---", 2)[2] if t.startswith("---") and t.count("---") >= 2 else t), "テキスト"
    if ext in IMG_EXT:
        return ocr(path), "画像（文字認識）"
    import pymupdf
    with pymupdf.open(path) as pdf:
        pages = [p.get_text() for p in pdf]
        if sum(len(p.strip()) for p in pages) >= 40 * max(len(pages), 1):
            return "\f".join(pages), "PDF"
        tmp = UPLOADS / (path.stem + "_pages")  # 文字がほとんど取れない＝スキャンした冊子。ページを画像にして文字認識
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            out = []
            for i, p in enumerate(pdf):
                img = tmp / f"p{i:04d}.png"
                p.get_pixmap(dpi=200).save(img)
                out.append(ocr(img))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return "\f".join(out), "PDF（スキャン・文字認識）"


def split(text):
    """「第○条（見出し）」があれば条ごと、なければページ・約600字ごとに分ける"""
    if len(ARTICLE.findall(text)) >= 2:
        parts = ARTICLE.split(text)
        return [(parts[i].strip(), re.sub(r"[ \t]+", " ", parts[i + 1]).strip()) for i in range(1, len(parts) - 1, 2)]
    out = []
    for n, page in enumerate(text.split("\f"), 1):
        buf, k = "", 1
        for para in re.split(r"\n\s*\n", page):
            para = re.sub(r"[ \t]+", " ", para).strip()
            if buf and len(buf) + len(para) > 600:
                out.append((f"p.{n}-{k}", buf)); buf, k = "", k + 1
            buf = (buf + "\n" + para).strip()
        if buf:
            out.append((f"p.{n}-{k}" if k > 1 else f"p.{n}", buf))
    return out


# ---------- 版の登録・承認 ----------
def _bump():
    db.set_setting("rag_gen", time.time())


def _store_chunks(con, vid, title, sections):
    for i in range(0, len(sections), 32):  # 埋め込みは32件ずつ
        batch = sections[i:i + 32]
        for (a, t), v in zip(batch, llm.embed([f"{title} {a} {t}" for a, t in batch])):
            con.execute("INSERT INTO chunk(version_id,article,body,vec) VALUES(?,?,?,?)", (vid, a, t, json.dumps(v)))


def _doc_id(con, title, insurer):
    r = con.execute("SELECT id FROM document WHERE title=?", (title,)).fetchone()
    if r:
        con.execute("UPDATE document SET insurer=? WHERE id=?", (insurer, r[0]))
        return r[0]
    return con.execute("INSERT INTO document(title,insurer) VALUES(?,?)", (title, insurer)).lastrowid


def seed():
    """初回：data/docs/*.md（架空の約款・規程）を承認済みで取り込む。旧版（第0.1版）の chunks テーブルは捨てる"""
    con = db.connect()
    con.execute("DROP TABLE IF EXISTS chunks")
    if con.execute("SELECT count(*) FROM document_version").fetchone()[0]:
        con.close()
        return 0
    n = 0
    for f in sorted(SEED_DOCS.glob("*.md")):
        meta, arts = read_md(f)
        insurer = meta.get("insurer", "社内" if "社内" in meta["title"] else "損保ジャパン（架空）")
        did = _doc_id(con, meta["title"], insurer)
        vid = con.execute("""INSERT INTO document_version(document_id,title,insurer,version,revised,source,status,file_hash,approved_at)
                             VALUES(?,?,?,?,?,?, 'active', ?, datetime('now','localtime'))""",
                          (did, meta["title"], insurer, meta.get("version", ""), meta.get("revised", ""), "初期登録（テキスト）",
                           hashlib.sha256(f.read_bytes()).hexdigest())).lastrowid
        _store_chunks(con, vid, meta["title"], arts)
        n += len(arts)
    con.commit()
    con.close()
    _bump()
    print("初期文書の取り込み:", n, "件")
    return n


def register(name, data, title, version, insurer, staff=None, via="画面から登録"):
    """文書を「承認待ち」で登録する（検索の対象になるのは承認後）"""
    ext = Path(name).suffix.lower()
    if ext not in TEXT_EXT | PDF_EXT | IMG_EXT:
        return {"error": f"この形式（{ext or '拡張子なし'}）には対応していません。PDF・テキスト・画像を登録できます"}
    title = (title or Path(name).stem).strip()[:100]
    version = (version or "版の記載なし").strip()[:50]
    insurer = (insurer or "未設定").strip()[:50]
    fh = hashlib.sha256(data).hexdigest()
    if db.one("SELECT 1 FROM document_version WHERE file_hash=? AND status IN ('pending','active')", fh):
        return {"error": "同じ内容のファイルがすでに登録されています（承認待ちまたは検索対象）"}
    UPLOADS.mkdir(parents=True, exist_ok=True)
    orig = UPLOADS / f"{fh[:16]}{ext}"
    orig.write_bytes(data)
    try:
        text, kind = extract(orig)
    except Exception as e:
        orig.unlink(missing_ok=True)
        return {"error": f"文字を取り出せませんでした：{e}"}
    if g := number_guard(text):
        orig.unlink(missing_ok=True)
        return {"error": f"登録を止めました：{g}が含まれています。お客さまの情報を含む資料は登録できません"}
    sections = split(text)
    if not sections:
        orig.unlink(missing_ok=True)
        return {"error": "文字を読み取れませんでした"}
    con = db.connect()
    vid = con.execute("""INSERT INTO document_version(title,insurer,version,revised,source,status,file_hash,orig,uploaded_by)
                         VALUES(?,?,?,?,?, 'pending', ?,?,?)""",
                      (title, insurer, version, time.strftime("%Y-%m-%d"), f"{kind}（{name}）・{via}", fh, orig.name,
                       (staff or {}).get("id"))).lastrowid
    try:
        _store_chunks(con, vid, title, sections)
    except Exception:
        con.rollback()
        con.close()
        orig.unlink(missing_ok=True)
        raise
    con.commit()
    con.close()
    audit.log("doc_register", staff=staff, channel="docs", result="pending", refs=f"v{vid}")
    audit.notify("文書の承認待ちがあります", f"「{title}」{version}（{via}）", roles="admin", key=f"docpending:{vid}")
    return {"ok": True, "id": vid, "title": title, "kind": kind, "sections": len(sections), "chars": len(text), "status": "pending"}


def approve(vid, staff):
    con = db.connect()
    v = con.execute("SELECT * FROM document_version WHERE id=?", (vid,)).fetchone()
    if not v or v["status"] != "pending":
        con.close()
        return {"error": "承認待ちの版ではありません"}
    did = _doc_id(con, v["title"], v["insurer"])
    old = [r[0] for r in con.execute("SELECT id FROM document_version WHERE document_id=? AND status='active'", (did,))]
    con.execute("UPDATE document_version SET status='retired' WHERE document_id=? AND status='active'", (did,))
    con.execute("UPDATE document_version SET status='active', document_id=?, approved_by=?, approved_at=datetime('now','localtime') WHERE id=?",
                (did, staff["id"], vid))
    con.commit()
    con.close()
    _bump()
    audit.log("doc_approve", staff=staff, channel="docs", result="active", refs=f"v{vid}" + (f" 旧版:{old}" if old else ""))
    return {"ok": True, "retired": old}


def reject(vid, staff):
    v = db.one("SELECT * FROM document_version WHERE id=?", vid)
    if not v or v["status"] != "pending":
        return {"error": "承認待ちの版ではありません"}
    db.run("DELETE FROM chunk WHERE version_id=?", vid)
    db.run("UPDATE document_version SET status='rejected' WHERE id=?", vid)
    if v["orig"]:
        (UPLOADS / v["orig"]).unlink(missing_ok=True)
    audit.log("doc_reject", staff=staff, channel="docs", refs=f"v{vid}")
    return {"ok": True}


def edit_pending(vid, b, staff):
    v = db.one("SELECT * FROM document_version WHERE id=?", vid)
    if not v or v["status"] != "pending":
        return {"error": "承認待ちの版だけ修正できます"}
    db.run("UPDATE document_version SET title=?, version=?, insurer=? WHERE id=?",
           (b.get("title") or v["title"]).strip()[:100], (b.get("version") or v["version"]).strip()[:50],
           (b.get("insurer") or v["insurer"]).strip()[:50], vid)
    return {"ok": True}


def remove_document(vid, staff):
    """検索の対象から外す（その文書のすべての版を旧版にし、索引を消す）"""
    v = db.one("SELECT * FROM document_version WHERE id=?", vid)
    if not v:
        return {"error": "見つかりません"}
    ids = [r["id"] for r in db.rows("SELECT id FROM document_version WHERE document_id=?", v["document_id"])] if v["document_id"] else [vid]
    for i in ids:
        db.run("DELETE FROM chunk WHERE version_id=?", i)
        db.run("UPDATE document_version SET status=CASE status WHEN 'pending' THEN 'rejected' ELSE 'retired' END WHERE id=?", i)
    _bump()
    audit.log("doc_remove", staff=staff, channel="docs", refs=f"v{ids}")
    return {"ok": True}


def versions():
    rs = db.rows("""SELECT v.*, (SELECT count(*) FROM chunk c WHERE c.version_id=v.id) AS articles,
                    u.name AS uploaded_name, a.name AS approved_name
                    FROM document_version v LEFT JOIN staff u ON u.id=v.uploaded_by LEFT JOIN staff a ON a.id=v.approved_by
                    ORDER BY CASE v.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 WHEN 'retired' THEN 2 ELSE 3 END, v.title, v.id DESC""")
    for r in rs:
        r["status_name"] = STATUS.get(r["status"], r["status"])
    return rs


def preview(vid):
    return db.rows("SELECT article, body FROM chunk WHERE version_id=? ORDER BY id LIMIT 200", vid)


# ---------- フォルダ監視（D-04） ----------
_scan_lock = threading.Lock()


def scan_inbox():
    """data/inbox/ の新しいファイル・変わったファイルを承認待ちで取り込む。ファイル名「文書名__版.pdf」なら版も読む"""
    if not _scan_lock.acquire(blocking=False):
        return 0
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(INBOX.iterdir()):
            if not f.is_file() or f.name.startswith(".") or f.suffix.lower() not in TEXT_EXT | PDF_EXT | IMG_EXT:
                continue
            data = f.read_bytes()
            fh = hashlib.sha256(data).hexdigest()
            if db.setting("inbox:" + fh) or db.one("SELECT 1 FROM document_version WHERE file_hash=?", fh):
                continue
            title, _, version = f.stem.partition("__")
            try:
                r = register(f.name, data, title, version, "未設定", None, via="フォルダ監視")
            except llm.AIStopped:
                return n
            except Exception as e:
                r = {"error": str(e)}
            db.set_setting("inbox:" + fh, "done" if r.get("ok") else "error")
            if r.get("error"):
                audit.notify("フォルダ監視：取り込めないファイルがあります", f"{f.name}：{r['error']}", level="warn", key=f"inboxerr:{fh}")
            n += bool(r.get("ok"))
        return n
    finally:
        _scan_lock.release()


# ---------- 検索 ----------
_cache = {"gen": None, "rows": []}


def _index():
    gen = db.setting("rag_gen")
    if _cache["gen"] != gen:
        rows = []
        for r in db.rows("""SELECT c.article, c.body, c.vec, v.title, v.version, v.insurer FROM chunk c
                            JOIN document_version v ON v.id=c.version_id WHERE v.status='active'"""):
            vec = json.loads(r["vec"])
            n = math.sqrt(sum(x * x for x in vec)) or 1
            rows.append((r, [x / n for x in vec]))
        _cache.update(gen=gen, rows=rows)
    return _cache["rows"]


def search(q):
    # ponytail: 全件の総当たり。数千条を超えたら pgvector 等の索引に移す
    qv = llm.embed([q])[0]
    n = math.sqrt(sum(x * x for x in qv)) or 1
    hits = sorted(({"doc": r["title"], "version": r["version"], "insurer": r["insurer"], "article": r["article"], "text": r["body"],
                    "score": round(sum(a * b for a, b in zip(qv, v)) / n, 3)} for r, v in _index()), key=lambda h: -h["score"])
    return [h for h in hits[:TOP_K] if h["score"] >= MIN_SCORE]


RAG_SYSTEM = """あなたは保険代理店の社内向け検索アシスタントです。
【資料】は行ごとに [資料番号-行番号] が付いています。【質問】に答えるために読むべき行を、関係が強い順に1〜3個選んでください。
- 出力は番号だけをカンマ区切りで書く（例：2-6,1-3）。説明・結論・判断は書かない
- 例外（「ただし」）の行が関係する場合は必ず含める"""
JUDGE_WORDS = ["支払われ", "払われ", "出ますか", "補償され", "対象にな", "対象です", "有無責", "引受", "引き受け", "免責"]
NOTICE = "※これは該当しうる条項の候補で、支払可否・引受の判断ではありません。判断は原文を確認し、保険会社へ照会してください。"


def quick(q, staff=None):
    """検索だけ（約1秒）。AIの絞り込みを待つ間に候補の条文を先に見せる"""
    g = guard(q, internal=True)
    return {"blocked": g} if g else {"sources": search(q)}


def internal(q, staff=None):
    t0 = time.time()
    g = guard(q, internal=True)
    if g:
        audit.log("chat", staff=staff, channel="internal", result="blocked", guard=g, t0=t0)
        return {"blocked": g, "answer": f"送信を止めました：{g}。社内AIにも入力できない情報です（社内事務規程 第4条）。"}
    hits = search(q)
    if not hits:
        audit.log("chat", staff=staff, channel="internal", result="no_hit", t0=t0)
        return {"answer": "該当なし（登録済みの約款・規程に根拠が見つかりませんでした）", "sources": []}
    lines = [h["text"].splitlines() for h in hits]
    ctx = "\n\n".join(f"［資料{i}］{h['doc']} {h['article']}\n" + "\n".join(f"[{i}-{j}] {l}" for j, l in enumerate(ls, 1))
                      for i, (h, ls) in enumerate(zip(hits, lines), 1))
    out = llm.chat(RAG_SYSTEM, f"【資料】\n{ctx}\n\n【質問】\n{q}", max_tokens=24)
    quotes, seen = [], set()
    for n, j in [tuple(map(int, m)) for m in re.findall(r"(\d+)\s*-\s*(\d+)", out)][:3]:
        if 1 <= n <= len(hits) and 1 <= j <= len(lines[n - 1]) and (n, j) not in seen:  # 存在しない番号は捨てる
            seen.add((n, j))
            h, ls = hits[n - 1], lines[n - 1]
            # 小さいモデルは隣の号を選ぶことがある（T03）。前後の行も添えて、人が確かめやすくする
            quotes.append({"doc": h["doc"], "version": h["version"], "article": h["article"], "line": ls[j - 1],
                           "before": ls[j - 2] if j >= 2 else "", "after": ls[j] if j < len(ls) else ""})
    ans = ("該当しうる条文：\n" + "\n".join(f"・{x['doc']}（{x['version']}）{x['article']}\n　「{x['line']}」" for x in quotes)) \
        if quotes else "該当なし（AIが根拠の文を特定できませんでした。下の候補の条文を直接確認してください）"
    notice = NOTICE if any(w in q for w in JUDGE_WORDS) else ""
    audit.log("chat", staff=staff, channel="internal", result="answered",
              refs=",".join(h["doc"][:2] + h["article"].split("（")[0] for h in hits), t0=t0)
    return {"answer": ans, "quotes": quotes, "notice": notice, "sources": hits}
