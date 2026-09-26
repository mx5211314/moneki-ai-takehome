"""把文档切成检索用的小块。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .loader import Document

#: 切块参数变了，索引缓存必须失效，所以写进缓存键里。
CHUNKER_VERSION = "chunker-5"

CHUNK_SIZE = 300

#: 表格行：以 `|` 开头、以 `|` 结尾。
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
#: 分隔行：去掉首尾 `|` 之后只剩 `-`、`:`、空格（且至少有一个 `-`）。
_TABLE_SEP = re.compile(r"^[\s:\-|]+$")


@dataclass
class Chunk:
    doc_id: str
    chunk_id: str
    text: str
    source_text: str
    heading: str = ""
    kind: str = "text"
    table_header: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source_text": self.source_text,
            "heading": self.heading,
            "kind": self.kind,
            "table_header": self.table_header,
        }


def _is_separator(line: str) -> bool:
    """一行是不是 markdown 表格的分隔行（`| --- | --- |`）。"""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    if "-" not in stripped:
        return False
    return bool(_TABLE_SEP.match(stripped))


def _table_regions(lines: list[str]) -> list[tuple[int, int, list[str]]]:
    """返回 [(start, end_exclusive, header), ...]，每个是一段连续的表格块。

    表格块 = 以表头行开头、紧跟分隔行、之后若干数据行，且每行都形如 `| ... |`。
    """
    regions: list[tuple[int, int, list[str]]] = []
    n = len(lines)
    i = 0
    while i < n:
        if _TABLE_ROW.match(lines[i]) and i + 1 < n and _is_separator(lines[i + 1]):
            header = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
            j = i
            while j < n and _TABLE_ROW.match(lines[j]):
                j += 1
            regions.append((i, j, header))
            i = j
        else:
            i += 1
    return regions


def chunk_document(document: Document) -> list[Chunk]:
    """一篇文档切成检索块：散文按 300 字一块，表格单独成块并带上表头。

    表格块独立出来，units.py 才会把它们建成 `kind="table"` 的单元，
    `DocFacts.render_row` 才能把 `| P06 | 牛肉poke | ✓ | ✓ | — |` 渲染成
    “牛肉poke：含有 麸质、大豆、芝麻。”（KB-040 过敏原对照表就是这么用的）。
    """
    text = document.text
    lines = text.split("\n")
    regions = _table_regions(lines)
    chunks: list[Chunk] = []
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return "%s#%d" % (document.doc_id, seq)

    # 散文区间 = 表格块之间的部分。
    cursor = 0
    for start, end, header in regions:
        prose = "\n".join(lines[cursor:start])
        for piece in _slice_prose(prose, document.title, document.doc_id, next_id):
            chunks.append(piece)
        block = "\n".join(lines[start:end])
        chunks.append(
            Chunk(
                doc_id=document.doc_id,
                chunk_id=next_id(),
                text=block,
                source_text=block,
                heading=document.title,
                kind="table",
                table_header=header,
            )
        )
        cursor = end
    # 尾部散文。
    for piece in _slice_prose("\n".join(lines[cursor:]), document.title, document.doc_id, next_id):
        chunks.append(piece)
    if not chunks:
        piece = text.strip() or document.title
        chunks.append(
            Chunk(
                doc_id=document.doc_id,
                chunk_id=next_id(),
                text=piece,
                source_text=piece,
                heading=document.title,
            )
        )
    return chunks


def _slice_prose(prose: str, title: str, doc_id: str, next_id) -> list[Chunk]:
    """把一段散文切成不超过 CHUNK_SIZE 的块，但**绝不把一行从中间切断**。

    之前按固定 300 字硬切，会把一个 markdown 长行劈成两半（比如“冷冻吞拿鱼块”
    被切成“冷冻吞拿鱼”+“块，拆封后必须当天用完…”），units.py 把后半截当成一个
    独立句子，引用也就被截断，BG 里“毛利率低于 35%”这种关键信息因此丢掉（C07 就挂在这）。
    按行聚合后，每行整行待在一个块里，句子与引用都完整。
    """
    prose = prose.strip("\n")
    if not prose:
        return []
    chunks: list[str] = []
    buf = ""
    for line in prose.split("\n"):
        if buf and len(buf) + len(line) + 1 > CHUNK_SIZE:
            chunks.append(buf)
            buf = line
        elif not buf:
            buf = line
        else:
            buf = buf + "\n" + line
    if buf:
        chunks.append(buf)
    return [
        Chunk(
            doc_id=doc_id,
            chunk_id=next_id(),
            text=piece,
            source_text=piece,
            heading=title,
        )
        for piece in chunks
    ]



def chunk_documents(documents: list[Document]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document))
    return chunks
