from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from transoria.domain import Language
from transoria.formats.text import (
    BILINGUAL_OUTPUT_FOLDER_EN,
    TextSegment,
    parse_txt_file,
    write_bilingual_txt,
    write_translated_txt,
)


class TextDocumentTests(TestCase):
    def test_parse_txt_file_preserves_lines_and_blank_lines(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "novel.txt"
            source.write_text("첫 줄\n\n  둘째 줄  \n", encoding="utf-8")

            document = parse_txt_file(source)

        self.assertEqual(document.encoding, "utf-8")
        self.assertEqual(
            document.segments,
            [
                TextSegment(index=0, text="첫 줄", newline="\n"),
                TextSegment(index=1, text="", newline="\n"),
                TextSegment(index=2, text="  둘째 줄  ", newline="\n"),
            ],
        )

    def test_parse_txt_file_decodes_cp949(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.txt"
            source.write_bytes("권세혁\n".encode("cp949"))

            document = parse_txt_file(source)

        self.assertEqual(document.segments[0].text, "권세혁")
        self.assertEqual(document.encoding, "cp949")

    def test_parse_real_korean_txt_fixture(self) -> None:
        source = next(Path("test/test-files").glob("*.txt"))

        document = parse_txt_file(source)

        self.assertGreater(len(document.segments), 100)
        self.assertTrue(any(segment.text.strip() for segment in document.segments))

    def test_write_translated_txt_uses_target_language_filename(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Novel Name.txt"
            source.write_text("원문\n", encoding="utf-8")
            output_dir = Path(temp_dir) / "out"
            document = parse_txt_file(source)

            written = write_translated_txt(
                document,
                {0: "译文"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            self.assertEqual(written, output_dir / "Novel Name-zh.txt")
            self.assertEqual(written.read_text(encoding="utf-8"), "译文\n")

    def test_write_translated_txt_preserves_missing_final_newline(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Novel Name.txt"
            source.write_text("원문", encoding="utf-8")
            output_dir = Path(temp_dir) / "out"
            document = parse_txt_file(source)

            written = write_translated_txt(
                document,
                {0: "译文"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            self.assertEqual(written.read_text(encoding="utf-8"), "译文")

    def test_write_bilingual_txt_uses_shared_bilingual_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Novel Name.txt"
            source.write_text("원문\n", encoding="utf-8")
            output_dir = Path(temp_dir) / "out"
            document = parse_txt_file(source)

            written = write_bilingual_txt(
                document,
                {0: "译文"},
                output_dir,
                source_language=Language.KOREAN,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            self.assertEqual(written, output_dir / BILINGUAL_OUTPUT_FOLDER_EN / "Novel Name-zh-kr.txt")
            self.assertEqual(written.read_text(encoding="utf-8"), "원문\n译文\n")
