from __future__ import annotations

import difflib
import math
import posixpath
import re
import time

import cssselect2
import regex
import tinycss2
from lxml import etree
from lxml import html as lxml_html

from transoria.formats.epub_paths import resolve_epub_href
from transoria.tools.epub_content import MAX_TEXT_BYTES, XHTML, ContentSession, _decode, _encode, _xml

NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
UNSAFE_TAGS = {"html", "head", "body", "script", "iframe", "object", "embed", "link", "meta", "style", "base"}


def _rules(value: object, kind: str) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError("Provide one to 100 transformation rules.")
    rules = []
    for source in value:
        if not isinstance(source, dict) or any(not isinstance(item, str) or len(item) > 2000 for item in source.values()):
            raise ValueError("Transformation fields must be text under 2000 characters.")
        rule = dict(source)
        if kind == "html":
            try:
                rule["compiled"] = cssselect2.compile_selector_list(rule.get("selector", ""), namespaces={"h": XHTML, "xhtml": XHTML})
            except cssselect2.SelectorError as exc:
                raise ValueError(f"Invalid HTML selector: {exc}") from exc
            action = rule.get("action")
            if action not in {"rename", "wrap", "unwrap", "remove", "set_attr", "remove_attr", "add_class", "remove_class"}:
                raise ValueError("Invalid HTML transformation action.")
            if action in {"rename", "wrap"} and (not NAME.fullmatch(rule.get("target", "")) or rule["target"].lower() in UNSAFE_TAGS):
                raise ValueError("Choose a safe body tag name.")
            if action in {"rename", "wrap", "set_attr", "remove_attr"}:
                rule["target"] = rule.get("target", "").lower()
            if action in {"set_attr", "remove_attr"}:
                attribute = rule.get("target", "").lower()
                if not NAME.fullmatch(attribute) or attribute.startswith("on") or attribute in {"id", "xmlns", "srcdoc"}:
                    raise ValueError("Event handlers, IDs and namespace changes are not supported.")
            if action in {"add_class", "remove_class"} and not NAME.fullmatch(rule.get("value", "")):
                raise ValueError("Provide one class name.")
        else:
            if not NAME.fullmatch(rule.get("property", "")) or (rule.get("target") and not NAME.fullmatch(rule["target"])):
                raise ValueError("Provide valid CSS property names.")
            if rule.get("operator", "any") not in {"any", "equals", "contains", "regex"}:
                raise ValueError("Invalid CSS condition.")
            if rule.get("operator") == "regex":
                try:
                    rule["compiled"] = regex.compile(rule.get("match", ""), regex.IGNORECASE)
                except regex.error as exc:
                    raise ValueError(f"Invalid CSS value expression: {exc}") from exc
            if rule.get("action") not in {"set", "remove", "rename", "multiply", "add"}:
                raise ValueError("Invalid CSS transformation action.")
            if rule["action"] in {"multiply", "add"}:
                try:
                    amount = float(rule.get("value", ""))
                except ValueError as exc:
                    raise ValueError("CSS arithmetic requires a finite number.") from exc
                if not math.isfinite(amount) or abs(amount) > 10000:
                    raise ValueError("CSS arithmetic amount is too large.")
            if rule["action"] == "rename" and not rule.get("target"):
                raise ValueError("Choose the new CSS property name.")
            if rule["action"] == "set":
                parsed = tinycss2.parse_declaration_list(f"{rule.get('target') or rule['property']}:{rule.get('value', '')};", skip_whitespace=True, skip_comments=True)
                if len(parsed) != 1 or parsed[0].type != "declaration" or not parsed[0].value or any(token.type == "error" for token in parsed[0].value):
                    raise ValueError("Provide one valid CSS property value.")
        rules.append(rule)
    return rules


def _condition(declaration, rule) -> bool:
    if declaration.type != "declaration" or declaration.lower_name != rule["property"].lower():
        return False
    value = tinycss2.serialize(declaration.value).strip()
    expected = rule.get("match", "")
    operator = rule.get("operator", "any")
    if operator == "any":
        return True
    if operator == "equals":
        return value.casefold() == expected.casefold()
    if operator == "contains":
        return expected.casefold() in value.casefold()
    try:
        return rule["compiled"].search(value, timeout=.1) is not None
    except TimeoutError as exc:
        raise ValueError("CSS value matching timed out.") from exc


def _declarations(tokens, rules) -> tuple[list, int]:
    declarations = tinycss2.parse_declaration_list(tokens, skip_whitespace=False, skip_comments=False)
    if any(item.type == "error" for item in declarations):
        raise ValueError("Invalid or nested declarations cannot be transformed safely; edit the source first.")
    count = 0
    for rule in rules:
        if not any(_condition(item, rule) for item in declarations):
            continue
        target = (rule.get("target") or rule["property"]).lower()
        action = rule["action"]
        if action == "set":
            existing = [item for item in declarations if item.type == "declaration" and item.lower_name == target]
            important = bool(existing and existing[-1].important)
            declarations = [item for item in declarations if item not in existing]
            added = tinycss2.parse_declaration_list(f"{target}:{rule['value']}{'!important' if important else ''};")[0]
            declarations.append(added)
            count += 1
            continue
        for item in list(declarations):
            if item.type != "declaration" or item.lower_name != (rule["property"].lower() if action == "rename" else target):
                continue
            if action == "remove":
                declarations.remove(item)
            elif action == "rename":
                item.name = target
                item.lower_name = target
            else:
                values = [token for token in item.value if token.type not in {"whitespace", "comment"}]
                if len(values) != 1 or values[0].type not in {"number", "percentage", "dimension"}:
                    raise ValueError("CSS arithmetic requires a single number with an optional unit.")
                token = values[0]
                value = token.value * float(rule["value"]) if action == "multiply" else token.value + float(rule["value"])
                if not math.isfinite(value) or abs(value) > 1e6:
                    raise ValueError("CSS arithmetic result is too large.")
                unit = token.unit if token.type == "dimension" else "%" if token.type == "percentage" else ""
                item.value = tinycss2.parse_component_value_list(f"{value:g}{unit}")
            count += 1
    return tinycss2.parse_component_value_list(tinycss2.serialize(declarations)), count


def _stylesheet(tokens, rules) -> tuple[list, int]:
    count = 0
    for item in tokens:
        if item.type == "error":
            raise ValueError("Invalid stylesheet cannot be transformed safely; edit the source first.")
        if item.type == "qualified-rule" or (item.type == "at-rule" and item.lower_at_keyword in {"font-face", "page", "counter-style", "viewport"}):
            if item.content is not None:
                item.content, changes = _declarations(item.content, rules)
                count += changes
        elif item.type == "at-rule" and item.content is not None and item.lower_at_keyword in {"media", "supports", "layer", "container", "keyframes", "-webkit-keyframes", "scope"}:
            nested, changes = _stylesheet(tinycss2.parse_rule_list(item.content), rules)
            item.content = tinycss2.parse_component_value_list(tinycss2.serialize(nested))
            count += changes
    return tokens, count


def _unwrap(node) -> None:
    parent = node.getparent()
    index = parent.index(node)
    previous = node.getprevious()
    if node.text:
        if previous is None:
            parent.text = (parent.text or "") + node.text
        else:
            previous.tail = (previous.tail or "") + node.text
    children = list(node)
    for child in children:
        parent.insert(index, child)
        index += 1
    tail = node.tail or ""
    if children:
        children[-1].tail = (children[-1].tail or "") + tail
    elif previous is None:
        parent.text = (parent.text or "") + tail
    else:
        previous.tail = (previous.tail or "") + tail
    parent.remove(node)


def _html(root, rules, session, path) -> int:
    count = 0
    for rule in rules:
        wrapper = cssselect2.ElementWrapper.from_xml_root(root)
        for element in list(wrapper.iter_subtree()):
            node = element.etree_element
            if not any(selector.pseudo_element is None and selector.test(element) for selector in rule["compiled"]):
                continue
            # Earlier ancestor removals can detach a previously matched descendant.
            ancestors = list(node.iterancestors())
            if node is not root and root not in ancestors:
                continue
            if not any(isinstance(parent.tag, str) and etree.QName(parent).localname.lower() == "body" for parent in ancestors):
                raise ValueError("HTML transformations must target ordinary chapter body elements.")
            if node.getparent() is None or etree.QName(node).localname.lower() in UNSAFE_TAGS or etree.QName(node).namespace not in {None, XHTML}:
                raise ValueError("HTML transformations must target ordinary chapter body elements.")
            action, target, value = rule["action"], rule.get("target", ""), rule.get("value", "")
            if action == "rename":
                node.tag = f"{{{XHTML}}}{target}" if etree.QName(node).namespace else target
            elif action == "wrap":
                parent = node.getparent()
                wrapper_node = etree.Element(f"{{{XHTML}}}{target}" if etree.QName(node).namespace else target)
                wrapper_node.tail, node.tail = node.tail, None
                parent.replace(node, wrapper_node)
                wrapper_node.append(node)
            elif action in {"remove", "unwrap"}:
                if any(child.get("id") for child in (node.iter() if action == "remove" else [node])):
                    raise ValueError("Removing anchored elements would break links; retain the anchor or edit references first.")
                if action == "unwrap":
                    _unwrap(node)
                else:
                    for child in list(node):
                        node.remove(child)
                    node.text = None
                    _unwrap(node)
            elif action == "remove_attr":
                node.attrib.pop(target, None)
            elif action == "set_attr":
                if target in {"src", "href", "poster"}:
                    resolved = resolve_epub_href(posixpath.dirname(path), value)
                    if resolved not in {file["path"] for file in session.files}:
                        raise ValueError("Resource attributes must point to an existing local EPUB resource.")
                node.set(target, value)
            else:
                classes = node.get("class", "").split()
                classes = list(dict.fromkeys([*classes, value])) if action == "add_class" else [item for item in classes if item != value]
                if classes:
                    node.set("class", " ".join(classes))
                else:
                    node.attrib.pop("class", None)
            count += 1
    return count


def transform(session: ContentSession, kind: str, raw_rules: object, paths: object, apply: bool) -> dict[str, object]:
    rules = _rules(raw_rules, kind)
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise ValueError("Choose text or stylesheet files to transform.")
    pending, rows = {}, []
    deadline = time.monotonic() + 10
    matched = 0
    for path in dict.fromkeys(paths):
        if time.monotonic() > deadline:
            raise ValueError("Transformation timed out; narrow the scope.")
        item = session._file(path, editable=True)
        media = item["media_type"]
        if media not in {"text/css", "application/xhtml+xml", "text/html"} or (kind == "html" and media == "text/css"):
            continue
        data = session._bytes(path)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(f"Resource exceeds the editor limit: {path}")
        text, encoding = _decode(data)
        if media == "text/css":
            tokens, changes = _stylesheet(tinycss2.parse_stylesheet(text), rules)
            updated = tinycss2.serialize(tokens)
        else:
            try:
                root = _xml(data) if media == "application/xhtml+xml" else lxml_html.fromstring(data, parser=lxml_html.HTMLParser(no_network=True))
            except (etree.XMLSyntaxError, etree.ParserError) as exc:
                raise ValueError(f"Repair this chapter before structural transformations: {path}") from exc
            changes = 0
            if kind == "html":
                changes = _html(root, rules, session, path)
            else:
                for node in root.iter():
                    if not isinstance(node.tag, str):
                        continue
                    if etree.QName(node).localname == "style":
                        tokens, count = _stylesheet(tinycss2.parse_stylesheet(node.text or ""), rules)
                        if count:
                            node.text = tinycss2.serialize(tokens)
                        changes += count
                    if node.get("style"):
                        tokens, count = _declarations(node.get("style"), rules)
                        if count:
                            node.set("style", tinycss2.serialize(tokens))
                        changes += count
            if media == "application/xhtml+xml":
                updated = etree.tostring(root.getroottree(), encoding="utf-8" if encoding == "utf-8-sig" else encoding, xml_declaration=True).decode("utf-8" if encoding == "utf-8-sig" else encoding)
            else:
                updated = etree.tostring(root, encoding="unicode", method="html")
        if changes:
            encoded = _encode(updated, encoding)
            if len(encoded) > MAX_TEXT_BYTES:
                raise ValueError(f"Transformation exceeds the editor limit: {path}")
            if encoded != data:
                pending[path] = encoded
                matched += changes
                rows.append({"path": path, "message": f"{changes} rule applications", "diff": "".join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True), fromfile=path, tofile=path))[:100000]})
    if apply and pending:
        session._apply_changes(pending)
    return {"count": len(pending), "matched": matched, "rows": rows, "applied": apply}
