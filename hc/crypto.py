"""暗号化（N-03／N-12）。申込書控え・LINE の添付は、ファイルごとに別の鍵（AES-256-GCM）で暗号化する

  鍵の階層: マスター鍵（data/keys/master.key）→ ファイルごとの鍵（data/keys/keys.db に、マスター鍵で包んで保存）
  消去:     ファイルごとの鍵を消す（暗号化消去）。これでバックアップ内の写しも即時に読めなくなる
            ※ data/keys/ はバックアップに含めない運用にする（含めると暗号化消去が効かない）
"""
import os, secrets, sqlite3, stat, threading
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .config import KEYS

_lock = threading.Lock()


def _master():
    KEYS.mkdir(parents=True, exist_ok=True)
    f = KEYS / "master.key"
    if not f.exists():
        f.write_bytes(AESGCM.generate_key(bit_length=256))
        try:
            os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)  # 600
        except OSError:
            pass
    return f.read_bytes()


def _keys():
    KEYS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(KEYS / "keys.db", timeout=15)
    con.execute("CREATE TABLE IF NOT EXISTS key_store(ref TEXT PRIMARY KEY, wrapped BLOB)")
    return con


def encrypt_file(data: bytes, folder: Path) -> str:
    """暗号化して保存し、参照ID（ref）を返す"""
    ref = secrets.token_hex(16)
    key = AESGCM.generate_key(bit_length=256)
    n1, n2 = os.urandom(12), os.urandom(12)
    wrapped = n1 + AESGCM(_master()).encrypt(n1, key, ref.encode())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ref}.bin").write_bytes(n2 + AESGCM(key).encrypt(n2, data, ref.encode()))
    with _lock:
        con = _keys()
        con.execute("INSERT INTO key_store VALUES(?,?)", (ref, wrapped))
        con.commit()
        con.close()
    return ref


def decrypt_file(ref: str, folder: Path) -> bytes:
    con = _keys()
    r = con.execute("SELECT wrapped FROM key_store WHERE ref=?", (ref,)).fetchone()
    con.close()
    if not r:
        raise FileNotFoundError("鍵がありません（消去済み）")
    w = r[0]
    key = AESGCM(_master()).decrypt(w[:12], w[12:], ref.encode())
    blob = (folder / f"{ref}.bin").read_bytes()
    return AESGCM(key).decrypt(blob[:12], blob[12:], ref.encode())


def erase(ref: str, folder: Path) -> bool:
    """暗号化消去：鍵を消してからファイルを消す。両方なくなったことを確かめて True を返す（残存チェック）"""
    with _lock:
        con = _keys()
        con.execute("DELETE FROM key_store WHERE ref=?", (ref,))
        con.commit()
        con.execute("VACUUM")  # 消した鍵の断片を DB ファイルに残さない
        left = con.execute("SELECT 1 FROM key_store WHERE ref=?", (ref,)).fetchone()
        con.close()
    f = folder / f"{ref}.bin"
    if f.exists():
        f.unlink()
    return not left and not f.exists()


def has_key(ref: str) -> bool:
    con = _keys()
    r = con.execute("SELECT 1 FROM key_store WHERE ref=?", (ref,)).fetchone()
    con.close()
    return bool(r)
