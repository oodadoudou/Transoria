from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from transoria.tools.epub_content import ContentSession
from transoria.tools.epub_editor_tools import run_tool

CONTAINER = """<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"""
OPF = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">test</dc:identifier><dc:title>书</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="css" href="book.css" media-type="text/css"/></manifest><spine toc="ncx"><itemref idref="one"/></spine></package>"""
NAV = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head><body><nav epub:type="toc"><ol><li><a href="one.xhtml">第一章 头发</a></li></ol></nav></body></html>"""
NCX = """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>书</text></docTitle><navMap><navPoint id="one" playOrder="1"><navLabel><text>第一章 头发</text></navLabel><content src="one.xhtml"/></navPoint></navMap></ncx>"""


def _open(tmp_path: Path, body: str, css: str = ".keep { color: red; }", lang: str = "zh-CN") -> tuple[ContentSession, str]:
    chapter = f"""<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}"><head><title>头发</title><style>.inline {{ color: blue; }}</style></head><body>{body}</body></html>"""
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in (("META-INF/container.xml", CONTAINER), ("OEBPS/book.opf", OPF), ("OEBPS/nav.xhtml", NAV),
                           ("OEBPS/toc.ncx", NCX), ("OEBPS/one.xhtml", chapter), ("OEBPS/book.css", css)):
            book.writestr(name, data)
    return ContentSession.open(str(path)), "OEBPS/one.xhtml"


def _preview_and_apply(session: ContentSession, name: str, options: dict | None = None) -> dict:
    preview = run_tool(session, name, dict(options or {}))
    assert not session.dirty
    run_tool(session, name, {**(options or {}), "apply": True, "fingerprint": preview["fingerprint"]})
    return preview


def test_chinese_script_converts_text_attributes_lang_and_navigation(tmp_path: Path):
    session, chapter = _open(tmp_path, '<p>头发<img src="x.png" alt="鼠标"/>里面</p><p lang="ja">髪の毛</p><script>var 头="发";</script>')
    preview = _preview_and_apply(session, "chinese_script", {"conversion": "s2twp"})
    assert preview["count"] > 0 and preview["rows"][0]["diff"]
    text = session._bytes(chapter).decode()
    assert "頭髮" in text and 'alt="滑鼠"' in text and "裡面" in text
    assert 'lang="zh-TW"' in text and "髪の毛" in text and 'var 头="发"' in text
    assert "第一章 頭髮" in session._bytes("OEBPS/nav.xhtml").decode()
    assert "第一章 頭髮" in session._bytes("OEBPS/toc.ncx").decode()
    session.history("undo")
    assert "头发" in session._bytes(chapter).decode()


def test_chinese_script_rejects_unknown_conversion(tmp_path: Path):
    session, _ = _open(tmp_path, "<p>头发</p>")
    with pytest.raises(ValueError):
        run_tool(session, "chinese_script", {"conversion": "s2x"})


def test_chinese_script_apply_requires_matching_conversion(tmp_path: Path):
    session, _ = _open(tmp_path, "<p>头发</p>")
    preview = run_tool(session, "chinese_script", {"conversion": "s2t"})
    with pytest.raises(ValueError, match="preview"):
        run_tool(session, "chinese_script", {"conversion": "s2hk", "apply": True, "fingerprint": preview["fingerprint"]})


def test_clean_spans_unwraps_only_unstyled_spans(tmp_path: Path):
    body = ('<p><span>甲</span><span class="dead">乙</span><span class="keep">丙</span><span class="inline">丁</span>'
            '<span id="a">戊</span><span style="color:red">己</span><span lang="ko">庚</span></p>')
    session, chapter = _open(tmp_path, body)
    preview = _preview_and_apply(session, "clean_spans")
    assert preview["count"] == 2
    text = session._bytes(chapter).decode()
    assert "<p>甲乙<span class=\"keep\">丙</span><span class=\"inline\">丁</span>" in text
    assert '<span id="a">' in text and '<span style="color:red">' in text and '<span lang="ko">' in text


def test_remove_bilingual_drops_marked_original_paragraphs(tmp_path: Path):
    body = '<p style="opacity:0.50;" lang="ko">원문</p>\n<p id="p1">译文</p><p style="margin:0;opacity:0.50;">原文二</p>\n<p>译文二</p>'
    session, chapter = _open(tmp_path, body)
    preview = _preview_and_apply(session, "remove_bilingual")
    assert preview["count"] == 2
    text = session._bytes(chapter).decode()
    assert "원문" not in text and "原文二" not in text and '<p id="p1">译文</p>' in text and "译文二" in text


def test_text_report_flags_chinese_typography(tmp_path: Path):
    session, _ = _open(tmp_path, "<p>他说,我们走吧...好吗?</p><p>他拿起了칼和剑与刀</p><p>彼は東京に行った</p>")
    kinds = [row["kind"] for row in run_tool(session, "text_report", {})["rows"]]
    assert kinds.count("halfwidth_punctuation") == 2
    assert kinds.count("ellipsis") == 1
    assert kinds.count("script_residue") == 1


def test_text_report_flags_dashes_ellipses_and_quote_styles(tmp_path: Path):
    body = ("<p>他停了一下—然后走了。</p><p>他停了一下——然后走了。</p><p>等等…好吗。。。</p>"
            "<p>他说\"走吧\"。</p><p>“走吧，”他说，「好」。</p><p>他说：“走吧……”</p>")
    session, _ = _open(tmp_path, body)
    kinds = [row["kind"] for row in run_tool(session, "text_report", {})["rows"]]
    assert kinds.count("dash") == 1
    assert kinds.count("ellipsis") == 2
    assert kinds.count("quote_style") == 3


def test_swap_bilingual_toggles_original_position(tmp_path: Path):
    body = ('<h1 style="opacity:0.50;">원제</h1>\n<h1>标题</h1>\n<img src="x.png"/>\n'
            '<p style="opacity:0.50;">원문1</p>\n<p>译文1</p>\n<p style="opacity:0.50;">원문2</p>\n<p>译文2</p>')
    session, chapter = _open(tmp_path, body)
    original = session._bytes(chapter).decode()
    preview = _preview_and_apply(session, "swap_bilingual")
    assert preview["count"] == 3
    text = session._bytes(chapter).decode()
    order = [text.index(word) for word in ("标题", "원제", "译文1", "원문1", "译文2", "원문2")]
    assert order == sorted(order)
    again = run_tool(session, "swap_bilingual", {})
    run_tool(session, "swap_bilingual", {"apply": True, "fingerprint": again["fingerprint"]})
    restored = session._bytes(chapter).decode()
    assert restored[restored.index("<html"):] == original[original.index("<html"):]
