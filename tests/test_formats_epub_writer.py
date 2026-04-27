from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile

from tests.test_formats_epub_parser import _write_minimal_epub
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
                _first_segment_index(document, EpubTextKind.OPF_TITLE): "书名",
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
            self.assertIn("书名", opf)
            self.assertIn("第一句<span>加重</span>结束", chapter)
            self.assertIn("<p>第二句</p>", chapter)
            self.assertEqual(cover_bytes, b"\xff\xd8binary-cover\xff\xd9")

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


def _first_segment_index(document, kind: EpubTextKind) -> int:
    return next(segment.index for segment in document.segments if segment.kind == kind)


def _first_body_text_index(document, text: str) -> int:
    return next(
        segment.index
        for segment in document.segments
        if segment.kind == EpubTextKind.BODY and segment.text == text
    )
