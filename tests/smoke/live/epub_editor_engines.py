"""Opt-in local engine checks using generated documents and temporary outputs."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import tempfile
import zipfile
from pathlib import Path

from transoria.tools.epub_content import ContentSession
from transoria.tools.epub_editor_tools import external_check, import_book, upgrade_epub


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--converter", required=True)
    parser.add_argument("--epubcheck", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="epub-engine-smoke-") as directory:
        root = Path(directory)
        inputs = {
            "txt": "Chapter One\n\nSynthetic engine test paragraph.\n\nChapter Two\n\nAnother paragraph.",
            "html": '<html><head><title>Engine test</title></head><body><h1>Chapter One</h1><p>Synthetic engine test paragraph.</p></body></html>',
            "rtf": r"{\rtf1\ansi Chapter One\par Synthetic engine test paragraph.\par}",
            "fb2": '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><genre>prose</genre><author><first-name>Test</first-name><last-name>Author</last-name></author><book-title>Engine test</book-title><lang>en</lang></title-info><document-info><author><first-name>Test</first-name><last-name>Author</last-name></author><date>2026-09-30</date><id>synthetic-test</id><version>1.0</version></document-info></description><body><section><title><p>Chapter One</p></title><p>Synthetic engine test paragraph.</p></section></body></FictionBook>',
        }
        for suffix, text in inputs.items():
            (root / f"input.{suffix}").write_text(text, encoding="utf-8")
        docx = root / "input.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic engine test paragraph.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
        odt = root / "input.odt"
        with zipfile.ZipFile(odt, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/manifest.xml", '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
            archive.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2"><office:body><office:text><text:p>Synthetic engine test paragraph.</text:p></office:text></office:body></office:document-content>')
            archive.writestr("meta.xml", '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.2"><office:meta><dc:title>Engine test</dc:title><dc:language>en</dc:language></office:meta></office:document-meta>')
            archive.writestr("styles.xml", '<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" office:version="1.2"><office:styles/></office:document-styles>')
        for suffix in ("mobi", "azw3"):
            generated = subprocess.run([args.converter, str(root / "input.html"), str(root / f"input.{suffix}")], capture_output=True, text=True, timeout=300, check=False)
            assert generated.returncode == 0, generated.stderr or generated.stdout
        for suffix in [*inputs, "docx", "odt", "mobi", "azw3"]:
            source = root / f"input.{suffix}"
            original = hashlib.sha256(source.read_bytes()).digest()
            output = import_book(str(source), args.converter, root / "cache")
            session = ContentSession.open(str(output))
            assert session.spine
            assert any("Synthetic engine test paragraph" in session.preview(path) for path in session.spine)
            assert hashlib.sha256(source.read_bytes()).digest() == original
            saved = root / f"saved-{suffix}.epub"
            session.save(str(saved), overwrite=False)
            assert ContentSession.open(str(saved)).spine == session.spine
            if args.epubcheck:
                check = external_check(session, args.epubcheck)
                assert check["exit_code"] == 0, check["output"]
                session.write(session.spine[0], session.read(session.spine[0])["content"].replace("</body>", '<p><img src="missing-image.png" alt="test"/></p></body>'))
                invalid = external_check(session, args.epubcheck)
                assert invalid["exit_code"] != 0 and "missing-image.png" in invalid["output"]
            print(f"PASS {suffix.upper()}: import, preview, save/reopen, source preserved", flush=True)
        legacy = root / "legacy.epub"
        converted = subprocess.run([args.converter, str(root / "input.html"), str(legacy), "--epub-version", "2"], capture_output=True, text=True, timeout=300, check=False)
        assert converted.returncode == 0, converted.stderr or converted.stdout
        session = ContentSession.open(str(legacy))
        if len(session.spine) > 1:
            original = session._snapshot()
            session.merge_resources(session.spine.copy())
            if args.epubcheck:
                merged = external_check(session, args.epubcheck)
                assert merged["exit_code"] == 0, merged["output"]
            session.history("undo")
            assert session._snapshot() == original
            print(f"PASS EPUB 2 merge: {'EPUBCheck and ' if args.epubcheck else ''}undo", flush=True)
        before = session._snapshot()
        upgrade_epub(session)
        if args.epubcheck:
            checked = external_check(session, args.epubcheck)
            assert checked["exit_code"] == 0, checked["output"]
        session.history("undo")
        assert session._snapshot() == before
        print(f"PASS EPUB 2 upgrade: {'EPUBCheck and ' if args.epubcheck else ''}undo", flush=True)


if __name__ == "__main__":
    main()
