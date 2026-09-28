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
    assert "@font-face" in preview and "font-family: TestFont" in preview
    assert "@media screen" in preview
    assert f"data:font/otf;base64,{base64.b64encode(font).decode('ascii')}" in preview
    assert "data:image/png;base64," in preview
    assert "@import" not in preview
    assert obfuscated == session._bytes("OEBPS/Fonts/book.otf")
