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

    def test_parse_large_korean_txt_sample(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "large.txt"
            source.write_text(
                "\n".join(f"{index}번째 문장입니다." for index in range(160)) + "\n",
                encoding="utf-8",
            )

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

    def test_write_translated_txt_preserves_traditional_when_target_is_simplified(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Novel Name.txt"
            source.write_text("원문\n", encoding="utf-8")
            output_dir = Path(temp_dir) / "out"
            document = parse_txt_file(source)

            written = write_translated_txt(
                document,
                {0: "回乾說無法拒絕"},
                output_dir,
                target_language=Language.CHINESE_SIMPLIFIED,
            )

            self.assertEqual(written.read_text(encoding="utf-8"), "回乾說無法拒絕\n")

    def test_write_translated_txt_normalizes_traditional_output(self) -> None:
        import pytest

        pytest.importorskip("opencc")
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Novel Name.txt"
            source.write_text("원문\n", encoding="utf-8")
            output_dir = Path(temp_dir) / "out"
            document = parse_txt_file(source)

            written = write_translated_txt(
                document,
                {0: "他说无法拒绝"},
                output_dir,
                target_language=Language.CHINESE_TRADITIONAL,
            )

            self.assertEqual(written.read_text(encoding="utf-8"), "他說無法拒絕\n")

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


class DecodeTextBytesEncodingTests(TestCase):
    def test_decode_recovers_cp949_korean(self) -> None:
        from transoria.formats.text import decode_text_bytes

        raw = "한국어 텍스트의 일반적인 길이를 테스트합니다.".encode("cp949")
        text, encoding = decode_text_bytes(raw)
        self.assertEqual(text, "한국어 텍스트의 일반적인 길이를 테스트합니다.")
        self.assertIn(encoding.lower(), {"cp949", "euc-kr"})

    def test_decode_recovers_gbk_chinese(self) -> None:
        from transoria.formats.text import decode_text_bytes

        raw = "北京欢迎您。这是一段中文文本，用来测试编码检测。".encode("gbk")
        text, encoding = decode_text_bytes(raw)
        self.assertEqual(text, "北京欢迎您。这是一段中文文本，用来测试编码检测。")
        self.assertIn(encoding.lower(), {"gbk", "gb18030", "gb2312"})

    def test_decode_recovers_shift_jis_japanese(self) -> None:
        from transoria.formats.text import decode_text_bytes

        raw = "日本語のテキストです。これは符号化検出のテストのための文章です。".encode("shift_jis")
        text, encoding = decode_text_bytes(raw)
        self.assertEqual(text, "日本語のテキストです。これは符号化検出のテストのための文章です。")
        self.assertIn(encoding.lower(), {"shift_jis", "cp932"})


class DecodeTextBytesChardetFailureTests(TestCase):
    def test_decode_falls_through_when_chardet_raises_oserror(self) -> None:
        from unittest.mock import patch

        from transoria.formats.text import decode_text_bytes

        with patch("chardet.detect", side_effect=OSError("idf.bin missing")):
            text, _encoding = decode_text_bytes("안녕".encode("cp949"))

        self.assertEqual(text, "안녕")

    def test_decode_falls_through_when_chardet_raises_filenotfound(self) -> None:
        from unittest.mock import patch

        from transoria.formats.text import decode_text_bytes

        with patch("chardet.detect", side_effect=FileNotFoundError("idf.bin")):
            text, _encoding = decode_text_bytes("안녕".encode("cp949"))

        self.assertEqual(text, "안녕")
