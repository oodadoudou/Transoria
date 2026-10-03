import json
from pathlib import Path
import zipfile

import pytest

from tests.unit.formats.test_formats_epub_parser import _write_minimal_epub
from transoria.domain import Language
from transoria.formats.epub_parser import EpubTextKind, parse_epub_file, parse_xhtml_or_html
from transoria.formats.epub_writer import write_bilingual_epub, write_translated_epub


def _export(tmp_path, body, translation, *, bilingual=False):
    source = _write_minimal_epub(tmp_path / "book.epub", chapter_body=body)
    document = parse_epub_file(source)
    segment = next(s for s in document.segments if s.kind == EpubTextKind.BODY)
    translations = {segment.index: translation}
    if bilingual:
        written = write_bilingual_epub(
            document, translations, tmp_path / "out",
            source_language=Language.ENGLISH,
            target_language=Language.CHINESE_SIMPLIFIED,
        )
    else:
        written = write_translated_epub(
            document, translations, tmp_path / "out",
            target_language=Language.CHINESE_SIMPLIFIED,
        )
    with zipfile.ZipFile(written) as archive:
        assert archive.testzip() is None
        root = parse_xhtml_or_html(archive.read("OEBPS/Text/chapter.xhtml"))
    return source, written, root, segment


def _paragraph(root):
    return root.xpath(".//*[local-name()='p']")[0]


@pytest.mark.parametrize("prefix", [
    '<span class="stickup">A</span>',
    '<span class="publisher-any-name" style="font-size:150%;line-height:0">A</span>',
    '<span class="initial">\u201cA</span>',
    '<span class="initial"><em>A</em></span>',
    '<span class="initial">\u201c<em>A</em></span>',
])
@pytest.mark.parametrize("translation,initial", [
    ("新的正文完整可读。", "新"),
    ("\u201c新的正文完整可读。\u201d", "\u201c新"),
    ("e\u0301lan and another sentence.", "e\u0301"),
    ("\U0001f469\u200d\U0001f4bb writes a sentence.", "\U0001f469\u200d\U0001f4bb"),
])
def test_initials_do_not_expand_to_whole_paragraph(tmp_path, prefix, translation, initial):
    source, written, root, _ = _export(tmp_path, f"<p>{prefix} long opening sentence.</p>", translation)
    p = _paragraph(root)
    assert "".join(p[0].itertext()) == initial
    assert "".join(p.itertext()) == translation
    assert p[0].tail == translation[len(initial):]
    assert not written.with_suffix(".format-warnings.json").exists()
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(written) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name != "OEBPS/Text/chapter.xhtml":
                assert before.read(name) == after.read(name)


@pytest.mark.parametrize("translation", ["首字正文\n后文", "\n首字正文", "首\n字正文"])
def test_initial_scope_is_normalized_even_with_matching_line_count(tmp_path, translation):
    _, written, root, _ = _export(tmp_path, '<p><span class="cap">A</span> complete text.</p>', translation)
    p = _paragraph(root)
    assert p[0].text == "首"
    assert "".join(p.itertext()) == translation.replace("\n", "")
    assert not written.with_suffix(".format-warnings.json").exists()


def test_initial_next_to_italic_sibling_never_spills_into_italics(tmp_path):
    _, written, root, segment = _export(
        tmp_path, '<p><span class="cap">A</span><em>name</em> walks away.</p>',
        "\u201c一个名字走远了。\u201d",
    )
    p = _paragraph(root)
    assert p[0].text == "\u201c一"
    assert p[0].tail == "个名字走远了。\u201d"
    assert not p[1].text
    assert "".join(p.itertext()) == "\u201c一个名字走远了。\u201d"
    report = json.loads(written.with_suffix(".format-warnings.json").read_text())
    assert report["warnings"][0]["segment_index"] == segment.index


def test_initial_nested_inside_full_paragraph_wrapper_keeps_both_scopes(tmp_path):
    _, written, root, _ = _export(
        tmp_path,
        '<p><span class="whole"><span class="cap"><em>A</em></span> story.</span></p>',
        "首字正常的正文。",
    )
    whole = _paragraph(root)[0]
    assert whole.get("class") == "whole"
    assert whole[0][0].text == "首"
    assert whole[0].tail == "字正常的正文。"
    assert "".join(whole.itertext()) == "首字正常的正文。"
    assert not written.with_suffix(".format-warnings.json").exists()


def test_comment_before_initial_does_not_disable_scope_protection(tmp_path):
    _, written, root, _ = _export(
        tmp_path, '<p><!-- note --><span class="cap">A</span> story.</p>',
        "首字正文。",
    )
    p = _paragraph(root)
    assert p[0].text == " note "
    assert p[1].text == "首"
    assert p[1].tail == "字正文。"
    assert not written.with_suffix(".format-warnings.json").exists()


@pytest.mark.parametrize("wrapper", ["a", "mark", "sup"])
def test_initial_inside_partial_ancestor_cannot_style_entire_remainder(tmp_path, wrapper):
    _, written, root, _ = _export(
        tmp_path,
        f'<p><{wrapper}><span class="cap">A</span> word</{wrapper}> and normal prose.</p>',
        "首字和完整的普通正文。",
    )
    p = _paragraph(root)
    assert "".join(p[0].itertext()) == "首"
    assert p[0].tail == "字和完整的普通正文。"
    assert "".join(p.itertext()) == "首字和完整的普通正文。"
    assert written.with_suffix(".format-warnings.json").exists()


def test_initial_preserves_mapped_italic_and_bold_siblings(tmp_path):
    _, written, root, _ = _export(
        tmp_path, '<p><span class="cap">A</span><em>name</em> and <b>place</b>.</p>',
        "新开头\n名字\n和\n地点\n。",
    )
    p = _paragraph(root)
    assert p[0].text == "新"
    assert p[0].tail == "开头"
    assert p[1].text == "名字"
    assert p[2].text == "地点"
    assert "".join(p.itertext()) == "新开头名字和地点。"
    assert not written.with_suffix(".format-warnings.json").exists()


@pytest.mark.parametrize("inline", [
    '<span class="accent">An emphasized phrase</span>',
    '<a id="destination" href="#next">A link</a>',
    '<sup>1</sup>',
    '<sub>1</sub>',
    '<strong><em>A nested emphasized phrase</em></strong>',
    '<ruby>A<rt>annotation</rt></ruby>',
])
def test_unmapped_partial_styles_never_receive_entire_translation(tmp_path, inline):
    _, written, root, _ = _export(tmp_path, f"<p>{inline} with normal prose.</p>", "完整正文不会变成整段特殊样式。")
    p = _paragraph(root)
    assert p.text == "完整正文不会变成整段特殊样式。"
    assert not p[0].text
    assert "".join(p.itertext()) == "完整正文不会变成整段特殊样式。"
    assert written.with_suffix(".format-warnings.json").exists()
    if p[0].tag.endswith("a"):
        assert p[0].get("id") == "destination"
        assert p[0].get("href") == "#next"


def test_common_full_paragraph_style_is_retained(tmp_path):
    _, written, root, _ = _export(
        tmp_path, '<p><em class="whole">Opening<!-- marker --> and ending.</em></p>',
        "整段斜体依然保留。",
    )
    p = _paragraph(root)
    assert p[0].text == "整段斜体依然保留。"
    assert p[0].get("class") == "whole"
    assert p[0][0].text == " marker "
    assert not written.with_suffix(".format-warnings.json").exists()


def test_matching_boundaries_keep_all_localized_styles(tmp_path):
    _, written, root, _ = _export(
        tmp_path, '<p>Normal <em>italic</em> and <a href="#n">link</a><sup>2</sup>.</p>',
        "普通\n斜体\n和\n链接\n二\n。",
    )
    p = _paragraph(root)
    assert p[0].text == "斜体"
    assert p[1].text == "链接"
    assert p[2].text == "二"
    assert "".join(p.itertext()) == "普通斜体和链接二。"
    assert not written.with_suffix(".format-warnings.json").exists()


def test_empty_mapped_inline_text_is_reported(tmp_path):
    _, written, root, _ = _export(
        tmp_path, '<p>Normal <em>italic</em> ending.</p>', "普通正文\n\n结束。",
    )
    assert "".join(_paragraph(root).itertext()) == "普通正文结束。"
    assert written.with_suffix(".format-warnings.json").exists()


def test_extra_newlines_do_not_expand_initial_or_drop_any_text(tmp_path):
    translation = "首\n字正文\n强调词\n。\n额外换行"
    _, written, root, _ = _export(
        tmp_path, '<p><span class="cap">A</span> prose <em>emphasis</em>.</p>', translation,
    )
    p = _paragraph(root)
    assert p[0].text == "首"
    assert "".join(p.itertext()) == translation
    assert written.with_suffix(".format-warnings.json").exists()


def test_untranslated_source_and_original_bilingual_clone_are_unchanged(tmp_path):
    _, _, root, _ = _export(
        tmp_path, '<p><span class="cap">A</span> story <em>emphasis</em>.</p>',
        "首字正文强调。", bilingual=True,
    )
    paragraphs = root.xpath(".//*[local-name()='p']")
    assert len(paragraphs) == 2
    assert paragraphs[0][0].text == "A"
    assert paragraphs[0][1].text == "emphasis"
    assert paragraphs[0].get("style") == "opacity:0.50;"
    assert paragraphs[1][0].text == "首"
    assert "".join(paragraphs[1].itertext()) == "首字正文强调。"


def test_source_echo_keeps_original_ruby_annotations(tmp_path):
    body = '<p>Before <ruby>word<rt>reading</rt></ruby> after.</p>'
    source = _write_minimal_epub(tmp_path / "book.epub", chapter_body=body)
    document = parse_epub_file(source)
    segment = next(s for s in document.segments if s.kind == EpubTextKind.BODY)
    written = write_translated_epub(
        document, {segment.index: segment.text}, tmp_path / "out",
        target_language=Language.ENGLISH,
    )
    with zipfile.ZipFile(written) as archive:
        root = parse_xhtml_or_html(archive.read("OEBPS/Text/chapter.xhtml"))
    assert root.xpath(".//*[local-name()='rt']")[0].text == "reading"


def test_report_is_removed_after_boundaries_are_corrected(tmp_path):
    source, written, _, segment = _export(
        tmp_path, '<p>Before <em>word</em> after.</p>', "前面强调词后面。",
    )
    report_path = written.with_suffix(".format-warnings.json")
    assert report_path.exists()
    write_translated_epub(
        parse_epub_file(source), {segment.index: "前面\n强调词\n后面。"}, written.parent,
        target_language=Language.CHINESE_SIMPLIFIED,
    )
    assert not report_path.exists()


def test_report_failure_does_not_discard_successfully_saved_epub(tmp_path, monkeypatch):
    original = Path.write_text

    def write_text(path, *args, **kwargs):
        if "format-warnings" in path.name:
            raise PermissionError("report directory is read-only")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)
    _, written, root, _ = _export(
        tmp_path, '<p><em>Opening</em> rest.</p>', "完整译文。",
    )
    assert written.exists()
    assert "".join(_paragraph(root).itertext()) == "完整译文。"
    assert not list(written.parent.glob(".*.tmp"))


@pytest.mark.parametrize("translation", ["前面强调词后面。", "前面\n强调词\n后面。"])
def test_report_never_overwrites_or_deletes_an_unrelated_file(tmp_path, translation):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    unrelated = output_dir / "book-zh.format-warnings.json"
    unrelated.write_text('{"user": "keep this"}', encoding="utf-8")
    _export(tmp_path, '<p>Before <em>word</em> after.</p>', translation)
    assert unrelated.read_text(encoding="utf-8") == '{"user": "keep this"}'


def test_report_does_not_contain_private_prose(tmp_path):
    _, written, _, _ = _export(
        tmp_path, '<p>Private opening <em>private word</em> private ending.</p>', "私人译文。",
    )
    text = written.with_suffix(".format-warnings.json").read_text(encoding="utf-8")
    assert "Private opening" not in text
    assert "private word" not in text
    assert "私人译文" not in text


def test_fallback_inserts_at_segment_position_not_before_unrelated_content(tmp_path):
    body = '<section>Unchanged<p>Next block</p><span><em>Before</em> rest.</span></section>'
    source = _write_minimal_epub(tmp_path / "book.epub", chapter_body=body)
    document = parse_epub_file(source)
    segment = next(s for s in document.segments if s.text == "Before\nrest.")
    written = write_translated_epub(
        document, {segment.index: "后续的完整译文。"}, tmp_path / "out",
        target_language=Language.CHINESE_SIMPLIFIED,
    )
    with zipfile.ZipFile(written) as archive:
        root = parse_xhtml_or_html(archive.read("OEBPS/Text/chapter.xhtml"))
    section = root.xpath(".//*[local-name()='section']")[0]
    assert section.text == "Unchanged"
    assert section[0].text == "Next block"
    assert section[1].text == "后续的完整译文。"
    assert not section[1][0].text
    assert "".join(section.itertext()) == "UnchangedNext block后续的完整译文。"


@pytest.mark.parametrize("annotation", ["rt", "rp"])
def test_removing_ruby_annotations_keeps_their_translated_tails(tmp_path, annotation):
    _, _, root, _ = _export(
        tmp_path, f'<p><ruby>A<{annotation}>reading</{annotation}>B</ruby> end.</p>',
        "甲\n乙\n结束。",
    )
    p = _paragraph(root)
    assert "".join(p.itertext()) == "甲乙结束。"
    assert not p.xpath(".//*[local-name()='rt' or local-name()='rp']")


def test_consecutive_ruby_annotations_preserve_all_base_characters(tmp_path):
    _, _, root, _ = _export(
        tmp_path, '<p><ruby>A<rt>a</rt>B<rt>b</rt>C</ruby> end.</p>',
        "甲\n乙\n丙\n结束。",
    )
    assert "".join(_paragraph(root).itertext()) == "甲乙丙结束。"


def test_ruby_cleanup_does_not_modify_untranslated_descendant_blocks(tmp_path):
    source = _write_minimal_epub(
        tmp_path / "book.epub",
        chapter_body='<section><ruby>A<rt>a</rt></ruby><p><ruby>B<rt>b</rt></ruby></p></section>',
    )
    doc = parse_epub_file(source)
    segment = next(s for s in doc.segments if s.text == "A")
    written = write_translated_epub(doc, {segment.index: "甲"}, tmp_path / "out", target_language=Language.CHINESE_SIMPLIFIED)
    with zipfile.ZipFile(written) as archive:
        root = parse_xhtml_or_html(archive.read("OEBPS/Text/chapter.xhtml"))
    assert _paragraph(root).xpath(".//*[local-name()='rt']")[0].text == "b"


def test_bilingual_nested_blocks_and_tails_are_not_duplicated(tmp_path):
    source = _write_minimal_epub(
        tmp_path / "book.epub", chapter_body='<section>Before<p>Inner text</p>After</section>',
    )
    doc = parse_epub_file(source)
    translated = {s.index: "译" + s.text for s in doc.segments if s.kind == EpubTextKind.BODY}
    written = write_bilingual_epub(doc, translated, tmp_path / "out", source_language=Language.ENGLISH, target_language=Language.CHINESE_SIMPLIFIED)
    with zipfile.ZipFile(written) as archive:
        root = parse_xhtml_or_html(archive.read("OEBPS/Text/chapter.xhtml"))
    sections = root.xpath(".//*[local-name()='section']")
    assert len(sections) == 2
    assert "".join(sections[0].itertext()) == "BeforeInner textAfter"
    assert "".join(sections[1].itertext()) == "译Before译Inner text译After"
    assert len(root.xpath(".//*[local-name()='p']")) == 2


def test_bilingual_parent_snapshot_is_taken_before_translating_children(tmp_path):
    source = _write_minimal_epub(
        tmp_path / "book.epub", chapter_body='<section><p>Inner text</p>After</section>',
    )
    doc = parse_epub_file(source)
    translated = {s.index: "译" + s.text for s in doc.segments if s.kind == EpubTextKind.BODY}
    written = write_bilingual_epub(doc, translated, tmp_path / "out", source_language=Language.ENGLISH, target_language=Language.CHINESE_SIMPLIFIED)
    with zipfile.ZipFile(written) as archive:
        root = parse_xhtml_or_html(archive.read("OEBPS/Text/chapter.xhtml"))
    sections = root.xpath(".//*[local-name()='section']")
    assert "".join(sections[0].itertext()) == "Inner textAfter"
    assert "".join(sections[1].itertext()) == "译Inner text译After"


def test_bilingual_copy_excludes_parent_tail_and_duplicate_anchors(tmp_path):
    _, _, root, _ = _export(
        tmp_path,
        '<section><p id="chapter" xml:id="xml-chapter" class="prose" style="color:red">'
        'Before <a id="anchor" name="legacy" href="#chapter">link</a></p>Outside</section>',
        "正文\n链接", bilingual=True,
    )
    paragraphs = root.xpath(".//*[local-name()='p']")
    clone, translated = paragraphs
    assert clone.tail == "\n"
    assert translated.tail == "Outside"
    assert root.xpath('count(//*[@id="chapter"])') == 1
    assert root.xpath('count(//*[@id="anchor"])') == 1
    assert root.xpath('count(//*[@name="legacy"])') == 1
    assert clone.get("class") == "prose"
    assert clone.get("style") == "color:red;opacity:0.50;"
    assert clone[0].get("href") == "#chapter"
    assert root.xpath("//*[@id='chapter']")[0] is translated
