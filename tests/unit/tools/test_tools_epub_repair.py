from __future__ import annotations

from pathlib import Path
import zipfile

from transoria.bridge import build_default_router
from transoria.formats.epub_parser import parse_epub_file
from transoria.tools.epub_repair import preview_epub_repair, repair_epub_file


def test_preview_epub_repair_is_read_only(tmp_path: Path):
    epub_path = _write_epub(tmp_path / "bad.epub", "<p><br>숨은 원문</br></p>")
    output_path = tmp_path / "preview-output.epub"

    preview = preview_epub_repair(epub_path, output_path)

    assert preview.documents_to_repair >= 1
    assert preview.output_path == output_path
    assert preview.structure_check["status"] in {"ok", "warning"}
    assert not output_path.exists()


def test_repair_epub_exposes_text_hidden_inside_invalid_void_tags(tmp_path: Path):
    epub_path = _write_epub(
        tmp_path / "bad.epub",
        """
<p><br>용태가 안도의 한숨을 내쉬었다.<br>응급실 수납을 마쳤다.</br><p>다음 문장</p></br></p>
""",
    )

    result = repair_epub_file(epub_path)
    repaired = parse_epub_file(result.output_path)
    body_text = "\n".join(segment.text for segment in repaired.segments)

    assert result.output_path != epub_path
    assert result.structure_check is not None
    assert result.structure_check["status"] in {"ok", "warning"}
    assert result.structure_check["missing_entries"] == []
    assert result.html_files_scanned == 1
    assert result.html_files_repaired == 1
    assert result.void_containers_repaired >= 1
    assert "용태가 안도의 한숨을 내쉬었다." in body_text
    assert "응급실 수납을 마쳤다." in body_text


def test_repair_epub_normalizes_xml_entities_and_html_fragments(tmp_path: Path):
    epub_path = _write_epub(
        tmp_path / "fragment.epub",
        "<p>본문 &nbsp; 내용</p>",
        ncx_text="Tom & Jerry",
    )

    result = repair_epub_file(epub_path, tmp_path / "fixed.epub")
    repaired = parse_epub_file(result.output_path)
    text = "\n".join(segment.text for segment in repaired.segments)

    assert result.documents_repaired >= 2
    assert result.html_files_repaired == 1
    assert result.xml_files_repaired == 1
    assert result.document_wrappers_added == 1
    assert "본문" in text
    assert "Tom & Jerry" in text


def test_repair_epub_scans_xhtm_html_documents(tmp_path: Path):
    epub_path = _write_epub(
        tmp_path / "bad.epub",
        "<p>본문 &nbsp; 내용</p>",
        chapter_href="Text/chapter.xhtm",
    )

    result = repair_epub_file(epub_path, tmp_path / "fixed.epub")
    repaired = parse_epub_file(result.output_path)
    text = "\n".join(segment.text for segment in repaired.segments)

    assert result.html_files_scanned == 1
    assert result.html_files_repaired == 1
    assert "본문" in text


def test_epub_repair_bridge_returns_repair_result(tmp_path: Path):
    epub_path = _write_epub(tmp_path / "bad.epub", "<p><br>숨은 원문</br></p>")
    output_path = tmp_path / "out.epub"
    router = build_default_router(cache_root=tmp_path / "cache")

    response = router.call(
        "epub_repair.apply",
        {
            "input_path": str(epub_path),
            "output_path": str(output_path),
            "overwrite": False,
        },
    )

    assert response["output_path"] == str(output_path)
    assert response["structure_check"]["missing_entries"] == []
    assert response["documents_repaired"] >= 1
    assert output_path.exists()


def test_epub_repair_bridge_previews_without_writing(tmp_path: Path):
    epub_path = _write_epub(tmp_path / "bad.epub", "<p><br>숨은 원문</br></p>")
    output_path = tmp_path / "out.epub"
    router = build_default_router(cache_root=tmp_path / "cache")

    response = router.call(
        "epub_repair.preview",
        {"input_path": str(epub_path), "output_path": str(output_path)},
    )

    assert response["documents_to_repair"] >= 1
    assert response["would_change"] is True
    assert not output_path.exists()


def _write_epub(
    path: Path,
    chapter_body: str,
    *,
    chapter_href: str = "Text/chapter.xhtml",
    ncx_text: str = "목차",
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
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <docTitle><text>{ncx_text}</text></docTitle>
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
        archive.writestr(f"OEBPS/{chapter_href}", chapter_body)
        archive.writestr("OEBPS/toc.ncx", ncx)
    return path
