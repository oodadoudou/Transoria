from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from transoria.domain import DocumentFormat
from transoria.formats.scanner import ensure_output_directory, scan_input_directory


class ScannerTests(TestCase):
    def test_scan_input_directory_finds_epub_and_txt_recursively(self) -> None:
        with TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            nested = input_dir / "nested"
            nested.mkdir(parents=True)
            (input_dir / "root.txt").write_text("root", encoding="utf-8")
            (nested / "book.epub").write_bytes(b"epub")
            (nested / "ignored.md").write_text("ignored", encoding="utf-8")

            documents = scan_input_directory(input_dir)

        self.assertEqual(
            [(doc.relative_path.as_posix(), doc.format) for doc in documents],
            [
                ("nested/book.epub", DocumentFormat.EPUB),
                ("root.txt", DocumentFormat.TXT),
            ],
        )

    def test_scan_input_directory_rejects_missing_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "Input directory does not exist"):
                scan_input_directory(Path(temp_dir) / "missing")

    def test_scan_input_directory_rejects_file_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            input_file = Path(temp_dir) / "book.txt"
            input_file.write_text("text", encoding="utf-8")

            with self.assertRaisesRegex(NotADirectoryError, "Input path is not a directory"):
                scan_input_directory(input_file)

    def test_ensure_output_directory_creates_missing_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"

            ensured = ensure_output_directory(output_dir)

        self.assertEqual(ensured, output_dir)

    def test_ensure_output_directory_rejects_file_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "output.txt"
            output_file.write_text("text", encoding="utf-8")

            with self.assertRaisesRegex(NotADirectoryError, "Output path is not a directory"):
                ensure_output_directory(output_file)
