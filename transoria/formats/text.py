from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transoria.domain import Language, translated_filename


BILINGUAL_OUTPUT_FOLDER_EN = "bilingual outputs"

TXT_ENCODING_CANDIDATES = (
    # BOM-prefixed and UTF variants are tried first because they're the most
    # likely to decode without ambiguity.
    "utf-8-sig",
    "utf-8",
    "utf-16",
    # Korean encodings (existing test fixtures).
    "cp949",
    "euc-kr",
    # Chinese encodings (Simplified covers Mainland; Traditional covers
    # Taiwan/HK). gb18030 is a strict superset of gbk so we try it last
    # among Chinese variants — anything that fails the others may still
    # land here.
    "gbk",
    "gb18030",
    "big5",
    # Japanese.
    "shift_jis",
    "euc-jp",
)


@dataclass(frozen=True)
class TextSegment:
    index: int
    text: str
    newline: str


@dataclass(frozen=True)
class TextDocument:
    path: Path
    encoding: str
    segments: list[TextSegment]


def parse_txt_file(path: Path) -> TextDocument:
    raw = path.read_bytes()
    text: str | None = None
    encoding_used = ""

    # Phase 1: stable BOM/UTF detection. UTF-8-with-BOM is unambiguous; raw
    # UTF-8 succeeds when the bytes are well-formed UTF-8.
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        encoding_used = "utf-8" if encoding == "utf-8-sig" else encoding
        break

    # Phase 2: chardet-based detection for legacy encodings. Multiple legacy
    # encodings (cp949, gbk, big5, shift_jis, …) have overlapping byte
    # ranges, so a naive "try-each" loop produces garbage when a wrong-but-
    # tolerant encoding wins. ``chardet`` looks at character-frequency
    # statistics to pick the most likely one. We trust the pick only if
    # decode + re-encode round-trips back to the original bytes — that
    # rules out wrong-encoding "successes" without needing a high
    # confidence threshold (which chardet rarely meets on short inputs).
    if text is None:
        try:
            import chardet  # type: ignore[import-not-found]
        except ImportError:
            chardet = None  # type: ignore[assignment]
        if chardet is not None:
            detection = chardet.detect(raw)
            confidence = float(detection.get("confidence") or 0.0)
            detected = (detection.get("encoding") or "").lower()
            # Need both modest confidence AND a clean round-trip. Confidence
            # alone is unreliable on short inputs (chardet often picks an
            # unrelated Asian encoding with confidence < 0.1); round-trip
            # alone is unreliable for byte ranges shared by many encodings
            # (e.g. gb18030 swallows cp949 bytes). The combination filters
            # both failure modes.
            if detected and confidence >= 0.3:
                try:
                    candidate = raw.decode(detected)
                    if candidate.encode(detected) == raw:
                        text = candidate
                        encoding_used = detected
                except (UnicodeDecodeError, LookupError):
                    text = None

    # Phase 3: fall back to the candidate list in priority order. Korean
    # encodings come first because the project's primary fixtures are
    # Korean novels and chardet is least helpful on short Korean names.
    if text is None:
        for encoding in TXT_ENCODING_CANDIDATES:
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            encoding_used = (
                "utf-8" if encoding == "utf-8-sig" else encoding
            )
            break

    if text is None:
        raise UnicodeDecodeError("utf-8", raw, 0, len(raw), "unsupported text encoding")

    return TextDocument(path=path, encoding=encoding_used, segments=_split_segments(text))


def write_translated_txt(
    document: TextDocument,
    translations: dict[int, str],
    output_dir: Path,
    *,
    target_language: Language,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / translated_filename(document.path, target_language)
    output_path.write_text(_render_translated(document, translations), encoding="utf-8")
    return output_path


def write_bilingual_txt(
    document: TextDocument,
    translations: dict[int, str],
    output_dir: Path,
    *,
    source_language: Language,
    target_language: Language,
    subfolder: str = BILINGUAL_OUTPUT_FOLDER_EN,
    dedup_when_same: bool = True,
) -> Path:
    bilingual_dir = output_dir / subfolder
    bilingual_dir.mkdir(parents=True, exist_ok=True)
    output_path = bilingual_dir / translated_filename(
        document.path,
        target_language,
        source_language=source_language,
        bilingual=True,
    )
    output_path.write_text(
        _render_bilingual(document, translations, dedup_when_same=dedup_when_same),
        encoding="utf-8",
    )
    return output_path


def _split_segments(text: str) -> list[TextSegment]:
    segments: list[TextSegment] = []
    index = 0
    offset = 0
    text_length = len(text)

    while offset < text_length:
        line_end = offset
        while line_end < text_length and text[line_end] not in "\r\n":
            line_end += 1

        newline = ""
        if line_end < text_length:
            if text[line_end : line_end + 2] == "\r\n":
                newline = "\r\n"
                next_offset = line_end + 2
            else:
                newline = text[line_end]
                next_offset = line_end + 1
        else:
            next_offset = line_end

        segments.append(TextSegment(index=index, text=text[offset:line_end], newline=newline))
        index += 1
        offset = next_offset

    if not segments:
        return [TextSegment(index=0, text="", newline="")]

    return segments


def _render_translated(document: TextDocument, translations: dict[int, str]) -> str:
    parts: list[str] = []
    for segment in document.segments:
        text = translations.get(segment.index, segment.text)
        parts.append(f"{text}{segment.newline}")
    return "".join(parts)


def _render_bilingual(
    document: TextDocument,
    translations: dict[int, str],
    *,
    dedup_when_same: bool = True,
) -> str:
    parts: list[str] = []
    for segment in document.segments:
        translated = translations.get(segment.index, segment.text)
        if segment.text == "":
            parts.append(segment.newline)
            continue
        parts.append(f"{segment.text}{segment.newline}")
        if dedup_when_same and translated == segment.text:
            continue
        parts.append(f"{translated}{segment.newline}")
    return "".join(parts)

