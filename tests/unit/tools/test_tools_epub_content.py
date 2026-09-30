from __future__ import annotations

import base64
import hashlib
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from lxml import etree

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.epub_content import register
from transoria.tools import epub_content as epub_content_module
from transoria.tools.epub_content import ContentSession, ContentSessionStore
from transoria.tools.epub_editor_tools import cleanup_css, compare, image_report, issues, run_tool, set_cover, text_report, upgrade_epub
from transoria.tools import epub_editor_tools


def _book(path: Path) -> None:
    container = """<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"""
    opf = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">test</dc:identifier><dc:title>Book</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="one" href="Text/one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="Text/two.xhtml" media-type="application/xhtml+xml"/><item id="css" href="Styles/book.css" media-type="text/css"/><item id="img" href="Images/pixel.png" media-type="image/png"/></manifest><spine toc="ncx"><itemref idref="one"/><itemref idref="two"/></spine></package>"""
    nav = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="Text/one.xhtml">One</a></li><li><a href="Text/two.xhtml">Two</a></li></ol></nav><nav epub:type="landmarks"><ol><li><a href="Text/one.xhtml">Start</a></li></ol></nav></body></html>"""
    ncx = """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>Book</text></docTitle><navMap><navPoint id="one" playOrder="1"><navLabel><text>One</text></navLabel><content src="Text/one.xhtml"/></navPoint><navPoint id="two" playOrder="2"><navLabel><text>Two</text></navLabel><content src="Text/two.xhtml"/></navPoint></navMap></ncx>"""
    one = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="../Styles/book.css"/></head><body><p id="start">Hello world.</p><img src="../Images/pixel.png"/><script>alert(1)</script><a href="https://example.com">Outside</a></body></html>"""
    two = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>Hello again.</p></body></html>"""
    with zipfile.ZipFile(path, "w") as book:
        book.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        for name, data in (
            ("META-INF/container.xml", container),
            ("OEBPS/book.opf", opf),
            ("OEBPS/nav.xhtml", nav),
            ("OEBPS/toc.ncx", ncx),
            ("OEBPS/Text/one.xhtml", one),
            ("OEBPS/Text/two.xhtml", two),
            ("OEBPS/Styles/book.css", "body { color: red; }"),
            ("OEBPS/Images/pixel.png", b"image-bytes"),
        ):
            book.writestr(name, data)


def _rewrite_book(path: Path, changes: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path) as book:
        entries = [(info, book.read(info.filename)) for info in book.infolist()]
    with zipfile.ZipFile(path, "w") as book:
        for info, data in entries:
            book.writestr(info, changes.pop(info.filename, data))
        for name, data in changes.items():
            book.writestr(name, data)


def test_saved_search_sequence_is_ordered_atomic_and_undoable(tmp_path: Path):
    source = tmp_path / "sequence.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    before = session._bytes(path)
    rules = [{"query": "Hello", "replacement": "Welcome", "paths": [path]},
             {"query": "Welcome", "replacement": "Greetings", "paths": [path]}]
    preview = run_tool(session, "replace_sequence", {"rules": rules})
    assert preview["replacements"] == 2
    assert session._bytes(path) == before and not session.dirty
    run_tool(session, "replace_sequence", {"rules": rules, "apply": True, "fingerprint": preview["fingerprint"]})
    assert b"Greetings" in session._bytes(path)
    session.history("undo")
    assert session._bytes(path) == before
    session.history("redo")
    assert b"Greetings" in session._bytes(path)


def test_saved_search_sequence_rejects_stale_options_and_failed_later_rule(tmp_path: Path):
    source = tmp_path / "sequence.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    before = session._bytes(path)
    rules = [{"query": "Hello", "replacement": "Welcome", "paths": [path]}]
    preview = run_tool(session, "replace_sequence", {"rules": rules})
    rules[0]["replacement"] = "Different"
    with pytest.raises(ValueError, match="draft changed"):
        run_tool(session, "replace_sequence", {"rules": rules, "apply": True, "fingerprint": preview["fingerprint"]})
    rules.append({"query": "(", "replacement": "", "paths": [path], "regular_expression": True})
    with pytest.raises(ValueError):
        run_tool(session, "replace_sequence", {"rules": rules})
    assert session._bytes(path) == before and not session.dirty


def test_grouped_navigation_retains_hierarchy_attributes_and_landmarks(tmp_path: Path):
    source = tmp_path / "groups.epub"
    _book(source)
    nav = '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
      <nav epub:type="toc list"><ol class="contents"><li id="part"><span>Part I</span><ol>
      <li id="chapter" class="chapter"><a href="Text/one.xhtml" class="entry"><b>One</b></a></li>
      <li><a href="Text/two.xhtml">Two</a></li></ol></li></ol></nav>
      <nav epub:type="landmarks"><ol><li><a href="Text/one.xhtml">Start</a></li></ol></nav></body></html>'''
    _rewrite_book(source, {"OEBPS/nav.xhtml": nav})
    session = ContentSession.open(str(source))
    assert [entry["depth"] for entry in session.toc] == [0, 1, 1]
    assert session.toc[0]["href"] == ""
    entries = [session.toc[0], session.toc[2], session.toc[1]]
    session.set_toc(entries)
    root = etree.fromstring(session._bytes(session.nav_path))
    assert root.xpath("//*[local-name()='li' and @id='chapter' and @class='chapter']/*[@class='entry']/*[local-name()='b']/text()") == ["One"]
    assert root.xpath("//*[local-name()='li' and @id='part']/*[local-name()='span']/text()") == ["Part I"]
    assert "Start" in session._bytes(session.nav_path).decode()
    output = tmp_path / "saved.epub"
    session.save(str(output), False)
    assert ContentSession.open(str(output)).toc == entries
    assert not ContentSession.open(str(source)).dirty


@pytest.mark.parametrize("valid_ncx", [True, False])
def test_malformed_navigation_is_readable_without_rewriting_source(tmp_path: Path, valid_ncx: bool):
    source = tmp_path / "malformed.epub"
    _book(source)
    changes = {"OEBPS/nav.xhtml": '<html><body><nav epub:type="toc"><ol><li><a href="Text/one.xhtml">Recovered</a></ol></nav>'}
    if not valid_ncx:
        changes["OEBPS/toc.ncx"] = "<ncx><navMap>"
    _rewrite_book(source, changes)
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    assert session.toc[0]["label"] == ("One" if valid_ncx else "Recovered")
    assert session.preview(session.spine[0])
    assert not session.changes and not session.dirty
    assert source.read_bytes() == original
    output = tmp_path / "preserved.epub"
    session.save(str(output), False)
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert {name: before.read(name) for name in before.namelist()} == {name: after.read(name) for name in after.namelist()}


def test_ncx_without_namespace_can_be_read(tmp_path: Path):
    source = tmp_path / "namespace.epub"
    _book(source)
    with zipfile.ZipFile(source) as book:
        opf = book.read("OEBPS/book.opf").decode().replace(' properties="nav"', "")
        ncx = book.read("OEBPS/toc.ncx").decode().replace(' xmlns="http://www.daisy.org/z3986/2005/ncx/"', "")
    _rewrite_book(source, {"OEBPS/book.opf": opf, "OEBPS/toc.ncx": ncx})
    assert len(ContentSession.open(str(source)).toc) == 2


def test_html5_recovery_preserves_svg_casing_entities_and_table_content(tmp_path: Path):
    source = tmp_path / "html5.epub"
    _book(source)
    content = '<html><body><table><p>Outside table&nbsp;中文</p><tr><td>Cell</table><svg viewBox="0 0 100 200"><image href="../Images/pixel.png"/></svg><ruby>字<rt>zi</ruby><p>Last paragraph'
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": content})
    session = ContentSession.open(str(source))
    preview = session.preview(session.spine[0])
    assert 'viewBox="0 0 100 200"' in preview
    assert 'Outside table\u00a0中文' in preview and '<tbody' in preview
    assert preview.index('Outside table') < preview.index('<table')
    assert '<ruby' in preview and 'Last paragraph' in preview
    assert session._bytes(session.spine[0]) == content.encode()


def test_html5_source_mapping_survives_table_reparenting_and_ignores_forged_lines(tmp_path: Path):
    source = tmp_path / "lines.epub"
    _book(source)
    content = '<html><body>\n<table>\n<p id="moved" data-transoria-line="999">Outside table</p>\n<tr><td>Cell</table>\n<p id="last">Last paragraph'
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": content})
    session = ContentSession.open(str(source))
    root = epub_content_module.lxml_html.fromstring(session.preview(session.spine[0]))
    assert root.xpath('//*[@id="moved"]/@data-transoria-line') == ["3"]
    assert root.xpath('//*[@id="last"]/@data-transoria-line') == ["5"]
    assert not root.xpath('//*[@*[starts-with(name(), "data-transoria-source-")]]')


def test_preview_preserves_conditional_imports_and_link_media(tmp_path: Path):
    source = tmp_path / "styles.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Styles/import.css", b"p{display:grid;color:blue}", "text/css")
    session.write("OEBPS/Styles/book.css", '@import "import.css" layer(book) supports(display:grid) screen and (min-width:1px); @media print{p{display:none}}')
    session.write(session.spine[0], '<html xmlns="http://www.w3.org/1999/xhtml"><head><link href="../Styles/book.css" rel="stylesheet" media="screen"/></head><body><p>Text</p></body></html>')
    original = session.changes.copy()
    preview = session.preview(session.spine[0])
    assert ') layer(book) supports(display:grid) screen and (min-width:1px);' in preview
    imported = base64.b64decode(preview.partition('data:text/css;base64,')[2].partition('"')[0]).decode()
    assert 'p{display:grid;color:blue;}' in imported and '@media print' in preview
    assert '<style media="screen">' in preview
    assert session.changes == original


@pytest.mark.parametrize(("query", "replacement", "regular_expression", "count"), [
    (r"(?m)^Chapter (\d+)$", r"Section \1", True, 2),
    (r"(?<=A )🌸", "Flower", True, 1),
    ("e\u0301", "accent", False, 1),
    (r"\bAlpha\b", "Beta", True, 2),
    (r"(?s)Chapter 1.*Chapter 2", "Span", True, 1),
    ("x.y", r"\1$", False, 1),
    (r"(?P<word>Alpha)", r"[\g<word>]", True, 2),
])
def test_search_preview_single_batch_and_undo_agree_across_unicode_files(tmp_path: Path, query: str, replacement: str, regular_expression: bool, count: int):
    source = tmp_path / "matrix.epub"
    _book(source)
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    content = "Chapter 1\nA 🌸 α e\u0301.\nChapter 2\nAlpha ALPHA\nx.y x+y\\path\n"
    paths = ["OEBPS/Text/a.txt", "OEBPS/Text/b.txt"]
    for path in paths:
        session.add_resource(path, content.encode(), "text/plain")
    baseline = session._snapshot()
    found = session.search(query, paths, regular_expression=regular_expression)
    assert len(found) == 2 * count
    first = found[0]
    session.replace_match(first["path"], first["start"], first["end"], query, replacement, regular_expression=regular_expression, expected_fingerprint=first["fingerprint"])
    assert session._bytes(paths[1]) == content.encode()
    session.history("undo")
    assert session._snapshot() == baseline
    proposal = session.preview_replace(query, replacement, paths, regular_expression=regular_expression)
    assert proposal["replacements"] == len(found)
    assert session._snapshot() == baseline
    result = session.replace(query, replacement, paths, expected_count=len(found), regular_expression=regular_expression, expected_fingerprints=proposal["fingerprints"])
    assert result == {"replacements": 2 * count, "files_changed": 2}
    updated = session._snapshot()
    session.history("undo")
    assert session._snapshot() == baseline
    session.history("redo")
    assert session._snapshot() == updated and source.read_bytes() == original


@pytest.mark.parametrize("query", [r"^", r"(?=Hello)", r".*?", r"\b"])
def test_zero_width_patterns_reject_all_operations_without_mutation(tmp_path: Path, query: str):
    source = tmp_path / "empty-match.epub"
    _book(source)
    session = ContentSession.open(str(source))
    baseline = session._snapshot()
    for operation in (
        lambda: session.search(query, session.spine, regular_expression=True),
        lambda: session.preview_replace(query, "new", session.spine, regular_expression=True),
        lambda: session.replace(query, "new", session.spine, regular_expression=True),
    ):
        with pytest.raises(ValueError, match="Zero-width"):
            operation()
        assert session._snapshot() == baseline and not session.dirty


def test_preview_css_token_urls_strings_nested_rules_and_vertical_aliases(tmp_path: Path):
    source = tmp_path / "tokens.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Images/odd(name).png", b"odd-image", "image/png")
    css = r'''/* url(missing.png) */ p::before{content:"url(missing.png)"}
    @layer book{@supports(display:grid){p{display:grid;background:url("../Images/odd(name).png")}}}
    body{-epub-writing-mode:vertical-rl;-epub-text-orientation:upright}
    p{mask:url("../Images/pixel.png#symbol");--data:url("data:image/svg+xml,%3Csvg%3E(x)%3C/svg%3E")}
    @import "book.css";
    '''
    session.write("OEBPS/Styles/book.css", css)
    preview = session.preview(session.spine[0])
    assert 'content:"url(missing.png)"' in preview
    assert '/* url(missing.png) */' in preview
    assert "b2RkLWltYWdl" in preview
    assert "#symbol" in preview
    assert "(x)%3C/svg%3E" in preview
    assert "writing-mode:vertical-rl" in preview and "text-orientation:upright" in preview
    assert "@layer book" in preview and "@supports(display:grid)" in preview
    assert session._bytes("OEBPS/Styles/book.css") == css.encode()


def test_repeated_css_image_expansion_is_bounded_and_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "expansion.epub"
    _book(source)
    session = ContentSession.open(str(source))
    monkeypatch.setattr(epub_content_module, "MAX_PREVIEW_BYTES", 64)
    with pytest.raises(ValueError, match="Expanded preview CSS"):
        epub_content_module._inline_css('p{background:url(../Images/pixel.png);mask:url(../Images/pixel.png)}', "OEBPS/Styles/book.css", session)
    assert not session.dirty


@pytest.mark.parametrize("import_rule", ['url("import\\20 file.css")', '"import file.css"'])
def test_css_import_escaped_space_and_anonymous_layer(tmp_path: Path, import_rule: str):
    source = tmp_path / "import.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Styles/import file.css", b'p{color:green}', "text/css")
    css = f'@import {import_rule} layer supports(selector(:has(*))) screen;'
    preview = epub_content_module._inline_css(css, "OEBPS/Styles/book.css", session)
    assert ') layer supports(selector(:has(*))) screen;' in preview
    imported = base64.b64decode(preview.partition('data:text/css;base64,')[2].partition('"')[0]).decode()
    assert 'p{color:green;}' in imported


def test_imported_css_namespace_stays_isolated_with_original_conditions(tmp_path: Path):
    source = tmp_path / "namespaced-style.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Styles/namespaced.css", b'@namespace x url(http://www.w3.org/1999/xhtml); x|p{color:green;background:url(../Images/pixel.png)}', "text/css")
    session.add_resource("OEBPS/Styles/first.css", b'p{color:blue}', "text/css")
    session.write("OEBPS/Styles/book.css", '@import "first.css"; @import "namespaced.css" layer(book) supports(display:grid) screen; p{color:red}')
    preview = session.preview(session.spine[0])
    imports = preview.split('data:text/css;base64,')
    assert len(imports) == 3
    assert 'p{color:blue;}' in base64.b64decode(imports[1].partition('"')[0]).decode()
    encoded = imports[2].partition('"')[0]
    css = base64.b64decode(encoded).decode()
    assert '@namespace x url(http://www.w3.org/1999/xhtml)' in css
    assert 'x|p{color:green;' in css and 'data:image/png;base64,' in css
    assert ') layer(book) supports(display:grid) screen;' in preview
    assert "style-src &#x27;unsafe-inline&#x27; data:" in preview


@pytest.mark.parametrize("attributes", ['rel="alternate stylesheet" title="Night"', 'rel="stylesheet" disabled="disabled"', 'rel="preload" as="style"'])
def test_inactive_or_non_stylesheet_links_are_not_activated(tmp_path: Path, attributes: str):
    source = tmp_path / "inactive.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.write(session.spine[0], f'<html xmlns="http://www.w3.org/1999/xhtml"><head><link href="../Styles/book.css" {attributes}/></head><body><p>Text</p></body></html>')
    preview = session.preview(session.spine[0])
    assert 'color: red' not in preview
    assert attributes in session.read(session.spine[0])["content"]


def test_directory_rename_retains_group_labels_and_ncx_ids_with_undo(tmp_path: Path):
    source = tmp_path / "directory.epub"
    _book(source)
    session = ContentSession.open(str(source))
    entries = [{"label": "Part", "href": "", "depth": 0}, {**session.toc[0], "label": "Edited", "depth": 1}, {**session.toc[1], "depth": 1}]
    session.set_toc(entries)
    assert etree.fromstring(session._bytes(session.ncx_path)).xpath("//*[local-name()='navPoint' and @id='one']/*[local-name()='navLabel']/*[local-name()='text']/text()") == ["Edited"]
    before = session._snapshot()
    session.rename_resource(session.spine[0], "OEBPS/Text/renamed.xhtml")
    assert session.toc[0]["href"] == "" and session.toc[1]["label"] == "Edited"
    assert session.toc[1]["href"] == "OEBPS/Text/renamed.xhtml"
    session.history("undo")
    assert session._snapshot() == before
    page = session.generate_toc_page("Contents")
    assert '<span>Part</span>' in session._bytes(page).decode()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_bom_encoding_preview_and_edit_preserve_text(tmp_path: Path, encoding: str):
    source = tmp_path / "bom.epub"
    _book(source)
    content = '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>中文 Korean</p></body></html>'
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": content.encode(encoding)})
    session = ContentSession.open(str(source))
    assert "中文" in session.preview(session.spine[0])
    session.write(session.spine[0], content.replace("Korean", "changed"))
    assert "中文 changed" in session._bytes(session.spine[0]).decode(encoding)


@pytest.mark.parametrize("malformed", [False, True])
def test_prefixed_xhtml_and_svg_preview_keeps_source_unchanged(tmp_path: Path, malformed: bool):
    source = tmp_path / "prefix.epub"
    _book(source)
    content = '<h:html xmlns:h="http://www.w3.org/1999/xhtml"><h:body><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 200"><image xlink:href="../Images/pixel.png" width="100" height="200"/></svg><h:p>Text</h:p></h:body></h:html>'
    if malformed:
        content = content.replace('</h:p>', '')
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": content})
    session = ContentSession.open(str(source))
    preview = session.preview(session.spine[0])
    assert "data:image/png;base64," in preview
    assert "<h:" not in preview and "<body" in preview
    assert session._bytes(session.spine[0]) == content.encode()
    assert not session.dirty


def test_move_image_updates_every_inline_and_embedded_css_reference(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><style>p {background:url(../Images/pixel.png)}</style></head><body><img src="../Images/pixel.png"/><p style="background:url(../Images/pixel.png)">A</p><div style="background:url(../Images/pixel.png)">B</div></body></html>'})
    session = ContentSession.open(str(source))
    session.rename_resource("OEBPS/Images/pixel.png", "OEBPS/Assets/new.png")
    content = session.read(session.spine[0])["content"]
    assert "../Images/pixel.png" not in content
    assert content.count("../Assets/new.png") == 4


def test_preview_old_encoding_and_internal_anchor_mapping(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": b'<?xml version="1.0" encoding="iso-8859-1"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p id="a">caf\xe9</p><a href="#a">Go</a></body></html>'})
    session = ContentSession.open(str(source))
    preview = session.preview(session.spine[0])
    assert "caf\u00e9" in preview and "caf\u00c3" not in preview
    assert 'data-transoria-target="OEBPS/Text/one.xhtml#a"' in preview
    assert not session.dirty


def test_css_split_keeps_legacy_charset(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Styles/book.css": b'@charset "iso-8859-1";\np {font-family:"caf\xe9";}\ndiv {font-family:"caf\xe9";}'})
    session = ContentSession.open(str(source))
    points = session.style_split_points("OEBPS/Styles/book.css")
    session.split_style("OEBPS/Styles/book.css", "OEBPS/Styles/prefix.css", points[-1]["index"])
    assert "caf\u00e9" in session.read("OEBPS/Styles/book.css")["content"]
    assert "caf\u00e9" in session.read("OEBPS/Styles/prefix.css")["content"]


def test_issue_report_inspects_css_import_and_font_urls(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Styles/book.css": '@import "missing.css";\n@font-face {src:url("../Fonts/missing.ttf")}\np {background:url(../Images/pixel.png)}'})
    rows = issues(ContentSession.open(str(source)))["rows"]
    missing = [row["message"] for row in rows if row["kind"] == "missing_resource"]
    assert missing == ["missing.css", "../Fonts/missing.ttf"]


def _synthetic_font(restricted: int = 0, extra: tuple[int, ...] = ()) -> bytes:
    import io
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    builder = FontBuilder(1000, isTTF=True)
    codes = sorted(set(range(32, 256)) | set(extra))
    order = [".notdef", *[f"glyph{index}" for index in codes]]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({index: f"glyph{index}" for index in codes})
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0)); pen.lineTo((450, 0)); pen.lineTo((250, 700)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (500, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "TestFont", "styleName": "Regular", "uniqueFontIdentifier": "TestFont", "fullName": "TestFont", "psName": "TestFont"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200, fsType=restricted)
    builder.setupPost()
    builder.setupMaxp()
    buffer = io.BytesIO()
    builder.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("restriction", [0, 2, 256])
def test_font_subset_embedding_and_license_gates(tmp_path: Path, restriction: int):
    import io
    from fontTools.ttLib import TTFont
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    data = _synthetic_font(restriction)
    path = "OEBPS/Fonts/test.ttf"
    session.add_resource(path, data, "font/ttf")
    report = run_tool(session, "fonts", {})
    assert session._bytes(path) == data
    if restriction:
        assert report["rows"][0]["skipped"]
        return
    assert report["count"] == 1
    run_tool(session, "fonts", {"apply": True, "fingerprint": report["fingerprint"]})
    with TTFont(io.BytesIO(session._bytes(path))) as font:
        assert all(ord(char) in font.getBestCmap() for char in "Hello world.")
    session.history("undo")
    assert session._bytes(path) == data
    epub_editor_tools.embed_font(session, path, "Test Font", "OEBPS/Styles/book.css")
    assert "@font-face" in session.read("OEBPS/Styles/book.css")["content"]
    assert "data:font/ttf;base64," in session.preview(session.spine[0])


def test_font_subset_retains_entities_and_css_escaped_glyphs(tmp_path: Path):
    import io
    from fontTools.ttLib import TTFont
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><style>p:before {content:"\\2606"}</style></head><body><p style="--marker: \'\\2665\'">&#x4E2D;</p></body></html>',
        "OEBPS/Styles/book.css": 'p:after {content:"\\2605"}',
    })
    session = ContentSession.open(str(source))
    path = "OEBPS/Fonts/test.ttf"
    required = (0x4E2D, 0x2605, 0x2606, 0x2665)
    original = _synthetic_font(extra=required)
    session.add_resource(path, original, "font/ttf")
    report = run_tool(session, "fonts", {})
    assert report["count"] == 1
    run_tool(session, "fonts", {"apply": True, "fingerprint": report["fingerprint"]})
    with TTFont(io.BytesIO(session._bytes(path))) as font:
        assert all(code in font.getBestCmap() for code in required)
    session.history("undo")
    assert session._bytes(path) == original


def test_font_subset_refuses_incomplete_document_inspection(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Text/two.xhtml": '<html><body><p>Broken</body></html>'})
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Fonts/test.ttf", _synthetic_font(), "font/ttf")
    before = session._snapshot()
    with pytest.raises(etree.XMLSyntaxError):
        run_tool(session, "fonts", {})
    assert session._snapshot() == before


def test_external_check_stages_valid_archive_without_writing_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess
    source = tmp_path / "book.epub"
    jar = tmp_path / "check.jar"
    _book(source)
    jar.write_bytes(b"test")
    before = source.read_bytes()
    session = ContentSession.open(str(source))
    session.write(session.spine[0], session.read(session.spine[0])["content"].replace("Hello", "Hi"))
    monkeypatch.setattr(epub_editor_tools.shutil, "which", lambda _: "/test/java")
    def check(command, **kwargs):
        assert kwargs["timeout"] == 120
        with zipfile.ZipFile(command[-1]) as archive:
            assert archive.infolist()[0].filename == "mimetype"
            assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
            assert b"Hi world" in archive.read(session.spine[0])
        return subprocess.CompletedProcess(command, 1, "test diagnostic", "")
    monkeypatch.setattr(epub_editor_tools.subprocess, "run", check)
    assert epub_editor_tools.external_check(session, str(jar))["exit_code"] == 1
    assert source.read_bytes() == before


def test_format_import_uses_cache_and_cleans_failed_conversion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess
    source = tmp_path / "input.txt"
    converter = tmp_path / "converter"
    source.write_text("Test source", encoding="utf-8")
    converter.touch()
    cache = tmp_path / "cache"
    def convert(command, **kwargs):
        assert command[1] == str(source)
        assert command[3:] == ["--epub-version", "3"]
        _book(Path(command[2]))
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(epub_editor_tools.subprocess, "run", convert)
    output = epub_editor_tools.import_book(str(source), str(converter), cache)
    assert output.parent.parent == cache
    assert ContentSession.open(str(output)).spine
    monkeypatch.setattr(epub_editor_tools.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "invalid"))
    with pytest.raises(ValueError, match="invalid"):
        epub_editor_tools.import_book(str(source), str(converter), cache)
    assert len(list(cache.iterdir())) == 1
    assert source.read_text() == "Test source"


@pytest.mark.parametrize("quality", [None, True, 1.5, "85"])
def test_tool_invalid_numeric_options_do_not_mutate(tmp_path: Path, quality):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    with pytest.raises(ValueError, match="integers"):
        run_tool(session, "images", {"quality": quality})
    assert not session.dirty


def test_diff_does_not_normalize_unchanged_package_namespaces(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    assert compare(session) == {"rows": [], "count": 0}
    session.write(session.spine[0], session.read(session.spine[0])["content"].replace("Hello", "Hi"))
    assert [row["path"] for row in compare(session)["rows"]] == [session.spine[0]]


def test_malformed_text_reports_are_read_only(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    malformed = b'<html><head><title>Hidden</title></head><body><div><p>Hello Hello!!!</body></html>'
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": malformed})
    session = ContentSession.open(str(source))
    assert any(row["kind"] == "xml" for row in issues(session)["rows"])
    report = text_report(session)
    assert any(row["kind"] == "repeated_word" for row in report["rows"])
    assert "hidden" not in {row["word"] for row in report["words"]}
    assert session._bytes(session.spine[0]) == malformed
    assert not session.dirty


def test_hunspell_report_handles_inflections_case_suggestions_and_ignored_words(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>cats Cat cta Transoria</p></body></html>', "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>cat</p></body></html>'})
    dictionary = tmp_path / "test.dic"
    dictionary.write_text("1\ncat/S\n", encoding="utf-8")
    dictionary.with_suffix(".aff").write_text("SET UTF-8\nTRY abcdefghijklmnopqrstuvwxyz\nSFX S Y 1\nSFX S 0 s .\n", encoding="utf-8")
    session = ContentSession.open(str(source))
    report = text_report(session, str(dictionary), ["Transoria"])
    spelling = [row for row in report["rows"] if row["kind"] == "spelling"]
    assert report["dictionary_loaded"]
    assert [row["message"] for row in spelling] == ["cta"]
    assert "cat" in spelling[0]["suggestions"]
    assert not session.dirty
    dictionary.with_suffix(".aff").unlink()
    with pytest.raises(ValueError, match="matching .aff"):
        text_report(session, str(dictionary))


def test_font_embedding_rejects_restricted_license_without_changes(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Fonts/restricted.ttf", _synthetic_font(2), "font/ttf")
    before = session._snapshot()
    with pytest.raises(ValueError, match="prohibits embedding"):
        epub_editor_tools.embed_font(session, "OEBPS/Fonts/restricted.ttf", "Test", "OEBPS/Styles/book.css")
    assert session._snapshot() == before


def test_multiple_packages_preserve_unselected_rendition(tmp_path: Path):
    source, output = tmp_path / "book.epub", tmp_path / "saved.epub"
    _book(source)
    with zipfile.ZipFile(source) as book:
        container, package = book.read("META-INF/container.xml"), book.read("OEBPS/book.opf")
    _rewrite_book(source, {
        "META-INF/container.xml": container.replace(b"</rootfiles>", b'<rootfile full-path="Other/book.opf" media-type="application/oebps-package+xml"/></rootfiles>'),
        "Other/book.opf": package,
        "Other/vendor.dat": b"untouched secondary rendition",
    })
    session = ContentSession.open(str(source))
    session.write(session.spine[0], session.read(session.spine[0])["content"].replace("Hello", "Hi"))
    session.save(str(output), overwrite=False)
    with zipfile.ZipFile(output) as book:
        assert book.read("Other/book.opf") == package
        assert book.read("Other/vendor.dat") == b"untouched secondary rendition"
    assert ContentSession.open(str(output)).opf_path == "OEBPS/book.opf"


def test_merge_chapters_migrates_toc_links_and_is_one_undo_step(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    one, two = session.spine
    session.write(one, session.read(one)["content"].replace("<script>alert(1)</script>", ""))
    session.write(two, session.read(two)["content"].replace("<p>", '<p id="second">').replace("</body>", '<a href="one.xhtml#start">Back</a></body>'))
    session.toc[1]["href"] = two + "#second"
    before = session._snapshot()
    undo_count = len(session.undo_stack)
    assert session.merge_resources([one, two]) == one
    assert len(session.undo_stack) == undo_count + 1
    assert session.spine == [one]
    assert session.toc[1]["href"] == one + "#second"
    assert "Hello again" in session.read(one)["content"]
    retained = session._package().find("{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item[@id='one']")
    assert retained.get("href") == "Text/one.xhtml"
    assert not etree.fromstring(session._bytes(one)).findall(".//{http://www.w3.org/1999/xhtml}section")
    session.history("undo")
    assert session._snapshot() == before
    session.history("redo")
    output = tmp_path / "merged.epub"
    session.save(str(output), False)
    reopened = ContentSession.open(str(output))
    assert reopened.spine == [one]
    assert reopened.toc[1]["href"] == one + "#second"
    assert source.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        assert two not in archive.namelist()
        assert archive.read("OEBPS/Images/pixel.png") == b"image-bytes"


def test_merge_rejects_duplicate_ids_atomically(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.write(session.spine[0], session.read(session.spine[0])["content"].replace("<script>alert(1)</script>", ""))
    session.write(session.spine[1], session.read(session.spine[1])["content"].replace("<p>", '<p id="start">'))
    before = session._snapshot()
    with pytest.raises(ValueError, match="duplicate IDs"):
        session.merge_resources(session.spine.copy())
    assert session._snapshot() == before


def test_css_split_preserves_import_order_and_relative_urls(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    style = "OEBPS/Styles/book.css"
    session.write(style, 'body { background:url(../Images/pixel.png) } p { color:blue }')
    target = "OEBPS/Styles/parts/first.css"
    count = len(session.undo_stack)
    session.split_style(style, target, 2)
    assert len(session.undo_stack) == count + 1
    assert '@import "parts/first.css"' in session.read(style)["content"]
    assert "../../Images/pixel.png" in session.read(target)["content"]
    assert "color:blue" in session.preview(session.spine[0])
    session.save(str(tmp_path / "split.epub"), False)


def test_css_merge_rewrites_inbound_links_and_resource_urls(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    first = "OEBPS/Styles/book.css"
    second = "OEBPS/Other/second.css"
    session.add_resource(second, b'p { background:url(../Images/pixel.png) }', "text/css")
    session.write(session.spine[1], session.read(session.spine[1])["content"].replace("<head/>", '<head><link rel="stylesheet" href="../Other/second.css"/></head>'))
    session.merge_resources([first, second])
    assert "../Images/pixel.png" in session.read(first)["content"]
    assert "../Styles/book.css" in session.read(session.spine[1])["content"]
    session.save(str(tmp_path / "merged.epub"), False)


def test_multi_buffer_write_rolls_back_on_last_failure(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    before = session._snapshot()
    with pytest.raises(ValueError):
        session.write_many({session.spine[0]: "changed", "missing": "failed"})
    assert session._snapshot() == before
    assert not session.undo_stack
    session.write_many({path: session.read(path)["content"].replace("Hello", "Hi") for path in session.spine})
    assert len(session.undo_stack) == 1
    session.history("undo")
    assert session._snapshot() == before


def test_tools_issue_report_locates_xml_ids_and_missing_links(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    first, second = session.spine
    session.write(first, session.read(first)["content"].replace("</body>", '<p id="start">Duplicate</p><a href="two.xhtml#lost">Broken</a></body>'))
    session.write(second, "<html><body></html>")
    report = issues(session)
    assert {row["kind"] for row in report["rows"]} >= {"xml", "duplicate_id"}
    assert all(row["line"] > 0 for row in report["rows"])
    assert source.exists()


def test_css_cleanup_retains_dynamic_and_nested_rules_and_supports_undo(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    style = "OEBPS/Styles/book.css"
    session.write(style, 'body{color:red}.absent{color:blue}p:hover{color:black}@media screen{.absent{color:white}}')
    before = session._snapshot()
    report = cleanup_css(session)
    assert report["count"] == 1
    assert session._snapshot() == before
    cleanup_css(session, True)
    assert ".absent{color:blue}" not in session.read(style)["content"]
    assert "p:hover" in session.read(style)["content"]
    assert "@media" in session.read(style)["content"]
    session.history("undo")
    assert session._snapshot() == before


def test_optimization_rejects_stale_preview(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    proposal = run_tool(session, "cleanup_css", {})
    session.write(session.spine[0], session.read(session.spine[0])["content"].replace("Hello", "Goodbye"))
    before = session._snapshot()
    with pytest.raises(ValueError, match="draft changed"):
        run_tool(session, "cleanup_css", {"apply": True, "fingerprint": proposal["fingerprint"]})
    assert session._snapshot() == before


def test_optimization_rejects_changed_linear_flags(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    proposal = run_tool(session, "cleanup_css", {})
    session.set_spine([{"path": path, "linear": False} for path in session.spine])
    before = session._snapshot()
    with pytest.raises(ValueError, match="draft changed"):
        run_tool(session, "cleanup_css", {"apply": True, "fingerprint": proposal["fingerprint"]})
    assert session._snapshot() == before


def test_diff_includes_spine_toc_and_binary_changes(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.reorder_spine(list(reversed(session.spine)))
    session.set_toc([{**entry, "label": "Changed " + str(entry["label"])} for entry in session.toc])
    session.replace_resource("OEBPS/Images/pixel.png", b"different image")
    result = compare(session)
    assert {row["path"] for row in result["rows"]} >= {session.opf_path, session.nav_path, session.ncx_path, "OEBPS/Images/pixel.png"}
    assert "Changed" in next(row["diff"] for row in result["rows"] if row["path"] == session.nav_path)
    assert session.dirty


def test_named_checkpoints_survive_restart_and_restore_with_undo(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    store = ContentSessionStore(tmp_path / "sessions")
    summary = store.open(str(source))
    sid = str(summary["session_id"])
    session = store.get(sid)
    session.named_checkpoint("Before")
    session.write(session.spine[0], session.read(session.spine[0])["content"].replace("Hello", "Hi"))
    store.persist(sid)
    restored = ContentSessionStore(tmp_path / "sessions").get(sid)
    assert list(restored.checkpoints) == ["Before"]
    assert compare(restored, "Before")["count"] == 1
    restored.restore_checkpoint("Before")
    assert "Hello" in restored.read(restored.spine[0])["content"]
    restored.history("undo")
    assert "Hi" in restored.read(restored.spine[0])["content"]
    assert source.read_bytes() == session.path.read_bytes()


def test_toc_sidebar_and_navigation_source_stay_synchronized(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    before = session._snapshot()
    session.set_toc([{**entry, "label": "Chapter " + str(entry["label"])} for entry in session.toc])
    assert "Chapter One" in session.read(session.nav_path)["content"]
    assert "Chapter One" in session.read(session.ncx_path)["content"]
    session.history("undo")
    assert session._snapshot() == before
    session.write(session.nav_path, session.read(session.nav_path)["content"].replace("One", "First"))
    assert session.toc[0]["label"] == "First"
    assert "First" in session.read(session.ncx_path)["content"]
    session.history("undo")
    assert session._snapshot() == before
    session.replace("One", "First", [session.ncx_path], case_sensitive=True)
    assert session.toc[0]["label"] == "First"
    assert "First" in session.read(session.nav_path)["content"]
    session.history("undo")
    assert session._snapshot() == before


def test_navigation_batch_replacement_saves_and_undoes_as_one_operation(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    before = session._snapshot()
    proposal = session.preview_replace("One", "First", [session.nav_path, session.ncx_path], case_sensitive=True)
    session.replace("One", "First", [session.nav_path, session.ncx_path], case_sensitive=True, expected_fingerprints=proposal["fingerprints"])
    assert len(session.undo_stack) == 1
    assert session.toc[0]["label"] == "First"
    session.history("undo")
    assert session._snapshot() == before
    session.history("redo")
    output = tmp_path / "saved.epub"
    session.save(str(output), False)
    assert ContentSession.open(str(output)).toc[0]["label"] == "First"


def test_malformed_navigation_draft_keeps_last_valid_toc_without_silent_repair(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    before = session._snapshot()
    session.write(session.nav_path, "<html><nav>")
    assert session.toc == before[2]
    assert "<html><nav>" in session.read(session.nav_path)["content"]
    with pytest.raises(ValueError, match="Invalid XML"):
        session.save(str(tmp_path / "bad.epub"), False)
    session.history("undo")
    assert session._snapshot() == before


@pytest.mark.parametrize("checkpoints", [None, [], {"": {}}, {"x" * 81: {}}])
def test_invalid_checkpoint_cache_has_a_recoverable_error(tmp_path: Path, checkpoints):
    import json
    source = tmp_path / "book.epub"
    _book(source)
    store = ContentSessionStore(tmp_path / "sessions")
    summary = store.open(str(source))
    sid = str(summary["session_id"])
    path = store._state_path(sid)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checkpoints"] = checkpoints
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoints could not be restored"):
        ContentSessionStore(tmp_path / "sessions").get(sid)


def test_text_report_uses_visible_text_and_custom_dictionary(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    dictionary = tmp_path / "dictionary.txt"
    dictionary.write_text("hello\nworld\nagain\noutside", encoding="utf-8")
    before = session._snapshot()
    report = text_report(session, str(dictionary))
    assert report["dictionary_loaded"]
    assert not [row for row in report["rows"] if row["kind"] == "spelling"]
    assert "alert" not in {row["word"] for row in report["words"]}
    session.write(session.spine[1], session.read(session.spine[1])["content"].replace("Hello again.", "Helo again again!!!"))
    report = text_report(session, str(dictionary))
    assert {row["kind"] for row in report["rows"]} >= {"spelling", "repeated_word", "punctuation"}
    assert any(row.get("suggestions") == ["hello"] for row in report["rows"])
    assert session._snapshot() != before


def test_image_preview_and_apply_keep_original_and_resize_safely(tmp_path: Path):
    from PIL import Image
    import io
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    data = io.BytesIO()
    Image.new("RGB", (1000, 800), (240, 200, 20)).save(data, "PNG", compress_level=0)
    session.replace_resource("OEBPS/Images/pixel.png", data.getvalue())
    before = session._snapshot()
    report = image_report(session, max_dimension=100)
    assert report["count"] == 1
    assert session._snapshot() == before
    image_report(session, True, max_dimension=100)
    with Image.open(io.BytesIO(session._bytes("OEBPS/Images/pixel.png"))) as image:
        assert image.size == (100, 80)
    session.history("undo")
    assert session._snapshot() == before


def test_cover_metadata_is_undoable_and_preserves_text(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    before = session._snapshot()
    set_cover(session, "OEBPS/Images/pixel.png")
    assert session._package().find("{*}metadata/{*}meta[@name='cover']").get("content") == "img"
    assert session._bytes(session.spine[0]) == ContentSession.open(str(source))._bytes(session.spine[0])
    session.history("undo")
    assert session._snapshot() == before


def test_epub2_upgrade_adds_navigation_and_round_trips(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    with zipfile.ZipFile(source) as archive:
        package = archive.read("OEBPS/book.opf").decode().replace('version="3.0"', 'version="2.0"').replace(' properties="nav"', '')
    _rewrite_book(source, {"OEBPS/book.opf": package})
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    before = session._snapshot()
    upgrade_epub(session)
    assert session._package().get("version") == "3.0"
    assert session.nav_path == "OEBPS/navigation3.xhtml"
    session.history("undo")
    assert session._snapshot() == before
    session.history("redo")
    output = tmp_path / "upgraded.epub"
    session.save(str(output), False)
    assert ContentSession.open(str(output)).toc == before[2]
    assert source.read_bytes() == original


def test_epub2_upgrade_migrates_metadata_and_embedded_resource_properties(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    with zipfile.ZipFile(source) as archive:
        package = archive.read("OEBPS/book.opf").decode().replace('version="3.0"', 'version="2.0"').replace(' properties="nav"', '')
        package = package.replace('<dc:identifier id="id">', '<dc:identifier xmlns:opf="http://www.idpf.org/2007/opf" opf:scheme="UUID" id="id">')
        package = package.replace('</metadata>', '<dc:creator xmlns:opf="http://www.idpf.org/2007/opf" opf:role="aut" opf:file-as="Author, Test">Test Author</dc:creator><meta name="cover" content="img"/></metadata>')
        one = archive.read("OEBPS/Text/one.xhtml").decode().replace('</body>', '<svg xmlns="http://www.w3.org/2000/svg"/><math xmlns="http://www.w3.org/1998/Math/MathML"/></body>')
    _rewrite_book(source, {"OEBPS/book.opf": package, "OEBPS/Text/one.xhtml": one})
    session = ContentSession.open(str(source))
    before = session._snapshot()
    upgrade_epub(session)
    package = session._package()
    metadata = package.find("{http://www.idpf.org/2007/opf}metadata")
    assert all(not key.startswith("{http://www.idpf.org/2007/opf}") for node in metadata for key in node.attrib)
    assert metadata.find("{http://www.idpf.org/2007/opf}meta[@property='identifier-type']").text == "UUID"
    assert metadata.find("{http://www.idpf.org/2007/opf}meta[@property='role']").text == "aut"
    assert metadata.find("{http://www.idpf.org/2007/opf}meta[@property='file-as']").text == "Author, Test"
    manifest = package.find("{http://www.idpf.org/2007/opf}manifest")
    assert set(manifest.find("{http://www.idpf.org/2007/opf}item[@id='one']").get("properties").split()) >= {"svg", "mathml", "scripted"}
    assert "cover-image" in manifest.find("{http://www.idpf.org/2007/opf}item[@id='img']").get("properties")
    session.history("undo")
    assert session._snapshot() == before


def test_edit_search_replace_history_and_safe_preview(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    store = ContentSessionStore()
    state = store.open(str(source))
    session = store.get(str(state["session_id"]))
    one = "OEBPS/Text/one.xhtml"
    two = "OEBPS/Text/two.xhtml"

    assert [entry["label"] for entry in session.toc] == ["One", "Two"]
    assert len(session.search("Hello", [one, two])) == 2
    assert len(session.search("HELLO", [one, two], case_sensitive=True)) == 0
    session.replace_match(
        one,
        session.read(one)["content"].index("Hello"),
        session.read(one)["content"].index("Hello") + 5,
        "Hello",
        "Hi",
    )
    assert "Hi world" in session.read(one)["content"]
    session.history("undo")
    assert "Hello world" in session.read(one)["content"]
    session.history("redo")
    assert "Hi world" in session.read(one)["content"]
    with pytest.raises(ValueError, match="Search results changed"):
        session.replace("Hello", "Bonjour", [two], expected_count=2)
    assert "Hello again" in session.read(two)["content"]
    assert (
        session.replace("Hello", "Bonjour", [two], expected_count=1)["replacements"]
        == 1
    )
    assert "Hi world" in session.read(one)["content"]
    preview = session.preview(one)
    assert "alert(1)" not in preview
    assert "https://example.com" not in preview
    assert "body { color: red; }" in preview
    assert "data:image/png;base64," in preview
    assert "Draft text" in session.preview(
        one, one, session.read(one)["content"].replace("Hi world", "Draft text")
    )
    assert "Draft text" not in session.read(one)["content"]
    assert "color: blue" in session.preview(
        one, "OEBPS/Styles/book.css", "body { color: blue; }"
    )
    assert "color: red" in session.read("OEBPS/Styles/book.css")["content"]
    store.close(str(state["session_id"]))


def test_replace_match_reports_next_position_and_keeps_other_files(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    original = source.read_bytes()
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    first, second = opened["spine"]
    match = router.call("epub_content.search", {
        "session_id": sid, "query": "Hello", "paths": [first, second],
    })["matches"][0]
    replaced = router.call("epub_content.replace_match", {
        "session_id": sid, "query": "Hello", "replacement": "Greetings", **match,
    })
    assert replaced["replaced_end"] == match["start"] + len("Greetings")
    remaining = router.call("epub_content.search", {
        "session_id": sid, "query": "Hello", "paths": [first, second],
    })["matches"]
    assert [item["path"] for item in remaining] == [second]
    assert source.read_bytes() == original


def test_create_chapter_is_undoable_and_saves_in_requested_order(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    original = source.read_bytes()
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    first, second = opened["spine"]
    path = "OEBPS/Text/inserted.xhtml"
    created = router.call("epub_content.create_chapter", {
        "session_id": sid, "path": path, "title": "New & <Chapter>",
        "body_text": "First paragraph\nSecond paragraph", "after_path": first,
    })
    assert created["spine"] == [first, path, second]
    chapter = router.call("epub_content.read", {"session_id": sid, "path": path})["content"]
    assert "New &amp; &lt;Chapter&gt;" in chapter
    assert "First paragraph" in chapter and "Second paragraph" in chapter
    router.call("epub_content.undo", {"session_id": sid})
    assert router.call("epub_content.info", {"session_id": sid})["spine"] == [first, second]
    router.call("epub_content.redo", {"session_id": sid})
    router.call("epub_content.save", {"session_id": sid, "output_path": str(output), "overwrite": False})
    reopened = ContentSession.open(str(output))
    assert reopened.spine == [first, path, second]
    assert source.read_bytes() == original


def test_create_chapter_rejects_bad_insertion_without_changing_session(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    with pytest.raises(ValueError, match="insertion point"):
        session.create_chapter("OEBPS/Text/new.xhtml", "New", "", "missing.xhtml")
    assert not session.dirty
    assert all(item["path"] != "OEBPS/Text/new.xhtml" for item in session.files)


def test_split_chapter_is_atomic_undoable_and_preserves_resources(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "split.epub"
    _book(source)
    original = source.read_bytes()
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    first, second = opened["spine"]
    target = "OEBPS/Text/one_part2.xhtml"
    points = router.call("epub_content.split_points", {"session_id": sid, "path": first})["points"]
    assert points[0]["index"] == 1
    split = router.call("epub_content.split_chapter", {
        "session_id": sid, "path": first, "target": target, "index": 1,
    })
    assert split["spine"] == [first, target, second]
    assert "Hello world" in router.call("epub_content.read", {"session_id": sid, "path": first})["content"]
    assert "pixel.png" in router.call("epub_content.read", {"session_id": sid, "path": target})["content"]
    assert "pixel.png" not in router.call("epub_content.read", {"session_id": sid, "path": first})["content"]
    router.call("epub_content.undo", {"session_id": sid})
    assert router.call("epub_content.info", {"session_id": sid})["spine"] == [first, second]
    router.call("epub_content.redo", {"session_id": sid})
    router.call("epub_content.save", {
        "session_id": sid, "output_path": str(output), "overwrite": False,
    })
    reopened = ContentSession.open(str(output))
    assert reopened.spine == [first, target, second]
    assert "data:image/png;base64" in reopened.preview(target)
    assert source.read_bytes() == original


def test_split_chapter_migrates_moved_anchor_references(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    one = "OEBPS/Text/one.xhtml"
    with zipfile.ZipFile(source) as book:
        content = book.read(one).decode("utf-8")
    _rewrite_book(source, {one: content.replace("<img src=", '<p id="later">Later</p><img src=')})
    session = ContentSession.open(str(source))
    session.toc.append({"label": "Later", "href": one + "#later", "depth": 0})
    session.split_chapter(one, "OEBPS/Text/split.xhtml", 1)
    assert session.toc[-1]["href"] == "OEBPS/Text/split.xhtml#later"
    session.save(str(tmp_path / "split.epub"), False)


def test_split_chapter_migrates_local_link_back_to_first_half(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    one = "OEBPS/Text/one.xhtml"
    with zipfile.ZipFile(source) as book:
        content = book.read(one).decode("utf-8")
    _rewrite_book(source, {one: content.replace("<img src=", '<a href="#start">Back</a><img src=')})
    session = ContentSession.open(str(source))
    session.split_chapter(one, "OEBPS/Text/split.xhtml", 1)
    assert 'href="one.xhtml#start"' in session.read("OEBPS/Text/split.xhtml")["content"]
    session.save(str(tmp_path / "split.epub"), False)


def test_editor_session_survives_backend_restart(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    original = source.read_bytes()
    cache_root = tmp_path / "cache"
    router = BridgeRouter()
    register(router, cache_root=cache_root)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    session_id = opened["session_id"]
    path = opened["spine"][0]
    content = router.call("epub_content.read", {"session_id": session_id, "path": path})["content"]
    router.call(
        "epub_content.write",
        {"session_id": session_id, "path": path, "content": content.replace("Hello world", "Edited text")},
    )
    router.call("epub_content.reorder_spine", {"session_id": session_id, "paths": list(reversed(opened["spine"]))})

    restarted = BridgeRouter()
    register(restarted, cache_root=cache_root)
    restored = restarted.call("epub_content.info", {"session_id": session_id})
    assert restored["dirty"] is True
    assert restored["can_undo"] is True
    assert restored["spine"] == list(reversed(opened["spine"]))
    assert "Edited text" in restarted.call("epub_content.read", {"session_id": session_id, "path": path})["content"]
    restarted.call("epub_content.undo", {"session_id": session_id})
    assert restarted.call("epub_content.info", {"session_id": session_id})["spine"] == opened["spine"]
    restarted.call("epub_content.close", {"session_id": session_id})
    assert not list((cache_root / "epub-editor-sessions").glob("*.json"))
    assert source.read_bytes() == original


def test_checkpoint_restores_draft_without_writing_epub(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    original = source.read_bytes()
    cache_root = tmp_path / "cache"
    router = BridgeRouter()
    register(router, cache_root=cache_root)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    session_id = opened["session_id"]
    path = opened["spine"][0]
    before = router.call("epub_content.read", {"session_id": session_id, "path": path})["content"]
    router.call("epub_content.write", {"session_id": session_id, "path": path, "content": before.replace("Hello world", "Checkpoint text")})
    assert router.call("epub_content.checkpoint", {"session_id": session_id})["dirty"]
    restarted = BridgeRouter()
    register(restarted, cache_root=cache_root)
    assert "Checkpoint text" in restarted.call("epub_content.read", {"session_id": session_id, "path": path})["content"]
    assert source.read_bytes() == original


def test_navigation_source_is_indented_without_changing_archive(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    nav = session.read("OEBPS/nav.xhtml")["content"]
    ncx = session.read("OEBPS/toc.ncx")["content"]
    assert "\n    <nav" in nav
    assert "\n  <navMap>" in ncx
    assert "\n    <navPoint" in ncx
    assert source.read_bytes() == original


def test_css_transform_rules_cover_stylesheets_inline_and_embedded(tmp_path: Path):
    book = tmp_path / "transform-css.epub"
    _book(book)
    _rewrite_book(book, {
        "OEBPS/Styles/book.css": '@media screen {p{color:red!important;font-size:12px}} @supports(display:grid){div{font-size:2em}} @font-face{font-family:Book;src:url(../Fonts/book.ttf)}',
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><style>p{color:red}</style></head><body><p style="color:red;font-size:10px">Hello</p></body></html>',
    })
    session = ContentSession.open(str(book))
    rules = [dict(property="color", operator="equals", match="red", action="set", value="green"), dict(property="font-size", operator="any", action="multiply", value="2")]
    paths = ["OEBPS/Styles/book.css", "OEBPS/Text/one.xhtml"]
    proposal = run_tool(session, "transform_css", dict(rules=rules, paths=paths))
    assert proposal["count"] == 2 and proposal["matched"] == 6
    assert not session.dirty
    run_tool(session, "transform_css", dict(rules=rules, paths=paths, apply=True, fingerprint=proposal["fingerprint"]))
    css = session.read(paths[0])["content"]
    assert 'color:green!important' in css and 'font-size:24px' in css and 'font-size:4em' in css
    assert 'style="font-size:20px;color:green;"' in session.read(paths[1])["content"]
    session.history("undo")
    assert not session.dirty


def test_html_transform_order_wrap_unwrap_tail_and_anchor_safety(tmp_path: Path):
    book = tmp_path / "transform-html.epub"
    _book(book)
    path = "OEBPS/Text/one.xhtml"
    _rewrite_book(book, {path: '<html xmlns="http://www.w3.org/1999/xhtml"><body>Before <span>Hello <em>world</em>!</span> after.<p id="keep">Anchored</p></body></html>'})
    session = ContentSession.open(str(book))
    before = session._bytes(path)
    rules = [dict(selector="span", action="unwrap"), dict(selector="em", action="rename", target="strong"), dict(selector="strong", action="wrap", target="section"), dict(selector="p", action="add_class", value="chapter")]
    preview = run_tool(session, "transform_html", dict(rules=rules, paths=[path]))
    assert preview["count"] == 1 and not session.dirty
    run_tool(session, "transform_html", dict(rules=rules, paths=[path], apply=True, fingerprint=preview["fingerprint"]))
    assert 'Before Hello <section><strong>world</strong></section>! after.' in session._bytes(path).decode()
    assert 'id="keep" class="chapter"' in session.read(path)["content"]
    session.history("undo")
    assert session._bytes(path) == before
    with pytest.raises(ValueError, match="anchor"):
        run_tool(session, "transform_html", dict(rules=[dict(selector="p", action="remove")], paths=[path]))
    assert not session.dirty


@pytest.mark.parametrize("tool,rule", [
    ("transform_html", dict(selector="[", action="rename", target="p")),
    ("transform_html", dict(selector="p", action="set_attr", target="onclick", value="run()")),
    ("transform_html", dict(selector="p", action="wrap", target="script")),
    ("transform_css", dict(property="color", action="set", value="red;color:blue")),
    ("transform_css", dict(property="font-size", action="multiply", value="nan")),
])
def test_transform_invalid_rules_never_mutate(tmp_path: Path, tool, rule):
    book = tmp_path / "transform-invalid.epub"
    _book(book)
    session = ContentSession.open(str(book))
    with pytest.raises(ValueError):
        run_tool(session, tool, dict(rules=[rule], paths=["OEBPS/Text/one.xhtml"]))
    assert not session.dirty


def test_transform_apply_refuses_changed_rules_or_draft(tmp_path: Path):
    book = tmp_path / "transform-stale.epub"
    _book(book)
    session = ContentSession.open(str(book))
    path = "OEBPS/Text/one.xhtml"
    options = dict(rules=[dict(selector="p", action="add_class", value="chapter")], paths=[path])
    proposal = run_tool(session, "transform_html", options)
    with pytest.raises(ValueError, match="changed"):
        run_tool(session, "transform_html", {**options, "rules": [dict(selector="p", action="add_class", value="other")], "apply": True, "fingerprint": proposal["fingerprint"]})
    assert not session.dirty


def test_transform_actions_resources_and_atomic_failure(tmp_path: Path):
    book = tmp_path / "transform-actions.epub"
    _book(book)
    path, css_path = "OEBPS/Text/one.xhtml", "OEBPS/Styles/book.css"
    _rewrite_book(book, {path: '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Untouched</title></head><body><p class="old old keep" title="discard">Hello</p><div><span>remove</span></div></body></html>', css_path: 'p{font-size:12px;color:red;margin-left:20%} @layer book{@keyframes fade{from{opacity:.2}to{opacity:1}}}'})
    session = ContentSession.open(str(book))
    rules = [dict(selector="p", action="remove_class", value="old"), dict(selector="p", action="remove_attr", target="TITLE"), dict(selector="p", action="set_attr", target="href", value="two.xhtml"), dict(selector="div,span", action="remove")]
    proposal = run_tool(session, "transform_html", dict(rules=rules, paths=[path]))
    run_tool(session, "transform_html", dict(rules=rules, paths=[path], apply=True, fingerprint=proposal["fingerprint"]))
    assert 'class="keep" href="two.xhtml"' in session.read(path)["content"]
    assert "remove" not in session.read(path)["content"]
    session.history("undo")
    for rule in [dict(selector="title", action="rename", target="p"), dict(selector="p", action="set_attr", target="src", value="missing.jpg")]:
        with pytest.raises(ValueError):
            run_tool(session, "transform_html", dict(rules=[rule], paths=[path], apply=True))
        assert not session.dirty
    css_rules = [dict(property="color", operator="regex", match="^r.*", action="remove"), dict(property="font-size", action="rename", target="line-height"), dict(property="margin-left", action="add", value="5"), dict(property="opacity", action="multiply", value="2")]
    proposal = run_tool(session, "transform_css", dict(rules=css_rules, paths=[css_path]))
    run_tool(session, "transform_css", dict(rules=css_rules, paths=[css_path], apply=True, fingerprint=proposal["fingerprint"]))
    css = session.read(css_path)["content"]
    assert "color" not in css and "line-height:12px" in css and "margin-left:25%" in css and "opacity:0.4" in css
    session.history("undo")
    _rewrite_book(book, {"OEBPS/Text/two.xhtml": '<html><body><p>Broken</div></body></html>'})
    session = ContentSession.open(str(book))
    with pytest.raises(ValueError, match="Repair"):
        run_tool(session, "transform_html", dict(rules=[dict(selector="p", action="add_class", value="edited")], paths=[path, "OEBPS/Text/two.xhtml"]))
    assert not session.dirty


def test_ignore_markup_search_replace_preserves_tags_and_source_positions(tmp_path: Path):
    book = tmp_path / "text-search.epub"
    _book(book)
    path = "OEBPS/Text/one.xhtml"
    _rewrite_book(book, {path: '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Hello world</title></head><body><p title="Hello world">Hello <em>world</em> &amp; 🌸</p><p>Hello <span>world</span></p></body></html>'})
    session = ContentSession.open(str(book))
    original = session.read(path)["content"]
    hits = session.search("Hello world", [path], ignore_markup=True)
    assert len(hits) == 2
    assert original[hits[0]["start"]:hits[0]["end"]] == 'Hello <em>world'
    proposal = session.preview_replace("Hello (world)", r'Welcome \1 <3', [path], regular_expression=True, ignore_markup=True)
    assert proposal["replacements"] == 2 and not session.dirty
    result = session.replace("Hello (world)", r'Welcome \1 <3', [path], expected_count=2, regular_expression=True, expected_fingerprints=proposal["fingerprints"], ignore_markup=True)
    assert result["replacements"] == 2
    updated = session.read(path)["content"]
    assert '<em/>' in updated and '<span/>' in updated
    assert 'Welcome world &lt;3' in updated
    assert '<title>Hello world</title>' in updated
    session.history("undo")
    assert session.read(path)["content"] == original
    session.replace_match(path, hits[0]["start"], hits[0]["end"], "Hello world", "Hi", expected_fingerprint=hits[0]["fingerprint"], ignore_markup=True)
    assert len(session.search("Hello world", [path], ignore_markup=True)) == 1


def test_ignore_markup_selection_css_and_stale_preview(tmp_path: Path):
    book = tmp_path / "text-scope.epub"
    _book(book)
    session = ContentSession.open(str(book))
    path = "OEBPS/Text/two.xhtml"
    hit = session.search("Hello", [path], ignore_markup=True)[0]
    selection = {"path": path, "start": hit["start"], "end": hit["end"]}
    assert len(session.search("Hello", [path], selection=selection, ignore_markup=True)) == 1
    assert not session.search("red", ["OEBPS/Styles/book.css"], ignore_markup=True)
    preview = session.preview_replace("Hello", "Goodbye", [path], ignore_markup=True)
    session.write(path, session.read(path)["content"].replace("Hello", "Howdy"))
    with pytest.raises(ValueError, match="changed"):
        session.replace("Hello", "Goodbye", [path], expected_fingerprints=preview["fingerprints"], ignore_markup=True)


def test_xpath_toc_hierarchy_targets_preview_and_undo(tmp_path: Path):
    book = tmp_path / "xpath.epub"
    _book(book)
    _rewrite_book(book, {"OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Excluded</title></head><body><p class="chapter">Chapter A</p><h2 id="part">Part one</h2><p>Not a title</p><p class="chapter">Chapter B</p></body></html>'})
    session = ContentSession.open(str(book))
    patterns = ["//h:p[@class='chapter']", "//xhtml:h2"]
    before = session._bytes("OEBPS/Text/one.xhtml")
    result = session.generate_toc(patterns, preview_only=True, source="xpath")
    assert [(item["label"], item["depth"]) for item in result["entries"]] == [("Chapter A", 0), ("Part one", 1), ("Chapter B", 0)]
    assert not session.changes
    session.generate_toc(patterns, source="xpath")
    assert session.toc[1]["href"].endswith("#part")
    assert b'transoria-heading-1' in session._bytes("OEBPS/Text/one.xhtml")
    session.history("undo")
    assert session._bytes("OEBPS/Text/one.xhtml") == before


@pytest.mark.parametrize("expression", ["//*[", "count(//h:h1)", "//h:head", "//h:p/@id", "//h:body", "//unknown:h1"])
def test_xpath_toc_rejects_invalid_or_nonbody_targets(tmp_path: Path, expression: str):
    book = tmp_path / "xpath-invalid.epub"
    _book(book)
    session = ContentSession.open(str(book))
    with pytest.raises(ValueError):
        session.generate_toc([expression], source="xpath")
    assert not session.changes


def test_xpath_toc_handles_unnamespaced_legacy_html(tmp_path: Path):
    book = tmp_path / "xpath-html.epub"
    _book(book)
    _rewrite_book(book, {"OEBPS/Text/one.xhtml": '<html><body><h1 id="legacy">Legacy chapter</h1><p>Text</p></body></html>'})
    session = ContentSession.open(str(book))
    result = session.generate_toc(["//*[local-name()='h1']"], preview_only=True, source="xpath")
    assert result["entries"][0]["label"] == "Legacy chapter"
    assert result["entries"][0]["href"].endswith("#legacy")


def test_regex_toc_preview_and_apply_are_separate(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter 1 Intro</h1><p>Section 1.1 Scene</p><p>Hello.</p></body></html>',
        "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter 2 End</h1></body></html>',
    })
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    previous = list(session.toc)
    patterns = [r"^Chapter (.+)$", r"^Section (.+)$", ""]
    preview = session.generate_toc(patterns, preview_only=True)
    assert [(entry["label"], entry["depth"]) for entry in preview["entries"]] == [
        ("1 Intro", 0), ("1.1 Scene", 1), ("2 End", 0),
    ]
    assert session.toc == previous
    assert not session.dirty
    applied = session.generate_toc(patterns)
    assert applied["generated_entries"] == len(preview["entries"])
    assert session.toc == preview["entries"]
    assert session.dirty
    assert source.read_bytes() == original


def test_regex_toc_rejects_invalid_patterns_without_mutating_session(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    toc_before = list(session.toc)
    source_before = source.read_bytes()
    for patterns, message in [(["["], "regular expression"), ([r"(?=Hello)"], "Zero-width"), ([r"^Missing$"], "matched these patterns")]:
        with pytest.raises(ValueError, match=message):
            session.generate_toc(patterns)
        assert session.toc == toc_before
        assert not session.dirty
        assert source.read_bytes() == source_before


def test_toc_from_reading_order_files_previews_before_apply(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Generic</title></head><body><h1>First chapter</h1></body></html>',
        "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>No heading</p></body></html>',
    })
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    preview = session.generate_toc(source="files", preview_only=True)
    assert preview["entries"] == [
        {"label": "First chapter", "href": "OEBPS/Text/one.xhtml", "depth": 0},
        {"label": "two", "href": "OEBPS/Text/two.xhtml", "depth": 0},
    ]
    assert not session.dirty
    session.generate_toc(source="files")
    assert session.toc == preview["entries"]
    session.history("undo")
    assert [entry["label"] for entry in session.toc] == ["One", "Two"]
    assert source.read_bytes() == original


def test_toc_file_mode_is_forwarded_through_bridge(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    preview = router.call("epub_content.preview_toc", {"session_id": sid, "source": "files"})
    assert [entry["href"] for entry in preview["entries"]] == opened["spine"]
    assert not router.call("epub_content.info", {"session_id": sid})["dirty"]
    applied = router.call("epub_content.generate_toc", {"session_id": sid, "source": "files"})
    assert applied["toc"] == preview["entries"]


def test_toc_spine_save_as_and_source_protection(tmp_path: Path):
    source = tmp_path / "book.epub"
    target = tmp_path / "edited.epub"
    _book(source)
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    one, two = session.spine
    session.reorder_spine([two, one])
    session.set_toc(
        [
            {"label": "Second", "href": two, "depth": 0},
            {"label": "First", "href": one + "#start", "depth": 1},
        ]
    )
    assert session.validate()["structure_check"]["status"] in {"ok", "warning"}
    assert session.dirty
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="Output exists|confirm overwrite"):
        session.save(str(source), overwrite=False)
    assert source.read_bytes() == original
    result = session.save(str(target), overwrite=False)
    assert result["structure_check"]["status"] in {"ok", "warning"}
    assert source.read_bytes() == original
    reopened = ContentSession.open(str(target))
    assert reopened.spine == [two, one]
    assert reopened.toc == session.toc
    with zipfile.ZipFile(target) as archive:
        nav = etree.fromstring(archive.read("OEBPS/nav.xhtml"))
        ncx = etree.fromstring(archive.read("OEBPS/toc.ncx"))
        assert "Second" in "".join(
            nav.xpath("//*[local-name()='nav'][1]//*[local-name()='a']/text()")
        )
        assert "Start" in "".join(
            nav.xpath("//*[local-name()='nav'][2]//*[local-name()='a']/text()")
        )
        assert "First" in "".join(
            ncx.xpath("//*[local-name()='navMap']//*[local-name()='text']/text()")
        )
        assert archive.read("OEBPS/Images/pixel.png") == b"image-bytes"


def test_invalid_toc_and_xml_do_not_write_output(tmp_path: Path):
    source = tmp_path / "book.epub"
    target = tmp_path / "edited.epub"
    _book(source)
    session = ContentSession.open(str(source))
    with pytest.raises(ValueError, match="Invalid table-of-contents"):
        session.set_toc([{"label": "Bad", "href": "missing.xhtml", "depth": 0}])
    with pytest.raises(ValueError, match="target is missing"):
        session.set_toc([{"label": "Bad", "href": session.spine[0] + "#missing", "depth": 0}])
    session.set_toc([{"label": "Start", "href": session.spine[0] + "#start", "depth": 0}])
    session.write(session.spine[0], "<html><bad></html>")
    with pytest.raises(ValueError, match="Invalid XML"):
        session.validate()
    with pytest.raises(ValueError, match="Invalid XML"):
        session.save(str(target), overwrite=False)
    assert not target.exists()


def test_no_change_save_preserves_every_archive_entry(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "unchanged.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.save(str(output), overwrite=False)
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert before.namelist() == after.namelist()
        assert [(info.filename, info.compress_type) for info in before.infolist()] == [
            (info.filename, info.compress_type) for info in after.infolist()
        ]
        assert all(before.read(name) == after.read(name) for name in before.namelist())


def test_one_paragraph_edit_only_changes_its_resource(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    session.write(path, session.read(path)["content"].replace("Hello world.", "Changed paragraph."))
    session.save(str(output), overwrite=False)
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert before.namelist() == after.namelist()
        assert [name for name in before.namelist() if before.read(name) != after.read(name)] == [path]


def test_save_rejects_new_broken_toc_anchor_without_touching_source(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    path = session.spine[0]
    session.set_toc([{"label": "Start", "href": path + "#start", "depth": 0}])
    session.write(path, session.read(path)["content"].replace(' id="start"', ""))
    with pytest.raises(ValueError, match="broken table-of-contents targets"):
        session.save(str(output), overwrite=False)
    assert source.read_bytes() == original
    assert not output.exists()
    assert session.dirty


def test_existing_broken_toc_anchor_does_not_block_unrelated_save(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    with zipfile.ZipFile(source) as book:
        nav = book.read("OEBPS/nav.xhtml")
    _rewrite_book(source, {"OEBPS/nav.xhtml": nav.replace(b'Text/one.xhtml"', b'Text/one.xhtml#old-missing"')})
    session = ContentSession.open(str(source))
    session.save(str(output), overwrite=False)
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert all(before.read(name) == after.read(name) for name in before.namelist())


def test_bridge_serializes_save_and_later_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    session_id = opened["session_id"]
    path = opened["spine"][0]
    original_save = ContentSession.save
    saving = Event()
    release = Event()

    def held_save(session: ContentSession, output_path: str, overwrite: bool):
        saving.set()
        assert release.wait(timeout=5)
        return original_save(session, output_path, overwrite)

    monkeypatch.setattr(ContentSession, "save", held_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        saved = executor.submit(
            router.call,
            "epub_content.save",
            {"session_id": session_id, "output_path": str(output), "overwrite": False},
        )
        assert saving.wait(timeout=5)
        edited = executor.submit(
            router.call,
            "epub_content.write",
            {"session_id": session_id, "path": path, "content": opened["spine"][0] + " edited"},
        )
        time.sleep(0.05)
        assert not edited.done()
        release.set()
        saved.result(timeout=5)
        assert edited.result(timeout=5)["dirty"] is True
    with zipfile.ZipFile(output) as archive:
        assert b" edited" not in archive.read(path)


def test_duplicate_and_unsafe_archive_paths_are_rejected(tmp_path: Path):
    duplicate = tmp_path / "duplicate.epub"
    _book(duplicate)
    with zipfile.ZipFile(duplicate, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("OEBPS/Text/one.xhtml", "<html/>")
    with pytest.raises(ValueError, match="duplicate archive paths"):
        ContentSession.open(str(duplicate))

    unsafe = tmp_path / "unsafe.epub"
    _book(unsafe)
    with zipfile.ZipFile(unsafe, "a") as archive:
        archive.writestr("../outside.txt", "outside")
    with pytest.raises(ValueError, match="unsafe archive paths"):
        ContentSession.open(str(unsafe))


def test_preview_rejects_resources_over_budget_before_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    monkeypatch.setattr(epub_content_module, "MAX_PREVIEW_BYTES", 10)
    with pytest.raises(ValueError, match="Preview resources exceed"):
        session.preview(session.spine[0])


def test_ncx_only_epub_can_edit_toc(tmp_path: Path):
    source = tmp_path / "legacy.epub"
    target = tmp_path / "legacy-edited.epub"
    _book(source)
    with zipfile.ZipFile(source) as book:
        entries = [(info, book.read(info.filename)) for info in book.infolist()]
    with zipfile.ZipFile(source, "w") as book:
        for info, data in entries:
            if info.filename == "OEBPS/book.opf":
                data = data.replace(b' properties="nav"', b"").replace(b'version="3.0"', b'version="2.0"')
            book.writestr(info, data)
    session = ContentSession.open(str(source))
    assert session.nav_path == ""
    assert [entry["label"] for entry in session.toc] == ["One", "Two"]
    session.set_toc([{"label": "Legacy chapter", "href": session.spine[0], "depth": 0}])
    session.save(str(target), overwrite=False)
    assert ContentSession.open(str(target)).toc[0]["label"] == "Legacy chapter"


def test_source_changed_and_bridge_errors(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed on disk"):
        session.save(str(tmp_path / "edited.epub"), overwrite=False)

    router = BridgeRouter()
    register(router)
    with pytest.raises(BridgeError):
        router.call("epub_content.read", {"session_id": "missing", "path": "x"})


@pytest.mark.parametrize(
    ("query", "replacement", "case_sensitive", "expected"),
    [
        ("Hello", "New", False, 3),
        ("hello", "New", True, 1),
        ("a.b", r"\\1$", False, 2),
        ("🌸", "", False, 2),
    ],
)
def test_search_replace_multiple_files_and_literal_text(
    tmp_path: Path, query: str, replacement: str, case_sensitive: bool, expected: int,
):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>🌸 Hello hello a.b 🌸</p></body></html>',
        "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello a.b</p></body></html>',
    })
    original = source.read_bytes()
    session = ContentSession.open(str(source))
    paths = session.spine
    found = session.search(query, paths, case_sensitive)
    assert len(found) == expected
    for match in found:
        content = session.read(match["path"])["content"]
        assert content[match["start"]:match["end"]].casefold() == query.casefold()
    with pytest.raises(ValueError, match="Search results changed"):
        session.replace(query, replacement, paths, case_sensitive, expected + 1)
    assert not session.dirty
    result = session.replace(query, replacement, paths, case_sensitive, expected)
    assert result["replacements"] == expected
    assert len(session.search(query, paths, case_sensitive)) == 0
    session.history("undo")
    assert len(session.search(query, paths, case_sensitive)) == expected
    session.history("redo")
    assert source.read_bytes() == original


def test_single_match_requires_current_offsets_and_keeps_other_files(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    one, two = session.spine
    first = session.search("Hello", [one, two])[0]
    session.replace_match(one, first["start"], first["end"], "Hello", "Done")
    assert "Done world" in session.read(one)["content"]
    assert "Hello again" in session.read(two)["content"]
    with pytest.raises(ValueError, match="no longer current"):
        session.replace_match(one, first["start"], first["end"], "Hello", "Again")


def test_single_match_rejects_stale_file_with_same_match_offsets(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    found = session.search("Hello", [path])[0]
    changed = session.read(path)["content"].replace("world.", "earth.")
    session.write(path, changed)
    before = session._snapshot()
    with pytest.raises(ValueError, match="Search result changed"):
        session.replace_match(
            path, found["start"], found["end"], "Hello", "Hi",
            expected_fingerprint=found["fingerprint"],
        )
    assert session._snapshot() == before


def test_duplicate_paths_and_truncated_search_cannot_partially_replace(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    one = session.spine[0]
    assert len(session.search("Hello", [one, one])) == 1
    assert session.replace("Hello", "Hi", [one, one], expected_count=1)["replacements"] == 1
    session.history("undo")
    content = session.read(one)["content"].replace("Hello world.", "x " * 5001)
    session.write(one, content)
    assert len(session.search("x", [one])) == 5000
    with pytest.raises(ValueError, match="Search results changed"):
        session.replace("x", "y", [one], expected_count=5000)
    assert "y " not in session.read(one)["content"]


def test_oversized_single_and_batch_replacements_leave_all_files_unchanged(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    one, two = session.spine
    before = session._snapshot()
    oversized = "x" * epub_content_module.MAX_TEXT_BYTES

    match = session.search("Hello", [one])[0]
    with pytest.raises(ValueError, match="Replacement exceeds"):
        session.replace_match(one, match["start"], match["end"], "Hello", oversized)
    assert session._snapshot() == before

    with pytest.raises(ValueError, match="Replacement exceeds"):
        session.replace("Hello", oversized, [one, two], expected_count=2)
    assert session._snapshot() == before
    assert "Hello world" in session.read(one)["content"]
    assert "Hello again" in session.read(two)["content"]


def test_regex_search_and_group_replacement_across_files(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    one, two = session.spine
    matches = session.search(r"Hello\s+(world|again)", [one, two], regular_expression=True)
    assert [(match["path"], session.read(match["path"])["content"][match["start"]:match["end"]]) for match in matches] == [
        (one, "Hello world"), (two, "Hello again"),
    ]
    result = session.replace(
        r"Hello\s+(world|again)", r"Greetings \1", [one, two],
        expected_count=2, regular_expression=True,
    )
    assert result == {"replacements": 2, "files_changed": 2}
    assert "Greetings world" in session.read(one)["content"]
    assert "Greetings again" in session.read(two)["content"]
    session.history("undo")
    assert "Hello world" in session.read(one)["content"]
    assert "Hello again" in session.read(two)["content"]


def test_regex_replace_one_and_invalid_patterns_are_atomic(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    one, two = session.spine
    match = session.search(r"Hello (world)", [one], regular_expression=True)[0]
    session.replace_match(
        one, match["start"], match["end"], r"Hello (world)",
        r"Goodbye \1", regular_expression=True,
    )
    assert "Goodbye world" in session.read(one)["content"]
    assert "Hello again" in session.read(two)["content"]
    session.history("undo")
    with pytest.raises(ValueError, match="Invalid regular expression"):
        session.search("(", [one], regular_expression=True)
    with pytest.raises(ValueError, match="Invalid replacement expression"):
        session.replace(r"Hello (world|again)", r"\2", [one, two], regular_expression=True)
    with pytest.raises(ValueError, match="Zero-width"):
        session.search(r"(?=Hello)", [one], regular_expression=True)
    assert not session.dirty
    assert "Hello world" in session.read(one)["content"]
    assert "Hello again" in session.read(two)["content"]


def test_regex_bridge_calls_and_literal_compatibility(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    paths = opened["spine"]
    regex_matches = router.call("epub_content.search", {
        "session_id": sid, "query": r"Hello (world|again)", "paths": paths,
        "regular_expression": True,
    })["matches"]
    assert len(regex_matches) == 2
    assert router.call("epub_content.search", {
        "session_id": sid, "query": r"Hello (world|again)", "paths": paths,
    })["matches"] == []
    changed = router.call("epub_content.replace", {
        "session_id": sid, "query": r"Hello (world|again)", "replacement": r"Hi \1",
        "paths": paths, "expected_count": 2, "regular_expression": True,
    })
    assert changed["replacements"] == 2
    assert "Hi world" in router.call("epub_content.read", {"session_id": sid, "path": paths[0]})["content"]


def test_selection_scope_and_replacement_preview_share_exact_plan(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    content = session.read(path)["content"]
    start = content.index("Hello")
    selection = {"path": path, "start": start, "end": start + len("Hello world")}
    matches = session.search(r"Hello (world)", [path], regular_expression=True, selection=selection)
    assert len(matches) == 1 and matches[0]["start"] == start
    preview = session.preview_replace(
        r"Hello (world)", r"Hi \1", [path], regular_expression=True,
        selection=selection,
    )
    assert preview["replacements"] == 1
    assert preview["samples"] == [{"path": path, "start": start, "before": "Hello world", "after": "Hi world"}]
    assert not session.dirty
    result = session.replace(
        r"Hello (world)", r"Hi \1", [path], expected_count=1,
        regular_expression=True, selection=selection,
        expected_fingerprints=preview["fingerprints"],
    )
    assert result["replacements"] == 1
    assert "Hi world" in session.read(path)["content"]
    session.history("undo")
    assert "Hello world" in session.read(path)["content"]


def test_selection_regex_lookbehind_matches_and_replaces_with_full_context(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    content = session.read(path)["content"]
    start = content.index("world")
    selection = {"path": path, "start": start, "end": start + len("world")}
    query = r"(?<=Hello )world"
    assert len(session.search(query, [path], regular_expression=True, selection=selection)) == 1
    preview = session.preview_replace(query, "planet", [path], regular_expression=True, selection=selection)
    assert preview["replacements"] == 1
    assert preview["samples"][0]["start"] == start
    assert "Hello world" in session.read(path)["content"]
    session.replace(query, "planet", [path], expected_count=1, regular_expression=True,
                    selection=selection, expected_fingerprints=preview["fingerprints"])
    assert "Hello planet" in session.read(path)["content"]


def test_replacement_preview_rejects_stale_file_and_invalid_selection(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    session = ContentSession.open(str(source))
    path = session.spine[0]
    preview = session.preview_replace("Hello", "Hi", [path])
    session.write(path, session.read(path)["content"].replace("Hello", "HELLO"))
    before = session._snapshot()
    with pytest.raises(ValueError, match="preview replacement again"):
        session.replace("Hello", "Hi", [path], expected_count=1,
                        expected_fingerprints=preview["fingerprints"])
    assert session._snapshot() == before
    with pytest.raises(ValueError, match="Selected text changed"):
        session.search("HELLO", [path], selection={"path": path, "start": 9999, "end": 10000})


def test_regex_timeout_leaves_session_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
        + "a" * 5000 + "X</p></body></html>",
    })
    session = ContentSession.open(str(source))
    monkeypatch.setattr(epub_content_module, "SEARCH_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ValueError, match="timed out"):
        session.search(r"(a+)+$", [session.spine[0]], regular_expression=True)
    with pytest.raises(ValueError, match="timed out"):
        session.replace(r"(a+)+$", "b", [session.spine[0]], regular_expression=True)
    assert not session.dirty
    assert session.changes == {}


def test_generate_toc_from_headings_is_one_undoable_operation(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<h1>First chapter</h1><h2 id="part">First part</h2></body></html>',
        "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<h1>Second chapter</h1></body></html>',
    })
    session = ContentSession.open(str(source))
    original_toc = session.toc.copy()
    assert session.generate_toc() == {"generated_entries": 3, "approximate_targets": 0}
    assert [(entry["label"], entry["depth"]) for entry in session.toc] == [
        ("First chapter", 0), ("First part", 1), ("Second chapter", 0),
    ]
    assert "#transoria-heading-1" in session.toc[0]["href"]
    assert "#part" in session.toc[1]["href"]
    assert "transoria-heading-1" in session.read(session.spine[0])["content"]
    history_depth = len(session.undo_stack)
    assert session.generate_toc() == {"generated_entries": 3, "approximate_targets": 0}
    assert len(session.undo_stack) == history_depth
    session.history("undo")
    assert session.toc == original_toc
    assert "transoria-heading-1" not in session.read(session.spine[0])["content"]
    session.history("redo")
    session.save(str(output), overwrite=False)
    reopened = ContentSession.open(str(output))
    assert reopened.toc == session.toc


def test_generate_toc_bridge_returns_updated_session_and_survives_save(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<h1>Bridge chapter</h1></body></html>',
    })
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    generated = router.call("epub_content.generate_toc", {"session_id": sid})
    assert generated["generated_entries"] == 1
    assert generated["approximate_targets"] == 0
    assert generated["toc"][0]["label"] == "Bridge chapter"
    assert generated["dirty"]
    saved = router.call("epub_content.save", {
        "session_id": sid, "output_path": str(output), "overwrite": False,
    })
    assert saved["output_path"] == str(output)
    assert ContentSession.open(str(output)).toc[0]["label"] == "Bridge chapter"


def test_generate_toc_recovers_malformed_chapter_without_rewriting_it(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>First</h1></body></html>',
        "OEBPS/Text/two.xhtml": '<html><body><h1>Broken</body></html>',
    })
    session = ContentSession.open(str(source))
    malformed = session._bytes(session.spine[1])
    original_toc = session.toc.copy()
    assert session.generate_toc() == {"generated_entries": 2, "approximate_targets": 1}
    assert session.toc[1]["href"] == session.spine[1]
    assert session._bytes(session.spine[1]) == malformed
    assert "transoria-heading-1" in session.read(session.spine[0])["content"]
    session.history("undo")
    assert session.toc == original_toc
    assert session._bytes(session.spine[1]) == malformed
    assert not session.dirty


def test_resource_add_move_and_delete_preserve_links_after_reopen(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    session = ContentSession.open(str(source))
    session.add_resource("OEBPS/Styles/base.css", b"p { color: blue; }")
    session.write("OEBPS/Styles/book.css", '@import "base.css"; body { background: url(../Images/pixel.png); }')
    assert session.rename_resource(
        "OEBPS/Styles/book.css", "OEBPS/Assets/Styles/book.css",
    )["files_changed"] >= 3
    chapter = session.read("OEBPS/Text/one.xhtml")["content"]
    assert '../Assets/Styles/book.css' in chapter
    css = session.read("OEBPS/Assets/Styles/book.css")["content"]
    assert '../../Styles/base.css' in css
    assert '../../Images/pixel.png' in css
    assert "OEBPS/Styles/book.css" not in {item["path"] for item in session.files}
    session.save(str(output), overwrite=False)
    reopened = ContentSession.open(str(output))
    assert '../Assets/Styles/book.css' in reopened.read("OEBPS/Text/one.xhtml")["content"]
    assert '../../Images/pixel.png' in reopened.read("OEBPS/Assets/Styles/book.css")["content"]
    assert "OEBPS/Styles/book.css" not in {item["path"] for item in reopened.files}
    assert reopened.resource_references("OEBPS/Styles/base.css") == ["OEBPS/Assets/Styles/book.css"]
    with pytest.raises(ValueError, match="still referenced"):
        reopened.delete_resource("OEBPS/Styles/base.css")
    assert not reopened.dirty


def test_resource_operation_undo_and_session_restore(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    store = ContentSessionStore(tmp_path / "sessions")
    opened = store.open(str(source))
    sid = opened["session_id"]
    session = store.get(sid)
    session.add_resource("OEBPS/Styles/unused.css", b"p { color: red; }")
    store.persist(sid)
    restored = ContentSessionStore(tmp_path / "sessions").get(sid)
    assert "OEBPS/Styles/unused.css" in {item["path"] for item in restored.files}
    restored.delete_resource("OEBPS/Styles/unused.css")
    assert "OEBPS/Styles/unused.css" not in {item["path"] for item in restored.files}
    restored.history("undo")
    assert "OEBPS/Styles/unused.css" in {item["path"] for item in restored.files}
    restored.history("undo")
    assert "OEBPS/Styles/unused.css" not in {item["path"] for item in restored.files}
    assert not restored.dirty


def test_resource_rename_percent_and_fragment_filename(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    session = ContentSession.open(str(source))
    target = "OEBPS/Images/新 图#50%.png"
    session.rename_resource("OEBPS/Images/pixel.png", target)
    assert "../Images/%E6%96%B0%20%E5%9B%BE%2350%25.png" in session.read(session.spine[0])["content"]
    session.save(str(output), overwrite=False)
    with zipfile.ZipFile(output) as archive:
        assert target in archive.namelist()
        assert "OEBPS/Images/pixel.png" not in archive.namelist()
    reopened = ContentSession.open(str(output))
    assert target in {item["path"] for item in reopened.files}


def test_resource_import_into_spine_replace_export_and_delete(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    exported = tmp_path / "image.png"
    _book(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    session = ContentSession.open(str(source))
    extra = "OEBPS/Text/extra.xhtml"
    session.add_resource(extra, b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Extra</p></body></html>', in_spine=True)
    session.replace_resource("OEBPS/Images/pixel.png", b"new-image")
    assert "bmV3LWltYWdl" in session.preview(session.spine[0])
    session.export_resource("OEBPS/Images/pixel.png", str(exported), False)
    assert exported.read_bytes() == b"new-image"
    with pytest.raises(ValueError, match="exists"):
        session.export_resource("OEBPS/Images/pixel.png", str(exported), False)
    session.add_resource("OEBPS/Styles/temporary.css", b"p { color: red; }")
    session.delete_resource("OEBPS/Styles/temporary.css")
    session.save(str(output), overwrite=False)
    reopened = ContentSession.open(str(output))
    assert reopened.spine[-1] == extra
    assert reopened.read(extra)["content"].find("Extra") > -1
    with zipfile.ZipFile(output) as archive:
        assert archive.read("OEBPS/Images/pixel.png") == b"new-image"
        assert "OEBPS/Styles/temporary.css" not in archive.namelist()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_resource_rename_failure_keeps_session_unchanged(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Styles/book.css": "body { background: url(../missing.png); }"})
    session = ContentSession.open(str(source))
    before = session._snapshot()
    with pytest.raises(ValueError, match="unresolved link"):
        session.rename_resource("OEBPS/Styles/book.css", "OEBPS/Assets/book.css")
    assert session._snapshot() == before
    assert not session.dirty


def test_resource_bridge_round_trip(tmp_path: Path):
    source = tmp_path / "book.epub"
    local = tmp_path / "new.css"
    output = tmp_path / "edited.epub"
    _book(source)
    local.write_bytes(b"p { color: blue; }")
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    added = router.call("epub_content.add_resource", {
        "session_id": sid, "input_path": str(local), "path": "OEBPS/Styles/new.css",
    })
    assert added["dirty"] and added["files"][-1]["path"] == "OEBPS/Styles/new.css"
    assert router.call("epub_content.references", {
        "session_id": sid, "path": "OEBPS/Styles/new.css",
    })["inbound"] == []
    saved = router.call("epub_content.save", {
        "session_id": sid, "output_path": str(output), "overwrite": False,
    })
    assert saved["output_path"] == str(output)
    assert ContentSession.open(str(output)).read("OEBPS/Styles/new.css")["content"] == "p { color: blue; }"


def test_toc_anchor_picker_reads_current_draft_without_mutation(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    router = BridgeRouter()
    register(router)
    opened = router.call("epub_content.open", {"input_path": str(source)})
    sid = opened["session_id"]
    path = opened["spine"][0]
    assert router.call("epub_content.anchors", {"session_id": sid, "path": path})["anchors"] == [
        {"id": "start", "label": "Hello world."},
    ]
    content = router.call("epub_content.read", {"session_id": sid, "path": path})["content"]
    router.call("epub_content.write", {"session_id": sid, "path": path, "content": content.replace(
        '<p id="start">', '<p id="start">',
    ).replace("Hello world.", 'Hello world.</p><h2 id="next">Next part</h2><p>')})
    anchors = router.call("epub_content.anchors", {"session_id": sid, "path": path})["anchors"]
    assert anchors[-1] == {"id": "next", "label": "Next part"}
    router.call("epub_content.set_toc", {"session_id": sid, "entries": [
        {"label": "Next part", "href": path + "#next", "depth": 0},
    ]})
    assert router.call("epub_content.info", {"session_id": sid})["toc"][0]["href"] == path + "#next"
    with pytest.raises(BridgeError, match="XHTML or HTML"):
        router.call("epub_content.anchors", {"session_id": sid, "path": "OEBPS/Styles/book.css"})


def test_toc_anchor_picker_recovers_malformed_html_and_skips_duplicate_ids(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": b'<html><body><h2 id="a">One<h2 id="a">Two</body></html>'})
    session = ContentSession.open(str(source))
    assert session.anchors(session.spine[0]) == [{"id": "a", "label": "OneTwo"}]
    assert not session.dirty


def test_generate_in_book_toc_page_is_reversible_and_preserves_nav(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    with zipfile.ZipFile(source) as archive:
        original_nav = archive.read("OEBPS/nav.xhtml")
        original_ncx = archive.read("OEBPS/toc.ncx")
    session = ContentSession.open(str(source))
    generated = session.generate_toc_page("Contents")
    assert generated == "OEBPS/Text/transoria_contents.xhtml"
    assert session.spine[0] == generated
    assert "../Text/one.xhtml" not in session.read(generated)["content"]
    assert "one.xhtml" in session.read(generated)["content"]
    assert len(session.undo_stack) == 1
    with pytest.raises(ValueError, match="already exists"):
        session.generate_toc_page("Contents")
    session.history("undo")
    assert generated not in {file["path"] for file in session.files}
    session.history("redo")
    session.save(str(output), overwrite=False)
    reopened = ContentSession.open(str(output))
    assert reopened.spine[0] == generated
    with zipfile.ZipFile(output) as archive:
        assert archive.read("OEBPS/nav.xhtml") == original_nav
        assert archive.read("OEBPS/toc.ncx") == original_ncx


def test_spine_membership_and_linear_flag_preserve_resource_and_itemref(tmp_path: Path):
    source = tmp_path / "book.epub"
    output = tmp_path / "edited.epub"
    _book(source)
    with zipfile.ZipFile(source) as archive:
        opf = archive.read("OEBPS/book.opf")
    _rewrite_book(source, {"OEBPS/book.opf": opf.replace(
        b'<itemref idref="two"/>', b'<itemref idref="two" linear="no" id="vendor-ref"/>',
    )})
    session = ContentSession.open(str(source))
    one, two = session.spine
    assert not session.spine_linear[two]
    session.reorder_spine([two, one])
    session.save(str(output), overwrite=False)
    with zipfile.ZipFile(output) as archive:
        spine = etree.fromstring(archive.read("OEBPS/book.opf")).find(".//{http://www.idpf.org/2007/opf}spine")
        assert [item.get("idref") for item in spine] == ["two", "one"]
        assert spine[0].get("linear") == "no" and spine[0].get("id") == "vendor-ref"
    session.set_spine([{"path": one, "linear": True}])
    session.save(str(output), overwrite=True)
    reopened = ContentSession.open(str(output))
    assert reopened.spine == [one]
    assert two in {item["path"] for item in reopened.files}
    assert reopened.toc[-1]["href"] == two
    reopened.set_spine([{"path": one, "linear": True}, {"path": two, "linear": False}])
    reopened.save(str(output), overwrite=True)
    assert ContentSession.open(str(output)).spine_linear[two] is False


def test_malformed_xhtml_preview_recovers_without_changing_source(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    malformed = b"<html><head><style>p { color: teal }</style></head><body><div><p>Visible text</p></body></html>"
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": malformed})
    session = ContentSession.open(str(source))
    preview = session.preview(session.spine[0])
    assert "Visible text" in preview
    assert "color: teal" in preview
    assert session._bytes(session.spine[0]) == malformed
    assert not session.dirty


def test_preview_inlines_svg_cover_images_with_xlink_and_href(tmp_path: Path):
    source = tmp_path / "svg-cover.epub"
    _book(source)
    cover = '''<html xmlns="http://www.w3.org/1999/xhtml"><body>
      <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 960 1440">
        <image xlink:href="../Images/pixel.png" width="960" height="1440"/>
        <image href="../Images/pixel.png" width="960" height="1440"/>
        <image xlink:href="https://example.com/untrusted.png"/>
      </svg></body></html>'''
    _rewrite_book(source, {"OEBPS/Text/one.xhtml": cover})
    session = ContentSession.open(str(source))
    preview = session.preview(session.spine[0])
    expected = f"data:image/png;base64,{base64.b64encode(b'image-bytes').decode('ascii')}"
    assert preview.count(expected) == 2
    assert "../Images/pixel.png" not in preview
    assert "https://example.com" not in preview
    assert not session.dirty


def test_nonstandard_html_and_failed_encoding_leave_edits_atomic(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    with zipfile.ZipFile(source) as book:
        opf = book.read("OEBPS/book.opf")
    opf = opf.replace(b'href="Text/two.xhtml" media-type="application/xhtml+xml"', b'href="Text/two.xhtml" media-type="text/html"')
    _rewrite_book(source, {
        "OEBPS/book.opf": opf,
        "OEBPS/Text/one.xhtml": b'<?xml version="1.0" encoding="iso-8859-1"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>caf\xe9</p></body></html>',
        "OEBPS/Text/two.xhtml": b'<html><body><p style="color:purple">Unclosed <b>bold</p></body></html>',
    })
    session = ContentSession.open(str(source))
    assert "Unclosed" in session.preview(session.spine[1])
    assert "color:purple" in session.preview(session.spine[1])
    with pytest.raises(ValueError, match="cannot be encoded"):
        session.replace("caf\u00e9", "\u6c49", [session.spine[0]])
    assert not session.dirty
    assert "caf\u00e9" in session.read(session.spine[0])["content"]


def test_preview_local_css_imports_inline_styles_and_obfuscated_font(tmp_path: Path):
    source = tmp_path / "book.epub"
    _book(source)
    font = b"OTTO" + bytes(range(256)) * 5
    key = hashlib.sha1(b"test").digest()
    obfuscated = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(font[:1040])) + font[1040:]
    with zipfile.ZipFile(source) as book:
        opf = book.read("OEBPS/book.opf")
    opf = opf.replace(b"</manifest>", b'<item id="font" href="Fonts/book.otf" media-type="font/otf"/><item id="extra" href="Styles/extra.css" media-type="text/css"/></manifest>')
    encryption = b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><enc:CipherData><enc:CipherReference URI="OEBPS/Fonts/book.otf"/></enc:CipherData></enc:EncryptedData></encryption>'''
    _rewrite_book(source, {
        "OEBPS/book.opf": opf,
        "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="../Styles/book.css"/></head><body><p style="background:url(../Images/pixel.png)">Styled</p></body></html>',
        "OEBPS/Styles/book.css": '@import url("extra.css") screen; p { font-family: TestFont; }',
        "OEBPS/Styles/extra.css": '@import "book.css"; @font-face { font-family: TestFont; src: url("../Fonts/book.otf"); }',
        "OEBPS/Fonts/book.otf": obfuscated,
        "META-INF/encryption.xml": encryption,
    })
    session = ContentSession.open(str(source))
    preview = session.preview(session.spine[0])
    imported = base64.b64decode(preview.partition('data:text/css;base64,')[2].partition('"')[0]).decode()
    assert "@font-face" in imported and "font-family: TestFont" in preview
    assert ') screen;' in preview
    assert f"data:font/otf;base64,{base64.b64encode(font).decode('ascii')}" in imported
    assert "data:image/png;base64," in preview
    assert "@import" not in imported
    assert obfuscated == session._bytes("OEBPS/Fonts/book.otf")
