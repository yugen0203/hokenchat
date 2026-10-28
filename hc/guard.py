"""入力ガード：LLM に送る前に止める（注意点2(2)・F-03／F-25）。判定はコードだけで行い、AI は使わない"""
import re

SENSITIVE_WORDS = ["病歴", "持病", "既往", "入院", "通院", "手術", "骨折", "診断", "がんの", "癌", "糖尿", "うつ",
                   "精神", "障害者手帳", "けがの", "怪我の", "むちうち", "妊娠", "前科", "犯罪歴", "宗教", "信条"]
# 社内チャットでは「入院保険金」「手術給付金」のような商品の用語として普通に出るので止めない（LINE＝お客さまの入力では止める）
PRODUCT_TERMS = {"入院", "通院", "手術", "骨折", "診断"}
# ponytail: 辞書は安全側に倒した簡易版。「持病があっても入れる？」のような商品相談も止まる。本番は誤検知・見逃しを実データで評価する
_ZEN = str.maketrans("０１２３４５６７８９", "0123456789")
_NUM = re.compile(r"\d[\d\- ]{10,22}\d")


def _luhn(d):
    s = 0
    for i, c in enumerate(reversed(d)):
        n = int(c) * (2 if i % 2 else 1)
        s += n - 9 if n > 9 else n
    return s % 10 == 0


def _mynumber(d):
    p = [int(c) for c in d[:11]][::-1]
    r = sum(n * (i + 2 if i < 6 else i - 4) for i, n in enumerate(p)) % 11
    return int(d[11]) == (0 if r <= 1 else 11 - r)


def number_guard(text):
    """マイナンバー（12桁＋検査数字）・カード番号（13〜19桁＋Luhn）らしき番号があれば理由を返す"""
    for m in _NUM.findall((text or "").translate(_ZEN)):
        d = re.sub(r"\D", "", m)
        if len(d) == 12 and _mynumber(d):
            return "マイナンバーらしき番号"
        if 13 <= len(d) <= 19 and _luhn(d):
            return "カード番号らしき番号"
    return None


def guard(text, internal=False):
    """禁止情報を見つけたら理由を返す。見つからなければ None。internal=True は社内チャット用（商品の用語は通す）"""
    if g := number_guard(text):
        return g
    t = (text or "").translate(_ZEN)
    for w in SENSITIVE_WORDS:
        if w in t and not (internal and w in PRODUCT_TERMS):
            return f"要配慮個人情報の可能性（{w}）"
    return None
