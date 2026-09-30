"""Opt-in regression checks; all outputs are temporary and inputs are read-only."""
from __future__ import annotations

import argparse
import hashlib
import re
import tempfile
import zipfile
from pathlib import Path

from transoria.tools.epub_content import ContentSession
from transoria.tools.epub_editor_tools import compare


def entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {item.filename: archive.read(item) for item in archive.infolist()}


def check(source: Path) -> None:
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    original = entries(source)
    with tempfile.TemporaryDirectory(prefix="epub-editor-roundtrip-") as directory:
        output = Path(directory) / "result.epub"
        session = ContentSession.open(str(source))
        session.save(str(output), False)
        assert entries(output) == original
        for path in session.spine[:5]:
            assert session.preview(path)
        editable = [str(item["path"]) for item in session.files if item["editable"]]
        chapters = []
        for path in session.spine:
            text = session.read(path)["content"]
            if re.search(r"</(?:\w+:)?body>", text):
                chapters.append(path)
                session.write(path, re.sub(r"(</(?:\w+:)?body>)", r"<!--EDITOR_TEST_123-->\1", text, count=1))
                if len(chapters) == 2:
                    break
        assert chapters
        proposal = session.preview_replace("EDITOR_TEST_(\\d+)", "EDITOR_VERIFIED_\\1", editable, regular_expression=True)
        assert proposal["replacements"] == len(chapters)
        session.replace("EDITOR_TEST_(\\d+)", "EDITOR_VERIFIED_\\1", editable, regular_expression=True, expected_fingerprints=proposal["fingerprints"])
        assert all("EDITOR_VERIFIED_123" in session.read(path)["content"] for path in chapters)
        changed = {row["path"] for row in compare(session)["rows"]}
        assert changed == set(chapters), changed - set(chapters)
        session.save(str(output), True)
        reopened = ContentSession.open(str(output))
        assert len(reopened.search("EDITOR_VERIFIED_123", chapters)) == len(chapters)
        stylesheet = next((item for item in reopened.files if item["media_type"] == "text/css"), None)
        if stylesheet:
            path = str(stylesheet["path"])
            renamed = str(Path(path).with_name("editor_test_style.css"))
            reopened.rename_resource(path, renamed)
            reopened.save(str(output), True)
            assert renamed in entries(output) and path not in entries(output)
            assert ContentSession.open(str(output)).preview(chapters[0])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    print(f"PASS: {source.name}; roundtrip, preview, regex batch replacement, resource rename, source hash", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args()
    books = sorted(args.corpus.glob("*.epub"))
    if not books:
        parser.error("No EPUB files found in the supplied copy-only corpus.")
    for source in books:
        check(source)


if __name__ == "__main__":
    main()
