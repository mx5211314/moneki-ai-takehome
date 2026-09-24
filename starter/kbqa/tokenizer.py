"""分词。"""

from __future__ import annotations

import re
import unicodedata

#: 分词规则变了，索引缓存必须失效。
TOKENIZER_VERSION = "tokenizer-3"

#: 中文里几乎不携带信息的字。只用在“查询覆盖率”上，索引照常保留全部词。
STOP_CHARS = frozenset("的了吗呢是在有和与及或就都也还把被给对从向于个些这那哪什么怎样如何多少几请帮我你他它可以能要想会一下少吧啊呀们么样过得着为所")
STOP_WORDS = frozenset("the a an of to in is are and or for on at it this that how what".split())

#: 汉字（含扩展 A 与兼容汉字）。全角 ASCII 已由 normalise 的 NFKC 归一成半角，
#: 这里不把全角标点算进来，避免把 、。 这种标点在二元组里反复出现。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def normalise(text: str) -> str:
    """全角转半角、统一大小写，比较与分词都走这一层。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def tokenize(text: str) -> list[str]:
    """中文按二元组切，英文/数字按空白切。

    中文没有空格，若只按空白切，整句会被当成一个 token，BM25 几乎无法命中
    任何中文文档——这正是检索质量全面崩掉的根因。二元组让“退款”这样的连续两字
    在问句与文档里都能对齐；英文词（poke / salmon）保持整词，别名桥接照常工作。
    """
    norm = normalise(text)
    tokens: list[str] = []
    cjk_run: list[str] = []
    latin: list[str] = []

    def flush_cjk() -> None:
        if not cjk_run:
            return
        if len(cjk_run) == 1:
            tokens.append(cjk_run[0])
        else:
            for i in range(len(cjk_run) - 1):
                tokens.append(cjk_run[i] + cjk_run[i + 1])
        cjk_run.clear()

    def flush_latin() -> None:
        if latin:
            word = "".join(latin)
            if word:
                tokens.append(word)
            latin.clear()

    for ch in norm:
        if ch.isspace():
            flush_cjk()
            flush_latin()
            continue
        if _is_cjk(ch):
            flush_latin()
            cjk_run.append(ch)
        else:
            flush_cjk()
            latin.append(ch)
    flush_cjk()
    flush_latin()
    return tokens


def content_tokens(text: str) -> list[str]:
    """去掉虚词之后的查询词，用来算“这个问题被文档覆盖了多少”。"""
    kept = []
    for token in tokenize(text):
        if token in STOP_WORDS:
            continue
        if len(token) == 1 and all(char in STOP_CHARS for char in token):
            continue
        kept.append(token)
    return kept
