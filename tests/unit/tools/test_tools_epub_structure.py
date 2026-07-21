from __future__ import annotations

from pathlib import Path
import zipfile

from transoria.tools.epub_structure import inspect_epub_structure


def test_structure_check_reports_missing_navigation_and_css_references(tmp_path: Path):
    epub = tmp_path / "broken-links.epub"
    container = """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>"""
    opf = """<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="css" href="style.css" media-type="text/css"/></manifest><spine><itemref idref="chapter"/></spine></package>"""
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/Text/chapter.xhtml", "<html><body><img src='../Images/missing.jpg'/></body></html>")
        archive.writestr("OEBPS/nav.xhtml", "<html><body><nav><a href='Text/missing.xhtml'>Missing</a></nav></body></html>")
        archive.writestr("OEBPS/style.css", "@font-face{src:url('Fonts/missing.woff2')}")

    check = inspect_epub_structure(epub)

    assert check["status"] == "warning"
    assert check["counts"]["spine"] == 1
    assert check["counts"]["body_documents"] == 2
    assert check["counts"]["nav_links"] == 1
    assert check["counts"]["references_checked"] == 3
    assert check["missing_entries"] == [
        "OEBPS/Fonts/missing.woff2",
        "OEBPS/Images/missing.jpg",
        "OEBPS/Text/missing.xhtml",
    ]
