from pathlib import Path
from unittest import TestCase

from transoria.domain import (
    DocumentFile,
    DocumentFormat,
    Language,
    SubtaskStatus,
    TaskStatus,
    translated_filename,
)


class DomainTests(TestCase):
    def test_translated_filename_uses_original_stem_and_language_tag(self) -> None:
        self.assertEqual(
            translated_filename(Path("Novel Name.epub"), Language.CHINESE_SIMPLIFIED),
            "Novel Name-zh.epub",
        )

    def test_bilingual_filename_uses_target_and_source_language_tags(self) -> None:
        self.assertEqual(
            translated_filename(
                Path("Novel Name.epub"),
                Language.CHINESE_SIMPLIFIED,
                source_language=Language.KOREAN,
                bilingual=True,
            ),
            "Novel Name-zh-kr.epub",
        )

    def test_document_file_serializes_to_json_ready_dict(self) -> None:
        document = DocumentFile(
            path=Path("books/example.txt"),
            relative_path=Path("example.txt"),
            format=DocumentFormat.TXT,
        )

        self.assertEqual(
            document.to_dict(),
            {
                "path": "books/example.txt",
                "relative_path": "example.txt",
                "format": "txt",
            },
        )

    def test_task_statuses_include_runtime_control_states(self) -> None:
        self.assertEqual(TaskStatus.STOPPING.value, "stopping")
        self.assertEqual(TaskStatus.STOPPED.value, "stopped")
        self.assertEqual(SubtaskStatus.FAILED.value, "failed")
        self.assertEqual(SubtaskStatus.COMPLETED.value, "completed")

