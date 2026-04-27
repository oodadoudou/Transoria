from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile

from transoria.formats.epub_parser import EpubTextKind, parse_epub_file


class EpubDocumentTests(TestCase):
    def test_parse_epub_file_reads_package_info_and_spine_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(Path(temp_dir) / "book.epub")

            document = parse_epub_file(epub_path)

        self.assertEqual(document.package.opf_path, "OEBPS/content.opf")
        self.assertEqual(document.package.opf_version_major, 2)
        self.assertEqual(document.package.spine_paths, ["OEBPS/Text/chapter.xhtml"])
        self.assertEqual(document.package.ncx_path, "OEBPS/toc.ncx")

    def test_parse_epub_file_extracts_plain_text_segments_without_tags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(Path(temp_dir) / "book.epub")

            document = parse_epub_file(epub_path)

        body_segments = [segment for segment in document.segments if segment.kind == EpubTextKind.BODY]
        self.assertEqual([segment.text for segment in body_segments], ["첫 문장\n강조\n끝", "둘째 문장"])
        self.assertTrue(all("<" not in segment.text and ">" not in segment.text for segment in body_segments))
        self.assertTrue(all(len(segment.source_digest) == 40 for segment in body_segments))
        self.assertEqual([part.slot for part in body_segments[0].parts], ["text", "text", "tail"])

    def test_parse_epub_file_extracts_opf_title_and_ncx_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(Path(temp_dir) / "book.epub")

            document = parse_epub_file(epub_path)

        by_kind = {segment.kind: [] for segment in document.segments}
        for segment in document.segments:
            by_kind[segment.kind].append(segment.text)

        self.assertIn("책 제목", by_kind[EpubTextKind.OPF_TITLE])
        self.assertIn("목차 제목", by_kind[EpubTextKind.NCX])
        self.assertIn("1장", by_kind[EpubTextKind.NCX])

    def test_parse_real_epub_fixture_extracts_spine_text(self) -> None:
        source = next(Path("test/test-files").glob("*.epub"))

        document = parse_epub_file(source)

        self.assertEqual(document.package.opf_path, "OEBPS/content.opf")
        self.assertGreaterEqual(len(document.package.spine_paths), 5)
        self.assertGreater(len(document.segments), 100)
        self.assertTrue(any(segment.text == "스노우 화이트 1권" for segment in document.segments))
        self.assertTrue(any(segment.doc_path == "OEBPS/toc.ncx" for segment in document.segments))

    def test_parse_epub_file_rejects_invalid_epub(self) -> None:
        with TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir) / "broken.epub"
            invalid_path.write_text("not a zip", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid EPUB archive"):
                parse_epub_file(invalid_path)

    def test_parse_epub_file_reports_missing_container(self) -> None:
        with TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir) / "missing-container.epub"
            with zipfile.ZipFile(invalid_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")

            with self.assertRaisesRegex(ValueError, "Invalid EPUB structure: missing META-INF/container.xml"):
                parse_epub_file(invalid_path)

    def test_parse_epub_file_extracts_text_in_sectioning_containers(self) -> None:
        """Names and prose inside blockquote/aside/section/header/figure/details
        and bare text directly under <body> must be reachable, not silently
        dropped because the wrapping tag is not <p>."""

        with TemporaryDirectory() as temp_dir:
            epub_path = _write_sectioning_epub(Path(temp_dir) / "book.epub")

            document = parse_epub_file(epub_path)

        body_texts = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.BODY
        ]
        joined = " | ".join(body_texts)

        self.assertIn("申海范 stepped into the room", joined)  # blockquote
        self.assertIn("translator's note about 永夜", joined)  # aside
        self.assertIn("Author: 김작가", joined)                  # address
        self.assertIn("Section-level prose with 신해범", joined)  # section direct text
        self.assertIn("Header lead-in with 主人公", joined)      # header
        self.assertIn("Footer line with 配角", joined)          # footer
        self.assertIn("Figure direct caption naming 黑龙", joined)  # figure
        self.assertIn("Click to reveal 隐藏角色", joined)        # details/summary
        self.assertIn("Body-level orphan paragraph about 旅行", joined)  # body-direct


def _write_minimal_epub(path: Path) -> Path:
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata>
    <dc:title>책 제목</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>ignore title</title></head>
  <body>
    <p>첫 문장 <span>강조</span> 끝</p>
    <p>둘째 문장</p>
    <script>not translatable</script>
  </body>
</html>
"""
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <docTitle><text>목차 제목</text></docTitle>
  <navMap>
    <navPoint><navLabel><text>1장</text></navLabel><content src="Text/chapter.xhtml"/></navPoint>
  </navMap>
</ncx>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/Text/chapter.xhtml", chapter)
        archive.writestr("OEBPS/toc.ncx", ncx)
        archive.writestr("OEBPS/Images/cover.jpg", b"\xff\xd8binary-cover\xff\xd9")
    return path


def _write_sectioning_epub(path: Path) -> Path:
    """Build a minimal EPUB whose chapter exercises sectioning + flow-content
    containers. Used to verify BLOCK_TAGS covers HTML5 wrappers that real
    novel EPUBs commonly use."""

    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata><dc:title>Sectioning Sample</dc:title></metadata>
  <manifest>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>ignore</title></head>
  <body>
    Body-level orphan paragraph about 旅行
    <blockquote>申海范 stepped into the room</blockquote>
    <aside>translator's note about 永夜</aside>
    <address>Author: 김작가</address>
    <section>Section-level prose with 신해범</section>
    <header>Header lead-in with 主人公</header>
    <footer>Footer line with 配角</footer>
    <figure>Figure direct caption naming 黑龙</figure>
    <details><summary>Click to reveal 隐藏角色</summary>more</details>
  </body>
</html>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/Text/chapter.xhtml", chapter)
    return path
