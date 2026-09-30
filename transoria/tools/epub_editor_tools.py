from __future__ import annotations

import copy
import difflib
import hashlib
import io
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
from itertools import islice
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import cssselect2
import regex
import tinycss2
from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError
from lxml import etree
from lxml import html as lxml_html
from PIL import Image
from spylls.hunspell import Dictionary

from transoria.tools.epub_content import (
    MAX_PREVIEW_BYTES, MAX_TEXT_BYTES, OPF, XHTML, XML_TYPES, ContentSession,
    _decode, _document_ids, _editor_text, _encode, _serialize, _write_nav, _write_ncx, _xml,
)
from transoria.formats.epub_paths import resolve_epub_href


def staged_entries(session: ContentSession) -> dict[str, bytes]:
    with zipfile.ZipFile(session.path) as source:
        size = sum(info.file_size for info in source.infolist() if info.filename not in session.removed and info.filename not in session.changes) + sum(len(data) for data in session.changes.values())
        if size > MAX_PREVIEW_BYTES * 4:
            raise ValueError("Draft inspection exceeds the 192 MB resource budget.")
        result = {info.filename: source.read(info) for info in source.infolist() if info.filename not in session.removed and info.filename not in session.changes}
    result.update(session.changes)
    package = _xml(result[session.opf_path])
    manifest = package.find(f"{{{OPF}}}manifest")
    spine = package.find(f"{{{OPF}}}spine")
    if manifest is None or spine is None:
        raise ValueError("The package has no manifest or spine.")
    paths = {resolve_epub_href(posixpath.dirname(session.opf_path), item.get("href", "")): item.get("id") for item in manifest}
    old = {item.get("idref"): item for item in spine}
    refs = []
    changed = False
    for path in session.spine:
        if path not in paths:
            raise ValueError(f"Reading-order item is missing from manifest: {path}")
        ref = old.get(paths[path])
        if ref is None:
            ref = etree.Element(f"{{{OPF}}}itemref", idref=paths[path])
            changed = True
        if (ref.get("linear") != "no") != session.spine_linear.get(path, True):
            ref.set("linear", "yes" if session.spine_linear.get(path, True) else "no")
            changed = True
        refs.append(ref)
    if list(spine) != refs or changed:
        for item in list(spine):
            spine.remove(item)
        spine.extend(refs)
        result[session.opf_path] = _serialize(package)
    # Only serialize navigation when its logical contents changed.
    baseline = ContentSession.open(str(session.path))
    if session.toc != baseline.toc or session.nav_path != baseline.nav_path:
        if session.nav_path:
            result[session.nav_path] = _write_nav(result[session.nav_path], session.nav_path, session.toc)
        if session.ncx_path:
            result[session.ncx_path] = _write_ncx(result[session.ncx_path], session.ncx_path, session.toc)
    return result


def compare(session: ContentSession, checkpoint: str = "") -> dict[str, object]:
    after = staged_entries(session)
    if checkpoint:
        other = copy.copy(session)
        state = session.checkpoints.get(checkpoint)
        if state is None:
            raise ValueError("Checkpoint does not exist.")
        (other.changes, other.spine, other.toc, other.files, other.removed, other.nav_path, other.ncx_path, other.spine_linear) = copy.deepcopy(state)
        before = staged_entries(other)
    else:
        with zipfile.ZipFile(session.path) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_PREVIEW_BYTES * 4:
                raise ValueError("Source comparison exceeds the 192 MB resource budget.")
            before = {info.filename: archive.read(info) for info in archive.infolist()}
    rows = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        item = next((file for file in session.files if file["path"] == path), None)
        textual = path == session.opf_path or bool(item and item["editable"])
        difference = ""
        if textual and max(len(old or b""), len(new or b"")) <= MAX_TEXT_BYTES:
            try:
                old_text = _decode(old)[0] if old else ""
                new_text = _decode(new)[0] if new else ""
                difference = "".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True), fromfile=path, tofile=path, n=3))[:200_000]
            except ValueError:
                pass
        rows.append({"path": path, "status": "added" if old is None else "removed" if new is None else "modified", "before_size": len(old or b""), "after_size": len(new or b""), "diff": difference})
    return {"rows": rows, "count": len(rows)}


def issues(session: ContentSession) -> dict[str, object]:
    rows = []
    ids: dict[str, set[str]] = {}
    links: list[tuple[str, int, str]] = []
    known = session._resource_paths()
    def add(path: str, line: int, kind: str, message: str):
        rows.append({"path": path, "line": line, "kind": kind, "message": message})
    for item in [{"path": session.opf_path, "media_type": "application/xml"}, *session.files]:
        path, media = str(item["path"]), str(item["media_type"])
        if media not in XML_TYPES | {"application/xhtml+xml", "text/html", "text/css"}:
            continue
        data = session._bytes(path)
        if len(data) > MAX_TEXT_BYTES:
            add(path, 1, "warning", "Resource exceeds the inspection limit.")
            continue
        if media == "text/css":
            rules = tinycss2.parse_stylesheet(_decode(data)[0], skip_comments=True, skip_whitespace=True)
            def inspect(tokens):
                for token in tokens:
                    if token.type == "error":
                        add(path, token.source_line, "css", token.message)
                    if token.type == "url":
                        links.append((path, token.source_line, token.value))
                    elif token.type == "function" and token.lower_name == "url":
                        values = [value for value in token.arguments if value.type not in {"whitespace", "comment"}]
                        if len(values) == 1 and values[0].type == "string":
                            links.append((path, token.source_line, values[0].value))
                    elif token.type == "at-rule" and token.lower_at_keyword == "import":
                        values = [value for value in token.prelude if value.type not in {"whitespace", "comment"}]
                        if values and values[0].type == "string":
                            links.append((path, values[0].source_line, values[0].value))
                    if getattr(token, "prelude", None) is not None:
                        inspect(token.prelude)
                    if getattr(token, "arguments", None) is not None:
                        inspect(token.arguments)
                    if getattr(token, "content", None) is not None:
                        inspect(token.content)
            inspect(rules)
            continue
        try:
            content = _editor_text(data, media)
            root = _xml(_encode(content, _decode(data)[1]))
        except etree.XMLSyntaxError as exc:
            add(path, exc.position[0], "xml", str(exc))
            continue
        found = Counter()
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            identifier = node.get("id") or node.get("{http://www.w3.org/XML/1998/namespace}id")
            if identifier:
                found[identifier] += 1
                if found[identifier] > 1:
                    add(path, node.sourceline or 1, "duplicate_id", identifier)
            for key, value in node.attrib.items():
                if etree.QName(key).localname in {"src", "href", "poster", "data"}:
                    links.append((path, node.sourceline or 1, value))
        ids[path] = _document_ids(data)
    for path, line, href in links:
        parts = urlsplit(href)
        if parts.scheme or href.startswith("//"):
            continue
        target = resolve_epub_href(posixpath.dirname(path), parts.path) if parts.path else path
        if target not in known:
            add(path, line, "missing_resource", href)
        elif parts.fragment and not parts.fragment.startswith("epubcfi(") and target in ids and unquote(parts.fragment) not in ids[target]:
            add(path, line, "missing_anchor", href)
    return {"rows": rows[:5000], "count": len(rows), "truncated": len(rows) > 5000}


def text_report(session: ContentSession, dictionary_path: str = "", ignored: list[str] | None = None) -> dict[str, object]:
    dictionary: set[str] | None = None
    hunspell: Dictionary | None = None
    if dictionary_path:
        path = Path(dictionary_path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError("Select a UTF-8 dictionary under 4 MB, one word per line.")
        if path.suffix.lower() == ".dic":
            affixes = path.with_suffix(".aff")
            if not affixes.is_file() or affixes.stat().st_size > MAX_TEXT_BYTES:
                raise ValueError("Select a Hunspell .dic with its matching .aff file under 4 MB.")
            try:
                hunspell = Dictionary.from_files(str(path.with_suffix("")))
            except (ValueError, IndexError, KeyError, AssertionError) as exc:
                raise ValueError("The Hunspell dictionary could not be read.") from exc
        else:
            dictionary = {line.strip().casefold() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    ignored_words = {word.casefold() for word in ignored or []}
    allowed_words = (dictionary or set()) | ignored_words
    suggestions: dict[str, list[str]] = {}
    correct: dict[str, bool] = {}
    rows, words = [], Counter()
    for path in session.spine:
        data = session._bytes(path)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(f"Chapter exceeds the text report limit: {path}")
        try:
            content = _editor_text(data, "application/xhtml+xml")
            root = _xml(_encode(content, _decode(data)[1]))
        except etree.XMLSyntaxError:
            root = lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True))
        for node in root.iter():
            if not isinstance(node.tag, str) or etree.QName(node).localname in {"head", "style", "script"}:
                continue
            if any(etree.QName(parent).localname in {"head", "style", "script"} for parent in node.iterancestors() if isinstance(parent.tag, str)):
                continue
            for value in [node.text or "", node.tail or ""]:
                for word in regex.findall(r"[\p{Latin}\p{Hangul}]+(?:['’][\p{Latin}]+)?", value):
                    words[word.casefold()] += 1
                    if (dictionary is not None or hunspell is not None) and len(word) <= 80 and word.casefold() not in allowed_words:
                        folded = word.casefold()
                        if hunspell is not None:
                            if word not in correct:
                                correct[word] = hunspell.lookup(word)
                            if correct[word]:
                                continue
                        if folded not in suggestions:
                            suggestions[folded] = list(islice(hunspell.suggest(word), 3)) if hunspell is not None else difflib.get_close_matches(folded, dictionary, n=3, cutoff=0.75)
                        rows.append({"path": path, "line": node.sourceline or 1, "kind": "spelling", "message": word, "suggestions": suggestions[folded]})
                for kind, pattern in [("repeated_word", r"\b(\w+)\s+\1\b"), ("spacing", r"[ \t]{2,}"), ("punctuation", r"[!！?？]{3,}")]:
                    for match in regex.finditer(pattern, value, regex.I):
                        rows.append({"path": path, "line": node.sourceline or 1, "kind": kind, "message": match.group()[:80]})
                if len(rows) >= 5000:
                    return {"rows": rows[:5000], "truncated": True, "dictionary_loaded": dictionary is not None or hunspell is not None}
    return {"rows": rows, "words": [{"word": word, "count": count} for word, count in words.most_common(5000)], "dictionary_loaded": dictionary is not None or hunspell is not None}


def cleanup_css(session: ContentSession, apply: bool = False) -> dict[str, object]:
    elements = []
    for file in session.files:
        if file["media_type"] not in {"application/xhtml+xml", "text/html"}:
            continue
        data = session._bytes(str(file["path"]))
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError("CSS cleanup requires every document to fit the inspection limit.")
        root = _xml(data)
        elements.extend(cssselect2.ElementWrapper.from_xml_root(root).iter_subtree())
    pending, rows = {}, []
    for item in session.files:
        if item["media_type"] != "text/css":
            continue
        path = str(item["path"])
        text, encoding = _decode(session._bytes(path))
        rules = tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=False)
        retained, removed = [], []
        for rule in rules:
            if rule.type == "error":
                raise ValueError(f"Malformed CSS: {path}")
            unused = False
            if rule.type == "qualified-rule":
                selector = tinycss2.serialize(rule.prelude).strip()
                # Dynamic state and namespaced selectors cannot be proved unused statically.
                if not any(mark in selector for mark in (":", "|", "\\")):
                    try:
                        compiled = cssselect2.compile_selector_list(selector)
                        unused = not any(test.test(element) for test in compiled for element in elements)
                    except cssselect2.SelectorError:
                        pass
                if unused:
                    removed.append(selector)
            if not unused:
                retained.append(rule)
        if removed:
            pending[path] = _encode(tinycss2.serialize(retained), encoding)
            rows.append({"path": path, "selectors": removed, "removed": len(removed)})
    if apply:
        with session.transaction():
            for path, data in pending.items():
                session.replace_resource(path, data)
    return {"rows": rows, "count": sum(int(row["removed"]) for row in rows), "applied": apply}


def image_report(session: ContentSession, apply: bool = False, quality: int = 85, max_dimension: int = 0) -> dict[str, object]:
    if not 10 <= quality <= 100 or not 0 <= max_dimension <= 10000:
        raise ValueError("Invalid image compression settings.")
    pending, rows = {}, []
    for item in session.files:
        path = str(item["path"])
        if item["media_type"] not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        data = session._bytes(path)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError(f"Image exceeds the editor limit: {path}")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 40_000_000 or getattr(image, "n_frames", 1) > 1:
                    rows.append({"path": path, "skipped": True})
                    continue
                old_size = image.size
                image.load()
                if max_dimension and max(image.size) > max_dimension:
                    image.thumbnail((max_dimension, max_dimension))
                buffer = io.BytesIO()
                options = {"optimize": True}
                if image.format in {"JPEG", "WEBP"}:
                    options["quality"] = quality
                if image.info.get("icc_profile"):
                    options["icc_profile"] = image.info["icc_profile"]
                if image.info.get("exif"):
                    options["exif"] = image.info["exif"]
                image.save(buffer, format=image.format, **options)
                optimized = buffer.getvalue()
                if len(optimized) < len(data):
                    pending[path] = optimized
                rows.append({"path": path, "width": old_size[0], "height": old_size[1], "before_size": len(data), "after_size": min(len(data), len(optimized))})
        except (OSError, Image.DecompressionBombError) as exc:
            rows.append({"path": path, "error": str(exc)})
    if apply:
        with session.transaction():
            for path, data in pending.items():
                session.replace_resource(path, data)
    return {"rows": rows, "count": len(pending), "applied": apply}


def font_report(session: ContentSession, apply: bool = False) -> dict[str, object]:
    characters: set[int] = set()
    inspected = 0
    def collect(value: str):
        characters.update(map(ord, value))
    def collect_css(tokens):
        for token in tokens:
            if token.type == "error":
                raise ValueError("Repair malformed CSS before subsetting fonts.")
            value = getattr(token, "value", None)
            if isinstance(value, str):
                collect(value)
            for field in ("prelude", "content", "arguments"):
                children = getattr(token, field, None)
                if children is not None:
                    collect_css(children)
    for item in session.files:
        if not item["editable"]:
            continue
        data = session._bytes(str(item["path"]))
        inspected += len(data)
        if len(data) > MAX_TEXT_BYTES or inspected > MAX_PREVIEW_BYTES * 4:
            raise ValueError("Font subsetting exceeds the text inspection limit.")
        text = _decode(data)[0]
        collect(text)
        media = str(item["media_type"])
        if media == "text/css":
            collect_css(tinycss2.parse_stylesheet(text))
        elif media in XML_TYPES | {"application/xhtml+xml", "text/html"}:
            root = _xml(data)
            for value in root.itertext():
                collect(value)
            for node in root.iter():
                if not isinstance(node.tag, str):
                    continue
                for value in node.attrib.values():
                    collect(value)
                if node.get("style"):
                    collect_css(tinycss2.parse_component_value_list(node.get("style")))
                if etree.QName(node).localname == "style":
                    collect_css(tinycss2.parse_stylesheet("".join(node.itertext())))
    pending, rows = {}, []
    with zipfile.ZipFile(session.path) as archive:
        encrypted = archive.read("META-INF/encryption.xml") if "META-INF/encryption.xml" in archive.namelist() else b""
    for item in session.files:
        path = str(item["path"])
        if not path.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
            continue
        data = session._bytes(path)
        if encrypted and (path.encode() in encrypted or quote(path).encode() in encrypted):
            rows.append({"path": path, "skipped": True, "reason": "obfuscated"})
            continue
        try:
            with TTFont(io.BytesIO(data)) as font:
                if "OS/2" in font and font["OS/2"].fsType & 0x0102:
                    rows.append({"path": path, "skipped": True, "reason": "restricted_embedding_or_subsetting"})
                    continue
                names = font["name"]
                family = names.getDebugName(1) or path
                options = subset.Options()
                options.layout_features = ["*"]
                options.name_IDs = ["*"]
                subsetter = subset.Subsetter(options=options)
                subsetter.populate(unicodes=characters)
                subsetter.subset(font)
                output = io.BytesIO()
                font.save(output)
                optimized = output.getvalue()
                if len(optimized) < len(data):
                    pending[path] = optimized
                rows.append({"path": path, "family": family, "before_size": len(data), "after_size": min(len(data), len(optimized))})
        except (TTLibError, KeyError, ValueError) as exc:
            rows.append({"path": path, "error": str(exc)})
    if apply:
        with session.transaction():
            for path, data in pending.items():
                session.replace_resource(path, data)
    return {"rows": rows, "count": len(pending), "applied": apply}


def set_cover(session: ContentSession, path: str) -> None:
    item = session._file(path)
    if not str(item["media_type"]).startswith("image/"):
        raise ValueError("Choose an image resource as cover.")
    package = session._package()
    metadata = package.find(f"{{{OPF}}}metadata")
    if metadata is None:
        raise ValueError("Metadata is missing.")
    cover_id = ""
    for entry in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item"):
        properties = entry.get("properties", "").split()
        properties = [prop for prop in properties if prop != "cover-image"]
        if resolve_epub_href(posixpath.dirname(session.opf_path), entry.get("href", "")) == path:
            cover_id = entry.get("id", "")
            if package.get("version", "").startswith("3"):
                properties.append("cover-image")
        if properties:
            entry.set("properties", " ".join(properties))
        else:
            entry.attrib.pop("properties", None)
    for entry in list(metadata):
        if entry.get("name") == "cover":
            metadata.remove(entry)
    etree.SubElement(metadata, f"{{{OPF}}}meta", name="cover", content=cover_id)
    session._record()
    session.changes[session.opf_path] = _serialize(package)


def embed_font(session: ContentSession, path: str, family: str, style_path: str) -> None:
    if not family.strip() or not path.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
        raise ValueError("Choose a font and enter its family name.")
    session._file(path)
    if session._file(style_path)["media_type"] != "text/css":
        raise ValueError("Choose a stylesheet for the font-face rule.")
    try:
        with TTFont(io.BytesIO(session._bytes(path))) as font:
            if "OS/2" in font and font["OS/2"].fsType & 0x0002:
                raise ValueError("This font prohibits embedding.")
    except TTLibError as exc:
        raise ValueError("The selected resource is not a valid font.") from exc
    href = quote(posixpath.relpath(path, posixpath.dirname(style_path) or "."), safe="/-._~")
    content = session.read(style_path)["content"]
    rule = f'\n@font-face {{font-family:{json.dumps(family)};src:url("{href}");}}\n'
    session.write(style_path, content + rule)


def upgrade_epub(session: ContentSession) -> None:
    package = session._package()
    if package.get("version", "").startswith("3"):
        raise ValueError("This book already uses EPUB 3.")
    metadata = package.find(f"{{{OPF}}}metadata")
    if metadata is None or metadata.find("{http://purl.org/dc/elements/1.1/}identifier") is None:
        raise ValueError("EPUB 3 conversion requires identifier metadata.")
    with session.transaction():
        if not session.nav_path:
            nav_path = posixpath.join(posixpath.dirname(session.opf_path), "navigation3.xhtml")
            root = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML, "epub": "http://www.idpf.org/2007/ops"})
            head = etree.SubElement(root, f"{{{XHTML}}}head")
            etree.SubElement(head, f"{{{XHTML}}}title").text = "Contents"
            body = etree.SubElement(root, f"{{{XHTML}}}body")
            nav = etree.SubElement(body, f"{{{XHTML}}}nav", {"{http://www.idpf.org/2007/ops}type": "toc"})
            etree.SubElement(nav, f"{{{XHTML}}}ol")
            session.add_resource(nav_path, _write_nav(_serialize(root), nav_path, session.toc), "application/xhtml+xml")
            session.nav_path = nav_path
        package = session._package()
        package.set("version", "3.0")
        metadata = package.find(f"{{{OPF}}}metadata")
        assert metadata is not None
        identifiers = {node.get("id") for node in package.iter() if node.get("id")}
        for number, node in enumerate(list(metadata)):
            for key, value in list(node.attrib.items()):
                if not key.startswith(f"{{{OPF}}}"):
                    continue
                name = etree.QName(key).localname
                identifier = node.get("id")
                if not identifier:
                    identifier = f"upgraded-metadata-{number + 1}"
                    while identifier in identifiers:
                        identifier += "-"
                    node.set("id", identifier)
                    identifiers.add(identifier)
                if name in {"file-as", "role", "scheme"}:
                    property_name = "identifier-type" if name == "scheme" else name
                    refinement = etree.SubElement(metadata, f"{{{OPF}}}meta", property=property_name, refines=f"#{identifier}")
                    refinement.text = value
                else:
                    etree.SubElement(metadata, f"{{{OPF}}}meta", name=f"legacy-{identifier}-{name}", content=value)
                del node.attrib[key]
        cover = metadata.find(f"{{{OPF}}}meta[@name='cover']")
        for item in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item"):
            path = resolve_epub_href(posixpath.dirname(session.opf_path), item.get("href", ""))
            properties = set(item.get("properties", "").split())
            if path == session.nav_path:
                properties.add("nav")
            if cover is not None and item.get("id") == cover.get("content"):
                properties.add("cover-image")
            if item.get("media-type") == "application/xhtml+xml":
                document = _xml(session._bytes(path))
                namespaces = {etree.QName(node).namespace for node in document.iter() if isinstance(node.tag, str)}
                if "http://www.w3.org/2000/svg" in namespaces:
                    properties.add("svg")
                if "http://www.w3.org/1998/Math/MathML" in namespaces:
                    properties.add("mathml")
                if document.findall(f".//{{{XHTML}}}script") or any(key.lower().startswith("on") for node in document.iter() for key in node.attrib):
                    properties.add("scripted")
            if properties:
                item.set("properties", " ".join(sorted(properties)))
        modified = metadata.find(f"{{{OPF}}}meta[@property='dcterms:modified']")
        if modified is None:
            modified = etree.SubElement(metadata, f"{{{OPF}}}meta", property="dcterms:modified")
        from datetime import datetime, timezone
        modified.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        session.changes[session.opf_path] = _serialize(package)


def external_check(session: ContentSession, jar_path: str) -> dict[str, object]:
    jar = Path(jar_path).expanduser().resolve()
    java = shutil.which("java")
    if not java or not jar.is_file() or jar.suffix.lower() != ".jar":
        raise ValueError("Select an installed EPUBCheck JAR and install Java to run this check.")
    with tempfile.TemporaryDirectory(prefix="epub-check-") as folder:
        path = Path(folder) / "draft.epub"
        with zipfile.ZipFile(path, "w") as archive:
            entries = staged_entries(session)
            archive.writestr("mimetype", entries.pop("mimetype", b"application/epub+zip"), compress_type=zipfile.ZIP_STORED)
            for name, data in entries.items():
                archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        try:
            result = subprocess.run([java, "-jar", str(jar), str(path)], capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("EPUBCheck exceeded the two-minute limit.") from exc
    return {"exit_code": result.returncode, "output": (result.stdout + result.stderr)[-200_000:]}


def import_book(input_path: str, executable: str, cache_root: Path) -> Path:
    source = Path(input_path).expanduser().resolve()
    if source.suffix.lower() not in {".azw3", ".mobi", ".docx", ".odt", ".rtf", ".html", ".htm", ".txt", ".fb2"} or not source.is_file():
        raise ValueError("Select a supported unencrypted book or document.")
    if source.stat().st_size > MAX_PREVIEW_BYTES:
        raise ValueError("Imported document exceeds the 48 MB limit.")
    converter = Path(executable).expanduser().resolve() if executable else Path(shutil.which("ebook-convert") or "")
    if not converter.is_file():
        raise ValueError("Select an installed ebook-convert executable.")
    cache_root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="imported-book-", dir=cache_root))
    output = folder / (source.stem + ".epub")
    try:
        result = subprocess.run([str(converter), str(source), str(output), "--epub-version", "3"], capture_output=True, text=True, timeout=300, check=False)
        if result.returncode != 0 or not output.is_file():
            raise ValueError("Book import failed: " + (result.stderr or result.stdout)[-4000:])
        ContentSession.open(str(output))
        return output
    except Exception as exc:
        shutil.rmtree(folder)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ValueError("Book import exceeded the five-minute limit.") from exc
        raise


def replace_sequence(session: ContentSession, rules: object, apply: bool) -> dict[str, object]:
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise ValueError("Select one to one hundred saved searches.")
    shadow = copy.deepcopy(session)
    total = 0
    deadline = time.monotonic() + 10
    for rule in rules:
        if time.monotonic() > deadline:
            raise ValueError("Search sequence timed out; select fewer rules.")
        if not isinstance(rule, dict) or not isinstance(rule.get("query"), str) or not isinstance(rule.get("replacement"), str):
            raise ValueError("Search rules require query and replacement strings.")
        paths = rule.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
            raise ValueError("Choose editable files for each search rule.")
        result = shadow.replace(rule["query"], rule["replacement"], paths,
                                rule.get("case_sensitive") is True,
                                regular_expression=rule.get("regular_expression") is True,
                                ignore_markup=rule.get("ignore_markup") is True)
        total += int(result["replacements"])
    pending = {path: data for path, data in shadow.changes.items() if data != session._bytes(path)}
    if apply and pending:
        session._apply_changes(pending)
    return {"replacements": total, "files_changed": len(pending), "applied": apply}


def run_tool(session: ContentSession, name: str, options: dict[str, object]) -> dict[str, object]:
    apply = options.get("apply") is True
    fingerprint = hashlib.sha256(json.dumps({
        "state": {path: hashlib.sha256(data).hexdigest() for path, data in session.changes.items()},
        "spine": session.spine, "linear": session.spine_linear, "toc": session.toc,
        "files": session.files, "nav": session.nav_path, "ncx": session.ncx_path,
        "removed": sorted(session.removed),
        **({"rules": options.get("rules")} if name == "replace_sequence" else {}),
    }, sort_keys=True).encode()).hexdigest()
    if name in {"cleanup_css", "images", "fonts", "replace_sequence"} and apply and options.get("fingerprint") != fingerprint:
        raise ValueError("The draft changed; preview this operation again before applying it.")
    if name == "issues":
        return issues(session)
    if name == "replace_sequence":
        return {**replace_sequence(session, options.get("rules"), apply), "fingerprint": fingerprint}
    if name == "diff":
        return compare(session, str(options.get("checkpoint", "")))
    if name == "text_report":
        ignored = options.get("ignored", [])
        if not isinstance(ignored, list) or not all(isinstance(word, str) for word in ignored):
            raise ValueError("ignored must be a list of words.")
        return text_report(session, str(options.get("dictionary_path", "")), ignored)
    if name == "cleanup_css":
        return {**cleanup_css(session, apply), "fingerprint": fingerprint}
    if name == "images":
        quality, dimension = options.get("quality", 85), options.get("max_dimension", 0)
        if type(quality) is not int or type(dimension) is not int:
            raise ValueError("Image settings must be integers.")
        return {**image_report(session, apply, quality, dimension), "fingerprint": fingerprint}
    if name == "fonts":
        return {**font_report(session, apply), "fingerprint": fingerprint}
    if name == "set_cover":
        set_cover(session, str(options.get("path", "")))
        return {"applied": True}
    if name == "embed_font":
        embed_font(session, str(options.get("path", "")), str(options.get("family", "")), str(options.get("style_path", "")))
        return {"applied": True}
    if name == "upgrade":
        upgrade_epub(session)
        return {"applied": True}
    if name == "epubcheck":
        return external_check(session, str(options.get("jar_path", "")))
    raise ValueError("Unknown editor tool.")
