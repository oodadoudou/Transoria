from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile

from tests.test_formats_epub_parser import _write_minimal_epub
from transoria.tools.replacement import (
    ReplacementRule,
    apply_rules,
    load_replacement_rules_txt,
    replace_epub_file,
    replace_txt_file,
)


class ReplacementTests(TestCase):
    def test_load_replacement_rules_txt_supports_arrow_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.txt"
            rule_file.write_text(
                "# original phrase->new phrase\n"
                "foo->bar\n"
                "old phrase -> new phrase\n",
                encoding="utf-8",
            )

            rules = load_replacement_rules_txt(rule_file)

        self.assertEqual(
            rules,
            [
                ReplacementRule(src="foo", dst="bar"),
                ReplacementRule(src="old phrase", dst="new phrase"),
            ],
        )

    def test_load_replacement_rules_rejects_non_txt_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.json"
            rule_file.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Only .txt replacement rule files are supported"):
                load_replacement_rules_txt(rule_file)

    def test_load_replacement_rules_reports_malformed_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.txt"
            rule_file.write_text("only-one-column\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Malformed replacement rule at line 1"):
                load_replacement_rules_txt(rule_file)

    def test_load_replacement_rules_preserves_extra_arrows_in_destination(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.txt"
            rule_file.write_text("a->b->c\n", encoding="utf-8")

            rules = load_replacement_rules_txt(rule_file)

        self.assertEqual(rules, [ReplacementRule(src="a", dst="b->c")])

    def test_apply_rules_supports_plain_case_insensitive_and_regex(self) -> None:
        result = apply_rules(
            "Hello HERO 123",
            [
                ReplacementRule(src="hero", dst="villain", case_sensitive=False),
                ReplacementRule(src=r"\d+", dst="456", regex=True),
            ],
        )

        self.assertEqual(result.text, "Hello villain 456")
        self.assertEqual(result.replacement_count, 2)
        self.assertEqual(result.errors, [])

    def test_apply_rules_skips_invalid_regex_and_records_error(self) -> None:
        result = apply_rules("abc", [ReplacementRule(src="[", dst="x", regex=True)])

        self.assertEqual(result.text, "abc")
        self.assertEqual(result.replacement_count, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Invalid regex", result.errors[0])

    def test_replace_txt_file_writes_replaced_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Novel Name.txt"
            source.write_text("Hello hero\n", encoding="utf-8")
            output_dir = Path(temp_dir) / "out"

            result = replace_txt_file(
                source,
                output_dir,
                [ReplacementRule(src="hero", dst="villain")],
            )

            self.assertEqual(result.output_path, output_dir / "Novel Name-Replaced.txt")
            self.assertEqual(result.output_path.read_text(encoding="utf-8"), "Hello villain\n")
            self.assertEqual(result.replacement_count, 1)

    def test_replace_epub_file_writes_replaced_output_and_preserves_binary_assets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = _write_minimal_epub(Path(temp_dir) / "Novel Name.epub")
            output_dir = Path(temp_dir) / "out"

            result = replace_epub_file(
                source,
                output_dir,
                [
                    ReplacementRule(src="책 제목", dst="书名"),
                    ReplacementRule(src="둘째 문장", dst="第二句"),
                ],
            )

            self.assertEqual(result.output_path, output_dir / "Novel Name-Replaced.epub")
            self.assertEqual(result.replacement_count, 2)
            with zipfile.ZipFile(result.output_path) as archive:
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                cover = archive.read("OEBPS/Images/cover.jpg")

            self.assertIn("书名", opf)
            self.assertIn("<p>第二句</p>", chapter)
            self.assertEqual(cover, b"\xff\xd8binary-cover\xff\xd9")
