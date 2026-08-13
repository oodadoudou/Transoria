from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import unicodedata
import zipfile

from tests.unit.formats.test_formats_epub_parser import _write_minimal_epub
from transoria.domain import Language
from transoria.formats.epub_parser import EpubTextKind, parse_epub_file
from transoria.formats.epub_writer import write_bilingual_epub, write_translated_epub
from transoria.formats.text import BILINGUAL_OUTPUT_FOLDER_EN


class EpubWriterTests(TestCase):
    def test_write_translated_epub_preserves_archive_and_replaces_text_slots(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            translations = {
                _first_body_text_index(document, "첫 문장\n강조\n끝"): "第一句\n加重\n结束",
                _first_body_text_index(document, "둘째 문장"): "第二句",
            }
            written = write_translated_epub(
                document,
                translations,
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            self.assertEqual(written, output_dir / "Novel Name-zh.epub")
            with zipfile.ZipFile(written) as archive:
                names = archive.namelist()
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                cover_bytes = archive.read("OEBPS/Images/cover.jpg")
                mimetype = archive.getinfo("mimetype")

            self.assertEqual(names[0], "mimetype")
            self.assertEqual(mimetype.compress_type, zipfile.ZIP_STORED)
            # OPF metadata title stays as the source's original — we
            # never rewrite it.
            self.assertIn("책 제목", opf)
            self.assertIn("第一句<span>加重</span>结束", chapter)
            self.assertIn("<p>第二句</p>", chapter)
            self.assertEqual(cover_bytes, b"\xff\xd8binary-cover\xff\xd9")

    def test_write_translated_epub_removes_only_xml_forbidden_characters(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {_first_body_text_index(document, "둘째 문장"): "第二\x00句\t保留"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn("第二句\t保留", chapter)
            self.assertNotIn("\x00", chapter)

    def test_write_translated_epub_failure_preserves_existing_output_and_cleans_temp(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"
            output_dir.mkdir()
            output = output_dir / "Novel Name-zh.epub"
            output.write_bytes(b"existing output")

            with patch(
                "transoria.formats.epub_writer._validate_output_archive",
                side_effect=ValueError("invalid output"),
            ):
                with self.assertRaisesRegex(ValueError, "invalid output"):
                    write_translated_epub(
                        document,
                        {_first_body_text_index(document, "둘째 문장"): "第二句"},
                        output_dir,
                        target_language=Language.CHINESE_SIMPLIFIED,
                    )

            self.assertEqual(output.read_bytes(), b"existing output")
            self.assertEqual(list(output_dir.glob(".*.tmp")), [])

            output.unlink()
            with patch(
                "transoria.formats.epub_writer._validate_output_archive",
                side_effect=ValueError("invalid output"),
            ):
                with self.assertRaisesRegex(ValueError, "invalid output"):
                    write_translated_epub(
                        document,
                        {_first_body_text_index(document, "둘째 문장"): "第二句"},
                        output_dir,
                        target_language=Language.CHINESE_SIMPLIFIED,
                    )

            self.assertFalse(output.exists())
            self.assertEqual(list(output_dir.glob(".*.tmp")), [])

    def test_write_translated_epub_preserves_package_entries_after_chapter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = _write_minimal_epub(root / "original.epub")
            source = root / "late-package.epub"
            _reorder_archive_entries(
                original,
                source,
                [
                    "mimetype",
                    "OEBPS/Text/chapter.xhtml",
                    "OEBPS/toc.ncx",
                    "OEBPS/Images/cover.jpg",
                    "OEBPS/content.opf",
                    "META-INF/container.xml",
                ],
            )
            document = parse_epub_file(source)

            written = write_translated_epub(
                document,
                {_first_body_text_index(document, "둘째 문장"): "第二句"},
                root / "out",
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(source) as source_archive:
                source_names = source_archive.namelist()
            with zipfile.ZipFile(written) as output_archive:
                self.assertEqual(output_archive.namelist(), source_names)
                self.assertIsNone(output_archive.testzip())
                self.assertIn("OEBPS/content.opf", output_archive.namelist())
                self.assertIn("META-INF/container.xml", output_archive.namelist())
                chapter = output_archive.read("OEBPS/Text/chapter.xhtml").decode(
                    "utf-8"
                )

            self.assertLess(
                source_names.index("OEBPS/Text/chapter.xhtml"),
                source_names.index("OEBPS/content.opf"),
            )
            self.assertIn("<p>第二句</p>", chapter)

    def test_write_translated_epub_replaces_chapter_with_orphan_markup_prefix(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(
                Path(temp_dir) / "Novel Name.epub",
                chapter_prefix='<img alt="图片" src="../dropped_image.png" />',
            )
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {_first_body_text_index(document, "둘째 문장"): "第二句"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn("<p>第二句</p>", chapter)
            self.assertNotIn("dropped_image", chapter)

    def test_write_translated_epub_matches_normalized_archive_entry_names(self) -> None:
        with TemporaryDirectory() as temp_dir:
            chapter_href = "Text/samk - 낫 포 세일.xhtml"
            archive_chapter_href = unicodedata.normalize("NFD", chapter_href)
            source = _write_minimal_epub(
                Path(temp_dir) / "Novel Name.epub",
                chapter_href=chapter_href,
                archive_chapter_href=archive_chapter_href,
            )
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {_first_body_text_index(document, "둘째 문장"): "第二句"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read(f"OEBPS/{archive_chapter_href}").decode("utf-8")

            self.assertIn("<p>第二句</p>", chapter)
            self.assertNotIn("둘째 문장", chapter)

    def test_write_translated_epub_collapses_multipart_segment_when_needed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {
                    _first_body_text_index(document, "첫 문장\n강조\n끝"): (
                        "第一句加重结束"
                    )
                },
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn("第一句加重结束<span></span>", chapter)
            self.assertNotIn("첫 문장", chapter)
            self.assertNotIn("강조", chapter)

    def test_write_translated_epub_collapses_custom_inline_tags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_custom_inline_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {
                    _first_body_text_index(document, "그러나\n(소문 속 표현)\n만큼은 깔끔했다"): (
                        "但是据说他脸很干净。"
                    )
                },
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn("但是据说他脸很干净。<p2></p2>", chapter)
            self.assertNotIn("그러나", chapter)
            self.assertNotIn("소문 속 표현", chapter)

    def test_write_translated_epub_removes_ruby_annotations_from_translated_blocks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(
                Path(temp_dir) / "Novel Name.epub",
                chapter_body="""
    <p>は<ruby>歴<rt>れっき</rt></ruby>とした言葉</p>
    <p>別の文</p>
""",
            )
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {_first_body_text_index(document, "は歴とした言葉"): "这是正式的说法。"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn("这是正式的说法。", chapter)
            self.assertNotIn("れっき", chapter)
            self.assertNotIn("<rt", chapter)
            self.assertIn("別の文", chapter)

    def test_write_translated_epub_normalizes_traditional_output(self) -> None:
        import pytest

        pytest.importorskip("opencc")
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                document,
                {_first_body_text_index(document, "둘째 문장"): "他说无法拒绝"},
                output_dir,
                target_language=Language.CHINESE_TRADITIONAL,
            )

            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn("<p>他說無法拒絕</p>", chapter)
            self.assertNotIn("他说无法拒绝", chapter)

    def test_write_translated_epub_skips_digest_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            changed_first = document.segments[0].__class__(
                **{**document.segments[0].__dict__, "source_digest": "0" * 40}
            )
            changed_document = document.__class__(
                path=document.path,
                package=document.package,
                segments=[changed_first, *document.segments[1:]],
            )
            output_dir = Path(temp_dir) / "out"

            written = write_translated_epub(
                changed_document,
                {changed_first.index: "SHOULD NOT APPLY"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            with zipfile.ZipFile(written) as archive:
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
            self.assertNotIn("SHOULD NOT APPLY", opf)

    def test_write_bilingual_epub_uses_shared_folder_and_inserts_original_block(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            document = parse_epub_file(source)
            output_dir = Path(temp_dir) / "out"

            written = write_bilingual_epub(
                document,
                {_first_body_text_index(document, "둘째 문장"): "第二句"},
                output_dir,
                source_language=Language.KOREAN,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            self.assertEqual(written, output_dir / BILINGUAL_OUTPUT_FOLDER_EN / "Novel Name-zh-kr.epub")
            with zipfile.ZipFile(written) as archive:
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")

            self.assertIn('<p style="opacity:0.50;">둘째 문장</p>', chapter)
            self.assertIn("<p>第二句</p>", chapter)


def _first_body_text_index(document, text: str) -> int:
    return next(
        segment.index
        for segment in document.segments
        if segment.kind == EpubTextKind.BODY and segment.text == text
    )


def _reorder_archive_entries(source: Path, output: Path, names: list[str]) -> None:
    with zipfile.ZipFile(source) as source_archive:
        entries = {
            info.filename: (info, source_archive.read(info.filename))
            for info in source_archive.infolist()
        }
    with zipfile.ZipFile(output, "w") as output_archive:
        for name in names:
            info, raw = entries[name]
            output_archive.writestr(info, raw)


def _write_custom_inline_epub(path: Path) -> Path:
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata><dc:title>책 제목</dc:title></metadata>
  <manifest>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""
    chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <p>그러나<p2>(소문 속 표현)</p2>만큼은 깔끔했다</p>
  </body>
</html>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/Text/chapter.xhtml", chapter)
    return path
