#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文本语言识别与模型自动路由。

设计目标：不下载大而慢的多语种模型，而是**按源语言自动挑合适的双语模型**，
缺直达模型时用英文中转（例如 zh→de = zh→en 再 en→de）。

对外接口：
    detect_lang(text)            -> 'zh' / 'ja' / 'ko' / 'ru' / 'en' / 'de' / 'fr' / ...
    build_index(model_metas)     -> {(src, dst): model_path}
    plan(index, text, to)        -> [model_path, ...]  1 段直达，或 2 段中转
"""
import os
import re

# ---------------------------------------------------------------- 文字体系
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_GREEK = re.compile(r"[\u0370-\u03ff]")
_ARABIC = re.compile(r"[\u0600-\u06ff]")
_THAI = re.compile(r"[\u0e00-\u0e7f]")
_LATIN_WORD = re.compile(r"[a-zA-ZÀ-ÿ']+")

# 拉丁字母语言用停用词打分（够用且零依赖）
_STOP = {
    "en": set("the and is are was were you to of in that it for on with this be have "
              "not but they from at as by an or we can will".split()),
    "de": set("der die das und ist nicht ein eine einen ich sie wir mit auf für von "
              "zu den dem des sich auch es er hat haben wird".split()),
    "fr": set("le la les et est pas une des du que qui pour avec dans sur ne se ce "
              "sont mais ou donc il elle nous vous".split()),
    "es": set("el la los las y es que una de por con para no se del al lo como más "
              "pero sus le ha o".split()),
    "it": set("il lo la gli le e che una per con non si del della sono come più ma "
              "anche questo".split()),
    "pt": set("o a os as e que uma de por com para não se do da em um mais como "
              "mas seu sua".split()),
    "nl": set("de het een en van is dat niet op te met voor zijn er maar ook aan".split()),
}

# 变音符号线索
_DIACRITIC_HINT = [
    ("de", "ßäöü"),
    ("fr", "àâçèéêëîïôùûœ"),
    ("es", "ñáíóúü"),
    ("pt", "ãõçáâêô"),
    ("it", "àèéìòù"),
]

_CODE_ALIAS = {
    "zh-cn": "zh", "zh-hans": "zh", "zh-tw": "zh", "zh-hant": "zh", "zh": "zh",
    "cmn": "zh", "jpn": "ja", "ja": "ja", "jp": "ja",
    "kor": "ko", "ko": "ko",
    "eng": "en", "en": "en",
    "deu": "de", "ger": "de", "de": "de",
    "fra": "fr", "fre": "fr", "fr": "fr",
    "rus": "ru", "ru": "ru",
    "spa": "es", "es": "es",
    "ita": "it", "it": "it",
    "por": "pt", "pt": "pt",
    "nld": "nl", "nl": "nl",
}


def norm_code(code):
    """把各种语言写法归一化成两位小写代码。"""
    c = (code or "").strip().lower().replace("_", "-")
    if c in _CODE_ALIAS:
        return _CODE_ALIAS[c]
    if "-" in c:
        head = c.split("-")[0]
        if head in _CODE_ALIAS:
            return _CODE_ALIAS[head]
    return c


def detect_lang(text):
    """识别文本主要语言。无法判断时返回 'en'。"""
    if not text or not text.strip():
        return "en"
    t = text.strip()

    if _KANA.search(t):
        return "ja"          # 有假名 → 日文（优先于汉字）
    if _HANGUL.search(t):
        return "ko"
    if _CYRILLIC.search(t):
        return "ru"
    if _GREEK.search(t):
        return "el"
    if _ARABIC.search(t):
        return "ar"
    if _THAI.search(t):
        return "th"

    cjk = len(_CJK.findall(t))
    total = len(re.sub(r"\s", "", t)) or 1
    if cjk / total > 0.15:
        return "zh"

    # 拉丁字母：停用词 + 变音符号打分
    low = t.lower()
    words = _LATIN_WORD.findall(low)
    if not words:
        return "en"

    scores = {}
    wset = set(words)
    for lang, stops in _STOP.items():
        hit = len(wset & stops)
        if hit:
            scores[lang] = scores.get(lang, 0) + hit * 3

    for lang, chars in _DIACRITIC_HINT:
        if any(ch in low for ch in chars):
            scores[lang] = scores.get(lang, 0) + 2

    if not scores:
        return "en"
    best = max(scores.items(), key=lambda kv: kv[1])
    # 分数太低就按英文处理
    return best[0] if best[1] >= 3 else "en"


# ---------------------------------------------------------------- 模型路由
def build_index(model_metas):
    """model_metas: [{'path':..., 'src':..., 'dst':...}] -> {(src,dst): path}"""
    idx = {}
    for m in model_metas:
        src = norm_code(m.get("src"))
        dst = norm_code(m.get("dst"))
        if not src or not dst:
            continue
        # 同一对语言留第一个
        idx.setdefault((src, dst), m["path"])
    return idx


def plan(index, text, to, allow_chain=True):
    """规划翻译路径。

    返回 (模型路径列表, 说明文字)。空列表表示没有可用模型。
    说明文字用于界面/日志展示，如 "zh→en→de（中转）"。
    """
    dst = norm_code(to)
    if not dst:
        return [], "目标语言为空"

    src = detect_lang(text)
    if src == dst:
        return [], f"源语言与目标语言相同（{src}）"

    # 1) 直达
    if (src, dst) in index:
        return [index[(src, dst)]], f"{src}→{dst}"

    # 2) 经英文中转
    if allow_chain and dst != "en" and src != "en":
        a = index.get((src, "en"))
        b = index.get(("en", dst))
        if a and b:
            return [a, b], f"{src}→en→{dst}（中转）"

    # 3) 再从已装模型里找「任意 → dst」的直接模型（源语言识别可能不准）
    for (s, d), p in index.items():
        if d == dst:
            return [p], f"{s}→{dst}（按已装模型）"

    available = ", ".join(sorted(f"{s}->{d}" for s, d in index)) or "无"
    return [], f"没有 {src}→{dst} 的模型（已装: {available}）"


def list_installed_pairs(model_metas):
    """已安装模型支持的语言对，供界面显示。"""
    out = []
    for m in model_metas:
        src = norm_code(m.get("src")) or "?"
        dst = norm_code(m.get("dst")) or "?"
        out.append(f"{src}→{dst}")
    return sorted(set(out))
