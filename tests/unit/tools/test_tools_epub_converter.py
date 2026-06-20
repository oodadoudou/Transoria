from __future__ import annotations

from pathlib import Path
import zipfile

from transoria.tools.epub_converter import (
    EpubConvertAction,
    EpubConvertOptions,
    build_epub_convert_plan,
    convert_epub_to_txt,
    export_epub_text,
)


def test_export_epub_text_preserves_paragraph_and_line_breaks(tmp_path: Path) -> None:
    epub_path = _write_epub(
        tmp_path / "book.epub",
        """
    <p>첫 문장<br/>둘째 줄</p>
    <p><br/></p>
    <p>셋째 문장</p>
""",
    )

    exported = export_epub_text(epub_path)

    assert exported.spine_documents == 1
    assert exported.segments_written == 2
    assert "첫 문장\n둘째 줄" in exported.text
    assert "첫 문장\n둘째 줄\n\n\n\n셋째 문장\n" == exported.text


def test_export_epub_text_reads_xhtm_spine_documents(tmp_path: Path) -> None:
    epub_path = _write_epub(
        tmp_path / "book.epub",
        "<p>군 특수 정예부대 암실버 제11분대장</p>",
        chapter_href="Text/chapter.xhtm",
    )

    exported = export_epub_text(epub_path)

    assert exported.spine_documents == 1
    assert exported.segments_written == 1
    assert exported.text == "군 특수 정예부대 암실버 제11분대장\n"


def test_convert_epub_to_txt_writes_same_name_txt_beside_source(tmp_path: Path) -> None:
    epub_path = _write_epub(tmp_path / "novel.epub", "<p>본문</p>")
    action = EpubConvertAction(
        id="epub-0000",
        source_path=str(epub_path),
        output_path=str(tmp_path / "novel.txt"),
    )

    result = convert_epub_to_txt(action)

    assert result.status == "converted"
    assert Path(result.output_path) == tmp_path / "novel.txt"
    assert (tmp_path / "novel.txt").read_text(encoding="utf-8") == "본문\n"


def test_convert_epub_to_txt_uses_unique_output_when_txt_exists(tmp_path: Path) -> None:
    epub_path = _write_epub(tmp_path / "novel.epub", "<p>본문</p>")
    (tmp_path / "novel.txt").write_text("old", encoding="utf-8")
    action = EpubConvertAction(
        id="epub-0000",
        source_path=str(epub_path),
        output_path=str(tmp_path / "novel.txt"),
    )

    result = convert_epub_to_txt(action)

    assert result.status == "converted"
    assert Path(result.output_path) == tmp_path / "novel (1).txt"
    assert (tmp_path / "novel.txt").read_text(encoding="utf-8") == "old"


def test_build_epub_convert_folder_plan_defaults_to_input_folder(tmp_path: Path) -> None:
    source = tmp_path / "input"
    nested = source / "nested"
    nested.mkdir(parents=True)
    _write_epub(source / "a.epub", "<p>A</p>")
    _write_epub(nested / "b.epub", "<p>B</p>")

    plan = build_epub_convert_plan(
        source,
        mode="folder",
        options=EpubConvertOptions(output_dir="", recursive=True),
    )

    assert [Path(action.output_path).relative_to(source) for action in plan.actions] == [
        Path("a.txt"),
        Path("nested/b.txt"),
    ]


def test_build_epub_convert_file_plan_returns_single_action(tmp_path: Path) -> None:
    epub_path = _write_epub(tmp_path / "novel.epub", "<p>본문</p>")

    plan = build_epub_convert_plan(
        epub_path,
        mode="file",
        options=EpubConvertOptions(output_dir="", recursive=True),
    )

    assert len(plan.actions) == 1
    assert plan.actions[0].source_path == str(epub_path.resolve())
    assert Path(plan.actions[0].output_path) == (tmp_path / "novel.txt").resolve()


def test_convert_epub_to_txt_reports_invalid_archive(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.epub"
    bad_path.write_text("not a zip", encoding="utf-8")
    action = EpubConvertAction(
        id="epub-0000",
        source_path=str(bad_path),
        output_path=str(tmp_path / "bad.txt"),
    )

    result = convert_epub_to_txt(action)

    assert result.status == "failed"
    assert "BadZipFile" in result.error
    assert not (tmp_path / "bad.txt").exists()


def _write_epub(
    path: Path,
    chapter_body: str,
    *,
    chapter_href: str = "Text/chapter.xhtml",
) -> Path:
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata>
    <dc:title>책 제목</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="{chapter_href}" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>ignore</title></head>
  <body>
{chapter_body}
  </body>
</html>
"""
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <docTitle><text>목차 제목</text></docTitle>
</ncx>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr(f"OEBPS/{chapter_href}", chapter)
        archive.writestr("OEBPS/toc.ncx", ncx)
    return path
