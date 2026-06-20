from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import unicodedata
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
        self.assertEqual(
            [part.path for part in body_segments[0].parts],
            [
                "/html/body[1]/p[1]",
                "/html/body[1]/p[1]/span[1]",
                "/html/body[1]/p[1]/span[1]",
            ],
        )

    def test_parse_epub_file_extracts_xhtm_spine_documents(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(
                Path(temp_dir) / "book.epub",
                chapter_href="Text/chapter.xhtm",
                chapter_body="""
    <p>1화</p>
    <p>군 특수 정예부대 암실버 제11분대장</p>
""",
            )

            document = parse_epub_file(epub_path)

        body_segments = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.BODY
        ]
        self.assertEqual(document.package.spine_paths, ["OEBPS/Text/chapter.xhtm"])
        self.assertIn("1화", body_segments)
        self.assertIn("군 특수 정예부대 암실버 제11분대장", body_segments)

    def test_parse_epub_file_reads_unicode_normalization_equivalent_entry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            chapter_href = "Text/낫 포 세일.xhtml"
            epub_path = _write_minimal_epub(
                Path(temp_dir) / "book.epub",
                chapter_href=chapter_href,
                archive_chapter_href=unicodedata.normalize("NFD", chapter_href),
            )

            document = parse_epub_file(epub_path)

        self.assertEqual(document.package.spine_paths, ["OEBPS/Text/낫 포 세일.xhtml"])
        self.assertTrue(any(segment.text == "첫 문장\n강조\n끝" for segment in document.segments))

    def test_parse_epub_file_ignores_ruby_annotations_without_splitting_base_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(
                Path(temp_dir) / "book.epub",
                chapter_body="""
    <p>は<ruby>歴<rt>れっき</rt></ruby>とした言葉</p>
    <p><ruby><span>木</span><rt><span>こ</span></rt></ruby><ruby><span>洩</span><rt>も</rt></ruby>れ日</p>
    <p><ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby></p>
""",
            )

            document = parse_epub_file(epub_path)

        body_texts = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.BODY
        ]
        self.assertIn("は歴とした言葉", body_texts)
        self.assertIn("木洩れ日", body_texts)
        self.assertIn("漢字", body_texts)
        self.assertNotIn("は\n歴\nとした言葉", body_texts)
        joined = "\n".join(body_texts)
        for annotation in ("れっき", "こ", "も", "かん"):
            self.assertNotIn(annotation, joined)

    def test_parse_epub_file_ignores_orphan_markup_before_xhtml_document(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(
                Path(temp_dir) / "book.epub",
                chapter_prefix='<img alt="图片" src="../dropped_image.png" />',
            )

            document = parse_epub_file(epub_path)

        body_segments = [segment for segment in document.segments if segment.kind == EpubTextKind.BODY]
        self.assertIn("첫 문장\n강조\n끝", [segment.text for segment in body_segments])
        self.assertIn("둘째 문장", [segment.text for segment in body_segments])

    def test_parse_epub_file_repairs_redundant_void_end_tags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(
                Path(temp_dir) / "book.epub",
                chapter_head='<link href="../Styles/style.css" rel="stylesheet"/></link>',
                chapter_body="""
    <p>Prologue</p>
    <p><br/></br></p>
    <p>쉴 새 없이 뿜어대는 기계음이 내부 곳곳에 퍼졌다.</p>
""",
            )

            document = parse_epub_file(epub_path)

        body_segments = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.BODY
        ]
        self.assertIn("Prologue", body_segments)
        self.assertIn("쉴 새 없이 뿜어대는 기계음이 내부 곳곳에 퍼졌다.", body_segments)

    def test_parse_epub_file_skips_opf_title_and_extracts_ncx_text(self) -> None:
        """OPF metadata <dc:title> is intentionally NOT extracted —
        the book title is part of the user's own metadata. NCX nav
        labels (inner TOC chapter titles) are still translatable."""

        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(Path(temp_dir) / "book.epub")

            document = parse_epub_file(epub_path)

        kinds = {segment.kind for segment in document.segments}
        ncx_texts = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.NCX
        ]

        self.assertNotIn("책 제목", [s.text for s in document.segments])
        self.assertIn("목차 제목", ncx_texts)
        self.assertIn("1장", ncx_texts)
        self.assertTrue(kinds.issubset({EpubTextKind.BODY, EpubTextKind.NAV, EpubTextKind.NCX}))

    def test_parse_epub_file_extracts_parent_nav_link_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_nav_epub(Path(temp_dir) / "book.epub")

            document = parse_epub_file(epub_path)

        nav_texts = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.NAV
        ]
        self.assertIn("할 수 없는 것들 외전3", nav_texts)
        self.assertIn("1. 베스트 드라이버", nav_texts)
        self.assertIn("2. 황금빛 인생", nav_texts)
        self.assertNotIn(
            "할 수 없는 것들 외전31. 베스트 드라이버2. 황금빛 인생",
            nav_texts,
        )

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

    def test_parse_epub_file_recovers_package_xml_entities(self) -> None:
        with TemporaryDirectory() as temp_dir:
            epub_path = _write_minimal_epub(
                Path(temp_dir) / "book.epub",
                opf_title="책 & 제목&nbsp;외전",
            )

            document = parse_epub_file(epub_path)

        self.assertEqual(document.package.opf_path, "OEBPS/content.opf")
        body_texts = [
            segment.text
            for segment in document.segments
            if segment.kind == EpubTextKind.BODY
        ]
        self.assertIn("첫 문장\n강조\n끝", body_texts)

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


def _write_minimal_epub(
    path: Path,
    *,
    chapter_href: str = "Text/chapter.xhtml",
    chapter_prefix: str = "",
    chapter_head: str = "<title>ignore title</title>",
    opf_title: str = "책 제목",
    archive_chapter_href: str | None = None,
    chapter_body: str = """
    <p>첫 문장 <span>강조</span> 끝</p>
    <p>둘째 문장</p>
    <script>not translatable</script>
""",
) -> Path:
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata>
    <dc:title>{opf_title}</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="{chapter_href}" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    chapter = f"""{chapter_prefix}<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>{chapter_head}</head>
  <body>
{chapter_body}
  </body>
</html>
"""
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <docTitle><text>목차 제목</text></docTitle>
  <navMap>
    <navPoint><navLabel><text>1장</text></navLabel><content src="{chapter_href}"/></navPoint>
  </navMap>
</ncx>
"""
    with zipfile.ZipFile(path, "w") as archive:
        chapter_entry_href = archive_chapter_href or chapter_href
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr(f"OEBPS/{chapter_entry_href}", chapter)
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


def _write_nav_epub(path: Path) -> Path:
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata><dc:title>Nav Sample</dc:title></metadata>
  <manifest>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="../nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>chapter</title></head>
  <body><p>본문</p></body>
</html>
"""
    nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Table of Contents</title></head>
  <body>
    <nav epub:type="toc">
      <ol>
        <li>
          <a href="OEBPS/Text/chapter.xhtml">할 수 없는 것들 외전3</a>
          <ol>
            <li><a href="OEBPS/Text/chapter.xhtml">1. 베스트 드라이버</a></li>
            <li><a href="OEBPS/Text/chapter.xhtml">2. 황금빛 인생</a></li>
          </ol>
        </li>
      </ol>
    </nav>
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
        archive.writestr("nav.xhtml", nav)
    return path
