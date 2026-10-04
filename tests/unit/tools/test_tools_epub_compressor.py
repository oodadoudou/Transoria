from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from transoria.tools import epub_compressor as compressor

from transoria.tools.epub_compressor import (
    EpubCompressAction,
    EpubCompressOptions,
    build_epub_compress_plan,
    compress_epub_file,
)


def test_missing_source_preserves_existing_destination(tmp_path):
    output = tmp_path / "existing.epub"
    output.write_bytes(b"precious existing book")
    action = EpubCompressAction("test", str(tmp_path / "missing.epub"), str(output))
    result = compress_epub_file(action, EpubCompressOptions())
    assert result.status == "failed"
    assert output.read_bytes() == b"precious existing book"


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("failure", ["write", "validate", "publish"])
def test_compression_failure_preserves_books(tmp_path, monkeypatch, overwrite, failure):
    source, output = tmp_path / "source.epub", tmp_path / "output.epub"
    _write_epub(source)
    output.write_bytes(b"existing output")
    original = source.read_bytes()
    stale = source.with_name(f".{source.name}.transoria-compress.tmp")
    stale.write_bytes(b"unrelated old temporary file")
    action = EpubCompressAction("test", str(source), str(output))

    def fail(*args, **kwargs):
        if failure == "write":
            Path(args[1]).write_bytes(b"partial archive")
        raise OSError("injected failure")

    target = {"write": "_compress_archive", "validate": "compare_epub_structure_checks", "publish": "publish_file"}[failure]
    monkeypatch.setattr(compressor, target, fail)
    result = compress_epub_file(action, EpubCompressOptions(replace_original=overwrite))
    assert result.status == "failed"
    assert source.read_bytes() == original
    assert output.read_bytes() == b"existing output"
    assert stale.read_bytes() == b"unrelated old temporary file"
    assert not list(tmp_path.glob(".epub-compress-*"))
    assert sorted(p.name for p in tmp_path.glob("*.epub")) == ["output.epub", "source.epub"]


def test_compression_publication_race_preserves_competing_file(tmp_path, monkeypatch):
    source, output = tmp_path / "source.epub", tmp_path / "output.epub"
    _write_epub(source)
    original = source.read_bytes()
    link = compressor.os.link

    def race(src, dst):
        Path(dst).write_bytes(b"competing output")
        link(src, dst)

    monkeypatch.setattr(compressor.os, "link", race)
    result = compress_epub_file(EpubCompressAction("test", str(source), str(output)), EpubCompressOptions())
    assert result.status == "failed"
    assert output.read_bytes() == b"competing output"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".epub-compress-*"))


def test_build_epub_compress_plan_uses_localized_suffix(tmp_path: Path) -> None:
    epub = tmp_path / "Book.epub"
    _write_epub(epub)

    plan = build_epub_compress_plan(
        epub,
        mode="file",
        options=EpubCompressOptions(suffix="-Compressed"),
    )

    assert plan.actions[0].output_path.endswith("Book-Compressed.epub")
    assert plan.actions[0].structure_check is not None


def test_folder_plan_skips_already_compressed_when_not_replacing(tmp_path: Path) -> None:
    _write_epub(tmp_path / "Book.epub")
    _write_epub(tmp_path / "Book_压缩.epub")

    plan = build_epub_compress_plan(
        tmp_path,
        mode="folder",
        options=EpubCompressOptions(suffix="_压缩", replace_original=False),
    )

    assert [Path(action.source_path).name for action in plan.actions] == ["Book.epub"]


def test_folder_plan_uses_default_suffix_when_marker_is_blank(tmp_path: Path) -> None:
    _write_epub(tmp_path / "Book.epub")
    _write_epub(tmp_path / "Book_压缩.epub")

    plan = build_epub_compress_plan(
        tmp_path,
        mode="folder",
        options=EpubCompressOptions(suffix="  ", replace_original=False),
    )

    assert [Path(action.source_path).name for action in plan.actions] == ["Book.epub"]
    assert plan.actions[0].output_path.endswith("Book_压缩.epub")


def test_compress_preserves_unique_fonts_and_mimetype_order(tmp_path: Path) -> None:
    source = tmp_path / "Book.epub"
    output = tmp_path / "Book_压缩.epub"
    _write_epub(source)

    result = compress_epub_file(
        EpubCompressAction(
            id="epub-0000",
            source_path=str(source),
            output_path=str(output),
        ),
        EpubCompressOptions(),
    )

    assert result.status == "compressed"
    assert result.outcome in {"success", "success_with_warnings"}
    assert result.structure_comparison is not None
    assert result.structure_comparison["status"] in {"ok", "warning"}
    assert result.fonts_removed == 0
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert "OEBPS/Fonts/font.ttf" in archive.namelist()
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert "Original Title" in opf
        assert "font.ttf" in opf


def test_compress_deduplicates_fonts_and_rewrites_css_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "Book.epub"
    output = tmp_path / "Book_压缩.epub"
    _write_epub(source, duplicate_font=True)

    result = compress_epub_file(
        EpubCompressAction(
            id="epub-0000",
            source_path=str(source),
            output_path=str(output),
        ),
        EpubCompressOptions(),
    )

    assert result.status == "compressed"
    assert result.fonts_removed == 1
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "OEBPS/Fonts/font.ttf" in names
        assert "OEBPS/Fonts/font-copy.ttf" not in names
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        css = archive.read("OEBPS/style.css").decode("utf-8")
        assert 'href="Fonts/font.ttf"' in opf
        assert "font-copy.ttf" not in opf
        assert "url('Fonts/font.ttf')" in css
        assert "font-copy.ttf" not in css


def test_compress_decodes_percent_encoded_font_hrefs(tmp_path: Path) -> None:
    source = tmp_path / "Book.epub"
    output = tmp_path / "Book_压缩.epub"
    _write_epub(source, duplicate_font=True, encoded_duplicate_font=True)

    result = compress_epub_file(
        EpubCompressAction(
            id="epub-0000",
            source_path=str(source),
            output_path=str(output),
        ),
        EpubCompressOptions(),
    )

    assert result.status == "compressed"
    assert result.fonts_removed == 1
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "OEBPS/Fonts/font.ttf" in names
        assert "OEBPS/Fonts/font copy.ttf" not in names
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        css = archive.read("OEBPS/style.css").decode("utf-8")

    assert 'href="Fonts/font.ttf"' in opf
    assert "font%20copy.ttf" not in opf
    assert "font%20copy.ttf" not in css
    assert "url('Fonts/font.ttf')" in css


def test_compress_can_remove_all_fonts_and_font_face_rules(tmp_path: Path) -> None:
    source = tmp_path / "Book.epub"
    output = tmp_path / "Book_压缩.epub"
    _write_epub(source, duplicate_font=True)

    result = compress_epub_file(
        EpubCompressAction(
            id="epub-0000",
            source_path=str(source),
            output_path=str(output),
        ),
        EpubCompressOptions(font_mode="remove"),
    )

    assert result.status == "compressed"
    assert result.fonts_removed == 2
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "OEBPS/Fonts/font.ttf" not in names
        assert "OEBPS/Fonts/font-copy.ttf" not in names
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        css = archive.read("OEBPS/style.css").decode("utf-8")
        assert "font.ttf" not in opf
        assert "@font-face" not in css
        assert "font-copy.ttf" not in css


def test_compress_replaces_original_atomically(tmp_path: Path) -> None:
    source = tmp_path / "Book.epub"
    _write_epub(source)
    original_size = source.stat().st_size

    result = compress_epub_file(
        EpubCompressAction(
            id="epub-0000",
            source_path=str(source),
            output_path=str(source),
        ),
        EpubCompressOptions(replace_original=True),
    )

    assert result.status == "compressed"
    assert result.output_path == str(source.resolve())
    assert result.original_size_bytes == original_size
    assert not (tmp_path / ".Book.epub.transoria-compress.tmp").exists()


def test_bad_zip_returns_failed_result(tmp_path: Path) -> None:
    source = tmp_path / "bad.epub"
    source.write_text("not zip", encoding="utf-8")

    result = compress_epub_file(
        EpubCompressAction(
            id="epub-0000",
            source_path=str(source),
            output_path=str(tmp_path / "bad_压缩.epub"),
        ),
        EpubCompressOptions(),
    )

    assert result.status == "failed"
    assert "BadZipFile" in result.error


def _write_epub(
    path: Path,
    *,
    duplicate_font: bool = False,
    encoded_duplicate_font: bool = False,
) -> None:
    image = Image.new("RGB", (128, 128), color=(120, 80, 40))
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="JPEG", quality=95)
    duplicate_href = "Fonts/font%20copy.ttf" if encoded_duplicate_font else "Fonts/font-copy.ttf"
    duplicate_entry = "OEBPS/Fonts/font copy.ttf" if encoded_duplicate_font else "OEBPS/Fonts/font-copy.ttf"
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata><dc:title>Original Title</dc:title></metadata>
  <manifest>
    <item id="chap" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="font" href="Fonts/font.ttf" media-type="application/x-font-ttf"/>
    {duplicate_font_item}
  </manifest>
  <spine><itemref idref="chap"/></spine>
</package>""".format(
        duplicate_font_item=(
            f'<item id="font2" href="{duplicate_href}" media-type="application/x-font-ttf"/>'
            if duplicate_font
            else ""
        )
    )
    css = f"@font-face {{ font-family: Test; src: url('{duplicate_href}'); }}\nbody {{ font-family: Test; }}" if duplicate_font else "@font-face { font-family: Test; src: url('Fonts/font.ttf'); }"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter.xhtml", "<html><body>Text</body></html>")
        archive.writestr("OEBPS/style.css", css)
        archive.writestr("OEBPS/Fonts/font.ttf", b"font-data")
        if duplicate_font:
            archive.writestr(duplicate_entry, b"font-data")
        archive.writestr("OEBPS/Images/cover.jpg", image_bytes.getvalue())
