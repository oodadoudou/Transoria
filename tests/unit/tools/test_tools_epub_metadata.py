from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from transoria.tools.epub_metadata import (
    apply_epub_metadata,
    read_cover_preview,
    read_epub_metadata,
)


OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"


def _image_bytes(fmt: str = "JPEG", color: tuple[int, int, int] = (200, 10, 20)) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (6, 6), color)
    image.save(output, format=fmt)
    return output.getvalue()


def _write_epub(
    path: Path,
    *,
    with_cover: bool = True,
    container_full_path: str = "OEBPS/content.opf",
    archive_opf_path: str = "OEBPS/content.opf",
    cover_href: str = "Images/cover.jpg",
    archive_cover_path: str = "OEBPS/Images/cover.jpg",
) -> None:
    container = f"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{container_full_path}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    cover_item = (
        f'<item id="cover-image" href="{cover_href}" media-type="image/jpeg" properties="cover-image"/>'
        if with_cover
        else ""
    )
    cover_meta = '<meta name="cover" content="cover-image"/>' if with_cover else ""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="{OPF_NS}" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="{DC_NS}">
    <dc:identifier id="bookid">id</dc:identifier>
    <dc:title>Old Title</dc:title>
    <dc:creator>Old Author</dc:creator>
    {cover_meta}
  </metadata>
  <manifest>
    {cover_item}
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr(archive_opf_path, opf)
        archive.writestr("OEBPS/nav.xhtml", "<html><body><nav></nav></body></html>")
        archive.writestr("OEBPS/Text/ch1.xhtml", "<html><body><p>Hello</p></body></html>")
        if with_cover:
            archive.writestr(archive_cover_path, _image_bytes())


def _read_opf(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read("OEBPS/content.opf"))


def test_read_epub_metadata_finds_title_author_and_cover(tmp_path: Path):
    epub = tmp_path / "book.epub"
    _write_epub(epub)

    info = read_epub_metadata(epub)

    assert info.title == "Old Title"
    assert info.authors == ("Old Author",)
    assert info.has_cover is True
    assert info.cover_archive_path == "OEBPS/Images/cover.jpg"
    assert info.cover_preview_data_url.startswith("data:image/")
    assert info.structure_check["status"] in {"ok", "warning"}


def test_read_epub_metadata_decodes_encoded_package_and_cover_paths(tmp_path: Path):
    epub = tmp_path / "book.epub"
    _write_epub(
        epub,
        container_full_path="OEBPS/package%20file.opf",
        archive_opf_path="OEBPS/package file.opf",
        cover_href="Images/cover%20image.jpg",
        archive_cover_path="OEBPS/Images/cover image.jpg",
    )

    info = read_epub_metadata(epub)

    assert info.package_path == "OEBPS/package file.opf"
    assert info.cover_archive_path == "OEBPS/Images/cover image.jpg"
    assert info.cover_preview_data_url.startswith("data:image/")


def test_read_cover_preview_returns_thumbnail_data_url(tmp_path: Path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(_image_bytes("PNG", (10, 220, 30)))

    preview = read_cover_preview(cover)

    assert preview.startswith("data:image/")


def test_apply_metadata_updates_only_package_metadata(tmp_path: Path):
    epub = tmp_path / "book.epub"
    out = tmp_path / "book_metadata.epub"
    _write_epub(epub)

    result = apply_epub_metadata(
        epub,
        out,
        title="New Title",
        author="New Author",
    )

    assert result.metadata_updated is True
    assert result.cover_updated is False
    assert result.outcome in {"success", "success_with_warnings"}
    assert result.structure_comparison["status"] in {"ok", "warning"}
    updated = read_epub_metadata(out)
    assert updated.title == "New Title"
    assert updated.authors == ("New Author",)
    with zipfile.ZipFile(epub) as before, zipfile.ZipFile(out) as after:
        assert before.read("OEBPS/nav.xhtml") == after.read("OEBPS/nav.xhtml")
        assert before.read("OEBPS/Text/ch1.xhtml") == after.read("OEBPS/Text/ch1.xhtml")
    opf = _read_opf(out)
    assert opf.find(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref").attrib["idref"] == "ch1"


def test_apply_metadata_rejects_same_path_without_overwrite(tmp_path: Path):
    epub = tmp_path / "book.epub"
    _write_epub(epub)

    try:
        apply_epub_metadata(epub, epub, title="New Title")
    except ValueError as exc:
        assert "confirm overwrite" in str(exc)
    else:
        raise AssertionError("same input/output path should require overwrite confirmation")


def test_apply_metadata_can_overwrite_original_with_temp_file(tmp_path: Path):
    epub = tmp_path / "book.epub"
    _write_epub(epub)

    result = apply_epub_metadata(
        epub,
        epub,
        title="Overwrite Title",
        overwrite=True,
    )

    assert result.output_path == epub
    updated = read_epub_metadata(epub)
    assert updated.title == "Overwrite Title"
    with zipfile.ZipFile(epub) as archive:
        assert archive.read("OEBPS/Text/ch1.xhtml") == b"<html><body><p>Hello</p></body></html>"


def test_apply_metadata_can_compress_output(tmp_path: Path):
    epub = tmp_path / "book.epub"
    out = tmp_path / "book_metadata.epub"
    _write_epub(epub)

    result = apply_epub_metadata(
        epub,
        out,
        title="Compressed Title",
        compress=True,
    )

    assert result.compressed is True
    updated = read_epub_metadata(out)
    assert updated.title == "Compressed Title"
    with zipfile.ZipFile(out) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"


def test_apply_metadata_replaces_existing_cover(tmp_path: Path):
    epub = tmp_path / "book.epub"
    out = tmp_path / "book_metadata.epub"
    cover = tmp_path / "new.png"
    _write_epub(epub)
    cover.write_bytes(_image_bytes("PNG", (10, 220, 30)))

    result = apply_epub_metadata(epub, out, cover_path=str(cover))

    assert result.cover_updated is True
    with zipfile.ZipFile(out) as archive:
        assert archive.read("OEBPS/Images/cover.jpg") != _image_bytes()
    updated = read_epub_metadata(out)
    assert updated.cover_archive_path == "OEBPS/Images/cover.jpg"


def test_apply_metadata_adds_cover_without_rebuilding_spine(tmp_path: Path):
    epub = tmp_path / "book.epub"
    out = tmp_path / "book_metadata.epub"
    cover = tmp_path / "new.jpg"
    _write_epub(epub, with_cover=False)
    cover.write_bytes(_image_bytes("JPEG", (10, 30, 220)))

    apply_epub_metadata(epub, out, cover_path=str(cover))

    updated = read_epub_metadata(out)
    assert updated.has_cover is True
    assert updated.cover_archive_path == "OEBPS/Images/transoria_cover.jpg"
    opf = _read_opf(out)
    cover_item = opf.find(
        f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item[@properties='cover-image']"
    )
    assert cover_item is not None
    assert opf.find(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref").attrib["idref"] == "ch1"
