"""切块层回归测试：锁死两块切分缺陷的修复。

1. chunker 必须识别 markdown 表格块，带表头建 `kind="table"` 单元，
   否则 `DocFacts.render_row` 渲染不出“含有 麸质、大豆、芝麻”（C02 挂因）。
2. 散文切块绝不能把一行从中间切断（"冷冻吞拿鱼块"→"块，拆封后…"），
   否则"毛利率低于 35%""首月目标 900"随引用被截断（C07/H03 挂因）。
"""

from __future__ import annotations

from pathlib import Path

from kbqa.chunker import CHUNK_SIZE, chunk_document
from kbqa.docfacts import DocFacts
from kbqa.loader import Document


def _doc(text: str) -> Document:
    return Document(
        doc_id="KB-TEST",
        title="测试文档",
        text=text,
        path=Path("KB-TEST.md"),
        fmt="md",
    )


def test_markdown_table_becomes_table_chunk_with_header():
    text = (
        "# 过敏原对照表\n\n"
        "维护部门：总部品控部\n\n"
        "| 商品编号 | 商品名称 | 麸质 | 大豆 | 芝麻 |\n"
        "|---|---|---|---|---|\n"
        "| P06 | 牛肉poke | ✓ | ✓ | — |\n"
        "| P02 | 味增拉面 | ✓ | — | — |\n"
    )
    chunks = chunk_document(_doc(text))
    table_chunks = [c for c in chunks if c.kind == "table"]
    assert table_chunks, "markdown 表格应被识别成 kind=table 的块"
    assert table_chunks[0].table_header == ["商品编号", "商品名称", "麸质", "大豆", "芝麻"]
    # 表头行与分隔行不应单独成块内容。
    for chunk in table_chunks:
        lines = [ln.strip() for ln in chunk.text.splitlines() if ln.strip()]
        assert lines[0].startswith("|"), "表格块应以表头行开头"


def test_render_row_lists_allergens_without_markers():
    # render_row 不依赖索引，直接构造即可。芝麻这一列是 ✓，应被列入。
    df = DocFacts(None)
    header = ["商品编号", "商品名称", "麸质", "大豆", "芝麻"]
    out = df.render_row(header, "| P06 | 牛肉poke | ✓ | ✓ | ✓ |")
    assert "牛肉poke" in out
    assert "麸质" in out and "大豆" in out and "芝麻" in out
    assert "✓" not in out, "勾选标记不应进答案"


def test_long_line_is_not_split_mid_word():
    # 构造一个超过 CHUNK_SIZE 的长行，确认它作为完整一行存在，
    # 不会被从中间劈断（否则引用会带半截词，如“块，拆封后…”）。
    long_line = (
        "林知夏汇报了吞拿鱼三明治的情况。这个商品在 S04 已经连续两个月"
        "毛利率低于 35%，原料是冷冻吞拿鱼块，" + "拆封后必须当天用完，" * 40
    )
    assert len(long_line) > CHUNK_SIZE
    text = "# 标题\n\n" + long_line + "\n\n第二行是短句。"
    chunks = chunk_document(_doc(text))
    prose = "\n".join(c.text for c in chunks if c.kind != "table")
    assert any(line == long_line for line in prose.split("\n")), (
        "超过 300 字的长行不应被从中间切断，否则引用会丢失“毛利率 35%”等信息"
    )


def test_table_and_prose_do_not_overlap():
    text = (
        "前文一句。\n\n"
        "| 商品编号 | 商品名称 | 麸质 |\n"
        "|---|---|---|\n"
        "| P06 | 牛肉poke | ✓ |\n\n"
        "后文一句。"
    )
    chunks = chunk_document(_doc(text))
    table_blocks = [c for c in chunks if c.kind == "table"]
    prose_blocks = [c for c in chunks if c.kind != "table"]
    assert table_blocks and prose_blocks
    joined_prose = "\n".join(c.text for c in prose_blocks)
    # 表格行不应出现在散文块里，避免重复建 unit。
    assert "| P06 | 牛肉poke" not in joined_prose
