from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transoria.domain import Language, normalize_target_script, translated_filename


BILINGUAL_OUTPUT_FOLDER_EN = "bilingual outputs"

# Single-byte Western encodings round-trip arbitrary bytes (every
# 0x00–0xFF maps to some code point), which makes them false-positive
# winners of chardet's pick + round-trip check on short legacy-Asian
# inputs. Demote the entire family by prefix so the Phase-3 candidate
# list (Korean / Chinese / Japanese first) gets a chance.
_CHARDET_LATIN_PREFIXES: tuple[str, ...] = (
    "iso-8859-",  # iso-8859-1..16 — all single-byte Western/Cyrillic/Greek/Turkish
    "windows-125",  # windows-1250..1258 — same family, MS variants
    "cp125",  # cp1250..1258 (alias of windows-125x)
)
_CHARDET_LATIN_NAMES: frozenset[str] = frozenset(
    {"ascii", "latin-1", "latin_1", "macroman", "mac-roman"}
)


def _is_chardet_latin_false_positive(name: str) -> bool:
    if not name:
        return False
    lowered = name.lower().replace("_", "-")
    if lowered in _CHARDET_LATIN_NAMES:
        return True
    return any(lowered.startswith(prefix) for prefix in _CHARDET_LATIN_PREFIXES)

TXT_ENCODING_CANDIDATES = (
    # BOM-prefixed and UTF-8 variants come first.
    # UTF-16 is intentionally NOT in this list — its codec doesn't reject
    # arbitrary aligned byte sequences, so without a BOM it would silently
    # win over the Asian candidates below and produce gibberish. BOM-
    # prefixed UTF-16 is handled by the Phase-1 BOM check in
    # ``parse_txt_file``.
    "utf-8-sig",
    "utf-8",
    # Korean (project's primary fixtures live here).
    "cp949",
    "euc-kr",
    # Chinese: gb18030 is a strict superset of gbk so we try it last
    # among Chinese variants — anything that fails the others may still
    # land here.
    "gbk",
    "gb18030",
    "big5",
    # Japanese.
    "shift_jis",
    "euc-jp",
    # Vietnamese (Windows code page; covers UI locale's Vietnamese
    # source).
    "cp1258",
    # Russian / Cyrillic — KOI8-R is the legacy standard, cp1251 is
    # Windows. Order matters because cp1251 is more permissive (Latin-1-
    # like) and would otherwise match too eagerly. We rely on the
    # Phase-2 chardet stage having already filtered cp1251 if it really
    # wasn't the right encoding.
    "koi8-r",
    "cp1251",
    # Thai (Windows + ISO).
    "cp874",
    "iso-8859-11",
    # Arabic (Windows code page).
    "cp1256",
    # Turkish (Windows code page).
    "cp1254",
    # German / French / Spanish / Italian / Portuguese / Polish /
    # Hungarian — most modern files for these are UTF-8, so legacy
    # detection only needs to cover Western Europe with one Latin-1
    # superset (cp1252) and Central Europe with one (cp1250). These
    # come last because they're permissive and would otherwise mask
    # legitimate Asian decode failures.
    "cp1250",
    "cp1252",
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


def decode_text_bytes(raw: bytes) -> tuple[str, str]:
    """Run the same BOM → chardet → candidate-list cascade as
    ``parse_txt_file`` over a raw byte string. Returns ``(text, encoding)``.

    Reused by JSON / replacement-rule importers so the desktop UI never
    refuses a legacy-encoded file just because the surrounding wrapper
    expected UTF-8. Raises ``UnicodeDecodeError`` only when every phase
    fails, matching ``parse_txt_file``'s contract.
    """

    # Phase 1: stable BOM/UTF detection. UTF-8-with-BOM is unambiguous;
    # raw UTF-8 succeeds when the bytes are well-formed UTF-8. UTF-16 is
    # only attempted when a BOM is present — without one,
    # ``raw.decode("utf-16")`` happily consumes arbitrary byte pairs as
    # code units (e.g. cp949 bytes become Chinese-looking gibberish)
    # because the codec almost never raises on aligned input.
    text: str | None = None
    encoding_used = ""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        encoding_used = "utf-8" if encoding == "utf-8-sig" else encoding
        break

    if text is None and (raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
            encoding_used = "utf-16"
        except UnicodeDecodeError:
            text = None

    # Phase 2: chardet-based detection for legacy encodings. Multiple
    # legacy encodings (cp949, gbk, big5, shift_jis, …) have overlapping
    # byte ranges, so a naive "try-each" loop produces garbage when a
    # wrong-but-tolerant encoding wins. ``chardet`` looks at character-
    # frequency statistics to pick the most likely one. We trust the
    # pick only if decode + re-encode round-trips back to the original
    # bytes — that rules out wrong-encoding "successes" without needing
    # a high confidence threshold (which chardet rarely meets on short
    # inputs).
    if text is None:
        try:
            import chardet  # type: ignore[import-not-found]
        except ImportError:
            chardet = None  # type: ignore[assignment]
        detection: dict | None = None
        if chardet is not None:
            try:
                detection = chardet.detect(raw)
            except (OSError, ValueError):
                # AV quarantining chardet's idf.bin → fall through to Phase 3.
                detection = None
        if detection is not None:
            confidence = float(detection.get("confidence") or 0.0)
            detected = (detection.get("encoding") or "").lower()
            # Western single-byte encodings round-trip ANY byte sequence
            # (Latin-1 maps every 0x00–0xFF to a code point), so the
            # round-trip test is a no-op there. chardet often picks one
            # of these for short legacy-Korean (cp949) inputs because
            # the byte distribution looks slightly Western. Skip them
            # and let Phase 3 try cp949/euc-kr first.
            if (
                detected
                and confidence >= 0.3
                and not _is_chardet_latin_false_positive(detected)
            ):
                try:
                    candidate = raw.decode(detected)
                    if candidate.encode(detected) == raw:
                        text = candidate
                        encoding_used = detected
                except (UnicodeDecodeError, LookupError):
                    text = None

    # Phase 3: fall back to the candidate list in priority order.
    # Korean encodings come first because the project's primary fixtures
    # are Korean novels and chardet is least helpful on short Korean
    # names.
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
        raise UnicodeDecodeError(
            "utf-8", raw, 0, len(raw), "unsupported text encoding"
        )
    return text, encoding_used


def parse_txt_file(path: Path) -> TextDocument:
    text, encoding_used = decode_text_bytes(path.read_bytes())
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
    normalized = _normalize_translations(translations, target_language)
    output_path.write_text(_render_translated(document, normalized), encoding="utf-8")
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
    normalized = _normalize_translations(translations, target_language)
    output_path.write_text(
        _render_bilingual(document, normalized, dedup_when_same=dedup_when_same),
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


def _normalize_translations(
    translations: dict[int, str],
    target_language: Language,
) -> dict[int, str]:
    return {
        index: normalize_target_script(text, target_language)
        for index, text in translations.items()
    }


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
