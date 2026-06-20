from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile

from tests.unit.formats.test_formats_epub_parser import _write_minimal_epub
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

    def test_load_replacement_rules_txt_supports_hash_marked_arrow_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.txt"
            rule_file.write_text(
                "foo#->#bar\n"
                "old phrase #-># new phrase\n",
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

    def test_load_replacement_rules_txt_strips_spaced_hash_arrow_markers(self) -> None:
        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.txt"
            rule_file.write_text(
                "我没能守护在他身边# -> #我没能守护在她身边\n"
                "做的事是否正确。】# -> #做的事是否正确。】\n",
                encoding="utf-8",
            )

            rules = load_replacement_rules_txt(rule_file)

        self.assertEqual(
            rules,
            [
                ReplacementRule(src="我没能守护在他身边", dst="我没能守护在她身边"),
                ReplacementRule(src="做的事是否正确。】", dst="做的事是否正确。】"),
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

    def test_load_replacement_rules_decodes_legacy_korean_cp949(self) -> None:
        """A user-supplied rule file in legacy Korean encoding (cp949)
        must still parse — earlier versions hard-coded utf-8 and crashed
        with UnicodeDecodeError on any cp949/euc-kr file."""

        with TemporaryDirectory() as temp_dir:
            rule_file = Path(temp_dir) / "rules.txt"
            rule_file.write_bytes(
                "권세혁->Logan\n로건->Logan\n".encode("cp949")
            )

            rules = load_replacement_rules_txt(rule_file)

        self.assertEqual(
            rules,
            [
                ReplacementRule(src="권세혁", dst="Logan"),
                ReplacementRule(src="로건", dst="Logan"),
            ],
        )

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
                    # OPF metadata <dc:title> is intentionally never
                    # touched, so this rule's source string in the OPF
                    # is left alone.
                    ReplacementRule(src="책 제목", dst="书名"),
                    ReplacementRule(src="둘째 문장", dst="第二句"),
                ],
            )

            self.assertEqual(result.output_path, output_dir / "Novel Name-Replaced.epub")
            self.assertEqual(result.replacement_count, 1)
            with zipfile.ZipFile(result.output_path) as archive:
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                chapter = archive.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                cover = archive.read("OEBPS/Images/cover.jpg")

            self.assertIn("책 제목", opf)
            self.assertIn("<p>第二句</p>", chapter)
            self.assertEqual(cover, b"\xff\xd8binary-cover\xff\xd9")

    def test_apply_rules_collects_per_match_context_when_requested(self) -> None:
        """The post-run report relies on apply_rules capturing each
        match site with surrounding context. The rule's character
        offset, the matched substring, and a window of context around
        it must all be preserved so the modal can render a proper
        before/after pair."""

        text = (
            "申海范走进客厅。\n"
            "他看了申海范一眼。\n"
            "另一段没有命中。\n"
        )
        rules = [
            ReplacementRule(src="申海范", dst="申海凡", case_sensitive=True),
            ReplacementRule(src="客厅", dst="书房", case_sensitive=True),
        ]

        result = apply_rules(text, rules, collect_occurrences=True)

        self.assertEqual(result.replacement_count, 3)
        self.assertEqual(len(result.occurrences), 3)
        # First rule fires twice; second rule fires once. Order in
        # ``occurrences`` matches the order rules ran in (stable for
        # the report rendering).
        rule_indices = [o.rule_index for o in result.occurrences]
        self.assertEqual(rule_indices, [0, 0, 1])
        first = result.occurrences[0]
        self.assertEqual(first.match_text, "申海范")
        self.assertEqual(first.replacement_text, "申海凡")
        self.assertIn("走进客厅", first.after_context)

    def test_apply_rules_caps_occurrences_per_rule(self) -> None:
        """A rule that fires many times should still only attach the
        first ``occurrence_limit_per_rule`` snippets to the report —
        ``replacement_count`` keeps the true total so the cap never
        misleads the user."""

        text = "x" * 500
        rule = ReplacementRule(src="x", dst="y", case_sensitive=True)
        result = apply_rules(
            text,
            [rule],
            collect_occurrences=True,
            occurrence_limit_per_rule=10,
        )
        self.assertEqual(result.replacement_count, 500)
        self.assertEqual(len(result.occurrences), 10)
        # All captured occurrences belong to rule 0.
        self.assertEqual({o.rule_index for o in result.occurrences}, {0})
