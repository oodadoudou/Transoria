from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.epub_content import register
from transoria.tools.epub_content import ContentSession, ContentSessionStore


def _book(path: Path) -> None:
    container = """<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"""
    opf = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">test</dc:identifier><dc:title>Book</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="one" href="Text/one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="Text/two.xhtml" media-type="application/xhtml+xml"/><item id="css" href="Styles/book.css" media-type="text/css"/><item id="img" href="Images/pixel.png" media-type="image/png"/></manifest><spine toc="ncx"><itemref idref="one"/><itemref idref="two"/></spine></package>"""
    nav = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="Text/one.xhtml">One</a></li><li><a href="Text/two.xhtml">Two</a></li></ol></nav><nav epub:type="landmarks"><ol><li><a href="Text/one.xhtml">Start</a></li></ol></nav></body></html>"""
    ncx = """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>Book</text></docTitle><navMap><navPoint id="one" playOrder="1"><navLabel><text>One</text></navLabel><content src="Text/one.xhtml"/></navPoint><navPoint id="two" playOrder="2"><navLabel><text>Two</text></navLabel><content src="Text/two.xhtml"/></navPoint></navMap></ncx>"""
    one = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="../Styles/book.css"/></head><body><p>Hello world.</p><img src="../Images/pixel.png"/><script>alert(1)</script><a href="https://example.com">Outside</a></body></html>"""
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
    session.write(session.spine[0], "<html><bad></html>")
    with pytest.raises(ValueError, match="Invalid XML"):
        session.validate()
    with pytest.raises(ValueError, match="Invalid XML"):
        session.save(str(target), overwrite=False)
    assert not target.exists()


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
