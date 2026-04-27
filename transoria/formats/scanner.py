from __future__ import annotations

from pathlib import Path

from transoria.domain import DocumentFile, DocumentFormat


SUPPORTED_FORMATS = {
    ".epub": DocumentFormat.EPUB,
    ".txt": DocumentFormat.TXT,
}


def scan_input_directory(input_dir: Path) -> list[DocumentFile]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    documents: list[DocumentFile] = []
    for path in sorted(input_dir.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue

        document_format = SUPPORTED_FORMATS.get(path.suffix.lower())
        if document_format is None:
            continue

        documents.append(
            DocumentFile(
                path=path,
                relative_path=path.relative_to(input_dir),
                format=document_format,
            )
        )

    return documents


def ensure_output_directory(output_dir: Path) -> Path:
    if output_dir.exists() and not output_dir.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    probe_path = output_dir / ".transoria_write_test"
    try:
        probe_path.write_text("", encoding="utf-8")
    except OSError as exc:
        raise PermissionError(f"Output directory is not writable: {output_dir}") from exc
    finally:
        if probe_path.exists():
            probe_path.unlink()

    return output_dir
