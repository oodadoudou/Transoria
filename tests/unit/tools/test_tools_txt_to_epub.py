from pathlib import Path
import zipfile

import pytest

from transoria.tools.txt_to_epub import (
    TxtToEpubOptions,
    TxtToEpubTocEntry,
    build_txt_to_epub_plan,
    convert_txt_to_epub,
    list_epub_styles,
    list_toc_presets,
    locate_txt_toc_entry,
    scan_txt_toc,
)


def _write_txt(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "novel.txt"
    path.write_text("\n正文\n".join(lines), encoding="utf-8")
    return path


def _scan_titles(
    tmp_path: Path,
    preset_id: str,
    lines: list[str],
    *,
    custom_rules: list[dict[str, object]] | None = None,
    advanced_pattern: str = "",
) -> list[tuple[int, str]]:
    result = scan_txt_toc(
        _write_txt(tmp_path, lines),
        preset_id=preset_id,
        custom_rules=custom_rules,
        advanced_pattern=advanced_pattern,
    )
    return [(entry["level"], entry["title"]) for entry in result["entries"]]


def test_toc_presets_merge_chinese_variants() -> None:
    ids = [preset["id"] for preset in list_toc_presets()["presets"]]

    assert "zh_novel" in ids
    assert "zh_webnovel" not in ids
    assert "zh_published" not in ids
    assert "extra" not in ids


def test_epub_style_list_is_curated_for_reader_compatibility() -> None:
    style_ids = [style["id"] for style in list_epub_styles()["styles"]]

    assert style_ids == [
        "basic:classic",
        "basic:clean",
        "basic:eyecare",
        "basic:modern",
        "basic:minimal",
        "basic:literary",
        "basic:compact",
        "basic:spacious",
        "basic:double_line",
        "basic:sans_clean",
        "basic:framed",
        "basic:sidebar",
        "basic:structure_lines",
        "basic:reader_modern",
        "enhanced:soft_structure",
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("第1卷 春日", (1, "第1卷 春日")),
        ("第 001 章：重逢", (2, "第 001 章：重逢")),
        ("第 一 章：醒来", (2, "第 一 章：醒来")),
        ("正文 第十二章 - 暗涌", (2, "正文 第十二章 - 暗涌")),
        ("正文卷 第一章：重逢", (2, "正文卷 第一章：重逢")),
        ("卷 一 序幕", (1, "卷 一 序幕")),
        ("上卷 春日", (1, "上卷 春日")),
        ("第一回：旧梦", (2, "第一回：旧梦")),
        ("第一节 - 风声", (2, "第一节 - 风声")),
        ("第一幕 - 雨夜", (2, "第一幕 - 雨夜")),
        ("序章：雨夜", (1, "序章：雨夜")),
        ("尾声 - 归处", (1, "尾声 - 归处")),
        ("后记：作者的话", (1, "后记：作者的话")),
        ("番外 三 生日", (2, "番外 三 生日")),
        ("外传：另一条路", (1, "外传：另一条路")),
        ("特别篇 - 夏日", (1, "特别篇 - 夏日")),
        ("IF 线：如果", (1, "IF 线：如果")),
    ],
)
def test_zh_novel_preset_matches_common_heading_symbols(
    tmp_path: Path,
    line: str,
    expected: tuple[int, str],
) -> None:
    assert _scan_titles(tmp_path, "zh_novel", [line]) == [expected]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# 第一卷", (1, "第一卷")),
        ("## 第一章", (2, "第一章")),
        ("### 第一节", (3, "第一节")),
        ("#### 第一幕", (4, "第一幕")),
    ],
)
def test_markdown_preset_supports_four_heading_levels(
    tmp_path: Path,
    line: str,
    expected: tuple[int, str],
) -> None:
    assert _scan_titles(tmp_path, "markdown", [line]) == [expected]


def test_markdown_preset_ignores_numeric_headings(tmp_path: Path) -> None:
    assert _scan_titles(
        tmp_path,
        "markdown",
        ["1. Opening", "1.1 Detail", "001", "38.7度。即便频繁使用医生留下的体温计测量。"],
    ) == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("프롤로그", (1, "프롤로그")),
        ("서장", (1, "서장")),
        ("제 1 권 시작", (1, "제 1 권 시작")),
        ("제 2 부 시작", (1, "제 2 부 시작")),
        ("제 12 화 재회", (2, "제 12 화 재회")),
        ("제 3 장 진실", (2, "제 3 장 진실")),
        ("14화 고백", (2, "14화 고백")),
        ("3편 재회", (1, "3편 재회")),
        ("번외 둘만의 밤", (1, "번외 둘만의 밤")),
        ("특별편 선물", (1, "특별편 선물")),
        ("외전 둘만의 밤", (1, "외전 둘만의 밤")),
        ("에필로그 끝", (1, "에필로그 끝")),
    ],
)
def test_ko_novel_preset_matches_episode_volume_and_specials(
    tmp_path: Path,
    line: str,
    expected: tuple[int, str],
) -> None:
    assert _scan_titles(tmp_path, "ko_novel", [line]) == [expected]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("プロローグ", (1, "プロローグ")),
        ("第1巻 春", (1, "第1巻 春")),
        ("第 2 話 再会", (2, "第 2 話 再会")),
        ("三章 真実", (2, "三章 真実")),
        ("外伝 ふたりの夜", (1, "外伝 ふたりの夜")),
        ("エピローグ 終わり", (1, "エピローグ 終わり")),
    ],
)
def test_ja_novel_preset_matches_episode_volume_and_specials(
    tmp_path: Path,
    line: str,
    expected: tuple[int, str],
) -> None:
    assert _scan_titles(tmp_path, "ja_novel", [line]) == [expected]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Prologue: Before", (1, "Prologue: Before")),
        ("Volume 2 - Winter", (1, "Volume 2 - Winter")),
        ("Chapter 03: Truth", (2, "Chapter 03: Truth")),
        ("Epilogue - After", (1, "Epilogue - After")),
    ],
)
def test_english_preset_matches_volume_chapter_and_bookends(
    tmp_path: Path,
    line: str,
    expected: tuple[int, str],
) -> None:
    assert _scan_titles(tmp_path, "en_chapter", [line]) == [expected]


def test_numeric_preset_keeps_simple_numbered_headings(tmp_path: Path) -> None:
    assert _scan_titles(tmp_path, "numeric", ["1.", "1. Opening", "1.1 Detail", "1、开端", "001"]) == [
        (1, "1."),
        (1, "1. Opening"),
        (2, "1.1 Detail"),
        (1, "1、开端"),
        (2, "001"),
    ]


def test_scan_toc_entries_include_confidence(tmp_path: Path) -> None:
    source = _write_txt(tmp_path, ["第1章 睡觉"])

    result = scan_txt_toc(source, preset_id="zh_novel")

    assert result["entries"][0]["confidence"] >= 0.85


def test_numeric_only_headings_are_lower_confidence(tmp_path: Path) -> None:
    source = _write_txt(tmp_path, ["1."])

    result = scan_txt_toc(source, preset_id="numeric")

    assert result["entries"][0]["confidence"] < 0.7


@pytest.mark.parametrize("preset_id", ["markdown", "zh_novel", "ko_novel", "ja_novel", "en_chapter"])
def test_chapter_presets_include_numeric_fallback_except_markdown(
    tmp_path: Path,
    preset_id: str,
) -> None:
    if preset_id == "markdown":
        assert _scan_titles(tmp_path, preset_id, ["1. Opening", "1.1 Detail", "001"]) == []
        return
    assert _scan_titles(tmp_path, preset_id, ["1. Opening", "1.1 Detail", "001"]) == [
        (1, "1. Opening"),
        (2, "1.1 Detail"),
        (2, "001"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "365일 내내 발정기를 겪고 있는 듯한 마수는 주헌을 볼 때마다 제 자지를 세웠다.",
        "2m는 족히 넘을 거대한 놈이 육탄공세를 한 덕분에 주헌은 서 있던 자세에서 그대로 뒤로 자빠졌다.",
        "3주나 흘렀음에도 주헌의 체취 구석구석에 제 페로몬이 깃들어 있었다.",
        "42년 전에 발생한 2차 던전 브레이크 이후로 터진 적 없던 대재앙이 갑작스럽게 찾아온 건 지금으로부터 13년 전이었다.",
    ],
)
def test_numeric_fallback_ignores_digit_prefixed_body_text(
    tmp_path: Path,
    line: str,
) -> None:
    assert _scan_titles(tmp_path, "ko_novel", [line]) == []


def test_locate_toc_entry_finds_user_text_and_skips_used_lines(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text(
        "프롤로그\n본문\n제1화 다시 만난 날\n본문\n제1화 다시 만난 날\n",
        encoding="utf-8",
    )

    first = locate_txt_toc_entry(source, query="다시 만난", level=2)
    second = locate_txt_toc_entry(
        source,
        query="제1화 다시 만난 날",
        level=3,
        used_start_lines=[int(first["startLine"])],
    )

    assert first["title"] == "제1화 다시 만난 날"
    assert first["level"] == 2
    assert first["startLine"] == 3
    assert second["level"] == 3
    assert second["startLine"] == 5


def test_locate_toc_entry_reports_unmatched_text(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("프롤로그\n본문\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot find TOC text"):
        locate_txt_toc_entry(source, query="없는 제목", level=1)


def test_txt_to_epub_without_toc_promotes_source_title_not_placeholder(
    tmp_path: Path,
) -> None:
    source = tmp_path / "korean.txt"
    output_dir = tmp_path / "out"
    source.write_text("던전 보스에게 고백받은 썰 푼다\n전국모브대축제\n", encoding="utf-8")
    plan = build_txt_to_epub_plan(
        TxtToEpubOptions(
            source_path=str(source),
            output_dir=str(output_dir),
            title="합병",
            overwrite=True,
        )
    )

    result = convert_txt_to_epub(plan.action)

    assert result.status == "converted"
    with zipfile.ZipFile(plan.output_path) as archive:
        chapter = archive.read("OEBPS/Text/chapter_0001.xhtml").decode("utf-8")
    assert "던전 보스에게 고백받은 썰 푼다" in chapter
    assert ">正文<" not in chapter


def test_txt_to_epub_keeps_prefix_text_out_of_toc_when_chapters_are_scanned(
    tmp_path: Path,
) -> None:
    source = tmp_path / "freedom.txt"
    output_dir = tmp_path / "out"
    source.write_text(
        "Freedom + 番外 by 鹿9少年 (上)\n\n文案：\n简介正文\n\n第1章 睡觉\n正文内容\n",
        encoding="utf-8",
    )
    scan = scan_txt_toc(source, preset_id="zh_novel")
    plan = build_txt_to_epub_plan(
        TxtToEpubOptions(
            source_path=str(source),
            output_dir=str(output_dir),
            title="Freedom",
            author="鹿9少年",
            toc_entries=tuple(
                TxtToEpubTocEntry.from_mapping(entry) for entry in scan["entries"]
            ),
            overwrite=True,
        )
    )

    result = convert_txt_to_epub(plan.action)

    assert result.status == "converted"
    assert result.toc_entries == 1
    with zipfile.ZipFile(plan.output_path) as archive:
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        toc = archive.read("OEBPS/toc.ncx").decode("utf-8")
        prefix = archive.read("OEBPS/Text/chapter_0001.xhtml").decode("utf-8")
        first_chapter = archive.read("OEBPS/Text/chapter_0002.xhtml").decode("utf-8")

    assert '<a href="Text/chapter_0001.xhtml">Freedom + 番外 by 鹿9少年 (上)</a>' not in nav
    assert "<navLabel><text>Freedom + 番外 by 鹿9少年 (上)</text></navLabel>" not in toc
    assert '<a href="Text/chapter_0002.xhtml">第1章 睡觉</a>' in nav
    assert "<h1>Freedom + 番外 by 鹿9少年 (上)</h1>" not in prefix
    assert "Freedom + 番外 by 鹿9少年 (上)" in prefix
    assert "<h2>第1章 睡觉</h2>" in first_chapter


def test_custom_rules_support_named_and_first_capture_fallback(tmp_path: Path) -> None:
    assert _scan_titles(
        tmp_path,
        "",
        ["@ 第一幕"],
        custom_rules=[{"level": 3, "pattern": r"^@\s*(?P<title>.+)$"}],
    ) == [(3, "第一幕")]

    assert _scan_titles(
        tmp_path,
        "",
        ["PART 2 - Arrival"],
        custom_rules=[{"level": 1, "pattern": r"^PART\s+\d+\s+-\s+(.+)$"}],
    ) == [(1, "Arrival")]


def test_advanced_regex_takes_precedence_over_presets(tmp_path: Path) -> None:
    assert _scan_titles(
        tmp_path,
        "markdown",
        ["~~ 幕间"],
        advanced_pattern=r"^~~\s*(?P<title>.+)$",
    ) == [(1, "幕间")]
