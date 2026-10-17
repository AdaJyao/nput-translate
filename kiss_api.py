#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kiss-translator 自定义接口兼容层。

规范来源：https://github.com/fishjar/kiss-translator/blob/master/custom-api_v2.md

非聚合翻译（本服务采用的模式）：
  请求 {"text": "hello", "from": "auto", "to": "zh-CN"}
  响应 {"text": "你好", "src": "en"}

聚合翻译（同时支持，便于批量）：
  请求 {"texts": ["hello"], "from": "auto", "to": "zh-CN"}
  响应 [{"text": "你好", "src": "en"}]
  或   {"translations": [{"text": "你好", "src": "en"}]}

语言策略（本机仅装 opus-mt-zh-en）：
  只有「源文是中文 且 目标语言是英文」才真正翻译；
  其他方向原样返回原文，src 标记检测到的语言，不报错。
  这样插件装上即可用，后续加 opus-mt-en-zh 再扩展。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# kiss-translator 标准语言代码（摘自规范文档末尾的语言代码表）
KISS_LANGS = {
    "en": "English", "zh-CN": "简体中文", "zh-TW": "繁體中文", "ar": "Arabic",
    "bg": "Bulgarian", "ca": "Catalan", "hr": "Croatian", "cs": "Czech",
    "da": "Danish", "nl": "Dutch", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "de": "German", "el": "Greek", "hi": "Hindi",
    "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "ms": "Malay", "mt": "Maltese", "nb": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sk": "Slovak", "sl": "Slovenian", "es": "Spanish", "sv": "Swedish",
    "ta": "Tamil", "te": "Telugu", "th": "Thai", "tr": "Turkish",
    "uk": "Ukrainian", "vi": "Vietnamese",
}

# 目标语言归一化：zh-CN / zh-TW / zh 都视作中文
_ZH = {"zh", "zh-CN", "zh-Hans", "zh-TW", "zh-Hant", "zh-cn", "zh-tw"}

# CJK 统一表意文字 + 中文标点，用于判断源文是否中文
_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f\uff00-\uffef]"
)


def detect_lang(text):
    """极简语言检测：中文字符占比 > 20% 判为中文，否则判英文。
    本服务只需区分「中文 / 非中文」，够用且零依赖。"""
    if not text:
        return "en"
    total = len(text)
    cjk = len(_CJK_RE.findall(text))
    return "zh-CN" if (total and cjk / total > 0.2) else "en"


def _norm_target(to):
    """把目标语言代码归一化到 {zh, en, other}。"""
    t = (to or "").strip()
    if t in _ZH:
        return "zh"
    if t.lower().startswith("en"):
        return "en"
    return "other"


def should_translate(text, to):
    """决定是否真正翻译。仅支持 中 -> 英。"""
    if _norm_target(to) != "en":
        return False
    return detect_lang(text) == "zh-CN"


def build_response_nobatch(orig_text, translated_text, src_lang):
    """按规范构造非聚合响应。"""
    return {"text": translated_text, "src": src_lang}


def build_response_batch(pairs):
    """按规范构造聚合响应。pairs = [(译文, src), ...]"""
    return [{"text": t, "src": s} for t, s in pairs]


def build_response_batch_v2(pairs):
    """v2.0.4+ 支持的另一种聚合格式。"""
    return {"translations": [{"text": t, "src": s} for t, s in pairs]}
