from __future__ import annotations

import difflib
import re
from collections.abc import Callable

from lxml import etree
from lxml import html as lxml_html

from transoria.tools.epub_content import MAX_TEXT_BYTES, XHTML, ContentSession, _decode, _encode, _xml
from transoria.tools.epub_transform import _unwrap

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
DOCUMENT_TYPES = {"application/xhtml+xml", "text/html"}
NCX_TYPE = "application/x-dtbncx+xml"
SCRIPT_TARGETS = {"s2t": "zh-Hant", "s2tw": "zh-TW", "s2twp": "zh-TW", "s2hk": "zh-HK", "t2s": "zh-Hans"}
CLASS_SELECTOR = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

Mutation = Callable[[etree._Element], int]


def _local(node: etree._Element) -> str:
    return etree.QName(node).localname.lower() if isinstance(node.tag, str) else ""


def _in_body(node: etree._Element) -> bool:
    return any(_local(parent) == "body" for parent in node.iterancestors())


def _language(node: etree._Element | None) -> str:
    while node is not None:
        value = node.get(XML_LANG) or node.get("lang") if isinstance(node.tag, str) else None
        if value:
            return value.lower()
        node = node.getparent()
    return ""


def _rewrite(session: ContentSession, mutate: Mutation, apply: bool, media_types: set[str]) -> dict[str, object]:
    pending, rows, total = {}, [], 0
    for item in session.files:
        path, media = str(item["path"]), item["media_type"]
        if media not in media_types or not item["editable"] or path in session.removed:
            continue
        data = session._bytes(path)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(f"Resource exceeds the editor limit: {path}")
        text, encoding = _decode(data)
        try:
            root = lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True)) if media == "text/html" else _xml(data)
        except (etree.XMLSyntaxError, etree.ParserError) as exc:
            raise ValueError(f"Repair this file before running the tool: {path}") from exc
        changes = mutate(root)
        if not changes:
            continue
        if media == "text/html":
            updated = etree.tostring(root, encoding="unicode", method="html")
        else:
            declared = "utf-8" if encoding == "utf-8-sig" else encoding
            updated = etree.tostring(root.getroottree(), encoding=declared, xml_declaration=True).decode(declared)
        encoded = _encode(updated, encoding)
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError(f"Result exceeds the editor limit: {path}")
        if encoded == data:
            continue
        pending[path] = encoded
        total += changes
        rows.append({"path": path, "diff": "".join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True), fromfile=path, tofile=path))[:100000]})
    if apply and pending:
        session._apply_changes(pending)
    return {"rows": rows, "count": total, "applied": apply}


def convert_script(session: ContentSession, conversion: str, apply: bool) -> dict[str, object]:
    if conversion not in SCRIPT_TARGETS:
        raise ValueError("Choose a Chinese script conversion.")
    from opencc import OpenCC

    convert, target = OpenCC(conversion).convert, SCRIPT_TARGETS[conversion]

    def foreign(node: etree._Element | None) -> bool:
        # Japanese and Korean originals in bilingual books keep their own kanji / hanja.
        return _language(node).startswith(("ja", "ko"))

    def mutate(root: etree._Element) -> int:
        count = 0
        for node in list(root.iter()):
            if isinstance(node.tag, str) and _local(node) not in {"style", "script"} and not foreign(node):
                if node.text and (value := convert(node.text)) != node.text:
                    node.text, count = value, count + 1
                for attribute in ("alt", "title"):
                    if (current := node.get(attribute)) and (value := convert(current)) != current:
                        node.set(attribute, value)
                        count += 1
            if isinstance(node.tag, str):
                for attribute in (XML_LANG, "lang"):
                    if (current := node.get(attribute)) and current.lower().startswith("zh") and current != target:
                        node.set(attribute, target)
                        count += 1
            if node.tail and not foreign(node.getparent()) and (value := convert(node.tail)) != node.tail:
                node.tail, count = value, count + 1
        return count

    return _rewrite(session, mutate, apply, DOCUMENT_TYPES | {NCX_TYPE})


def _referenced_classes(session: ContentSession) -> set[str]:
    sources = []
    for item in session.files:
        path, media = str(item["path"]), item["media_type"]
        if path in session.removed or media not in DOCUMENT_TYPES | {"text/css"}:
            continue
        data = session._bytes(path)
        if media == "text/css":
            sources.append(_decode(data)[0])
            continue
        try:
            root = lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True)) if media == "text/html" else _xml(data)
        except (etree.XMLSyntaxError, etree.ParserError) as exc:
            raise ValueError(f"Repair this file before running the tool: {path}") from exc
        sources.extend(node.text or "" for node in root.iter() if _local(node) == "style")
    return {name for source in sources for name in CLASS_SELECTOR.findall(CSS_COMMENT.sub("", source))}


def clean_spans(session: ContentSession, apply: bool) -> dict[str, object]:
    referenced = _referenced_classes(session)

    def mutate(root: etree._Element) -> int:
        count = 0
        for node in list(root.iter()):
            if _local(node) != "span" or etree.QName(node).namespace not in {None, XHTML} or not _in_body(node):
                continue
            if set(node.attrib) - {"class"} or set(node.get("class", "").split()) & referenced:
                continue
            _unwrap(node)
            count += 1
        return count

    return _rewrite(session, mutate, apply, DOCUMENT_TYPES)


def _drop(node: etree._Element) -> None:
    parent, previous, tail = node.getparent(), node.getprevious(), node.tail or ""
    if previous is not None:
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(node)


def remove_bilingual(session: ContentSession, apply: bool) -> dict[str, object]:
    def mutate(root: etree._Element) -> int:
        clones = [node for node in root.iter() if isinstance(node.tag, str) and _in_body(node)
                  and re.sub(r"\s+", "", node.get("style", "")).endswith("opacity:0.50;")]
        for node in clones:
            _drop(node)
        return len(clones)

    return _rewrite(session, mutate, apply, DOCUMENT_TYPES)
