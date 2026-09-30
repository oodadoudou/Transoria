"""Read rendering hints without changing package or chapter resources."""
from __future__ import annotations

import math
import posixpath
import re

from lxml import etree

from transoria.formats.epub_paths import resolve_epub_href

OPF = "http://www.idpf.org/2007/opf"


def rendition(package: etree._Element, document: etree._Element, package_path: str, path: str) -> dict:
    metadata = package.find(f"{{{OPF}}}metadata")
    values = {}
    if metadata is not None:
        for node in metadata:
            if not isinstance(node.tag, str) or etree.QName(node).localname != "meta" or node.get("refines"):
                continue
            values[node.get("property") or node.get("name", "")] = (node.get("content") or node.text or "").strip()
    layout = values.get("rendition:layout", "reflowable")
    if values.get("fixed-layout") == "true":
        layout = "pre-paginated"
    spread = values.get("rendition:spread", "auto")
    position = ""
    manifest = {item.get("id"): resolve_epub_href(posixpath.dirname(package_path), item.get("href", "")) for item in package.findall(f"{{{OPF}}}manifest/{{{OPF}}}item")}
    spine = package.find(f"{{{OPF}}}spine")
    direction = "rtl" if spine is not None and spine.get("page-progression-direction") == "rtl" else "ltr"
    if spine is not None:
        for item in spine:
            if manifest.get(item.get("idref")) != path:
                continue
            for prop in item.get("properties", "").split():
                if prop.startswith("rendition:layout-"):
                    layout = prop.removeprefix("rendition:layout-")
                if prop.startswith("rendition:spread-"):
                    spread = prop.removeprefix("rendition:spread-")
                if prop in {"page-spread-left", "page-spread-right", "rendition:page-spread-left", "rendition:page-spread-right", "rendition:page-spread-center"}:
                    position = prop.rsplit("-", 1)[1]
                if prop == "spread-none":
                    position = "center"
            break
    dimensions = {}
    viewport = next((node for node in document.iter() if isinstance(node.tag, str) and etree.QName(node).localname.lower() == "meta" and node.get("name", "").strip().lower() == "viewport"), None)
    if viewport is not None:
        for key, value in re.findall(r"(width|height)\s*=\s*([^,;\s]+)", viewport.get("content", ""), re.IGNORECASE):
            key = key.lower()
            if key in dimensions:
                continue
            if value.lower() == f"device-{key}":
                dimensions[key] = value.lower()
            else:
                try:
                    number = float(value)
                    if math.isfinite(number) and 0 < number <= 100000:
                        dimensions[key] = number
                except ValueError:
                    pass
    if layout == "pre-paginated" and not dimensions:
        svg = next((node for node in document.iter() if isinstance(node.tag, str) and etree.QName(node).localname == "svg" and node.get("viewBox")), None)
        if svg is not None:
            try:
                box = [float(value) for value in re.split(r"[,\s]+", svg.get("viewBox", "").strip())]
                if len(box) == 4 and all(math.isfinite(value) for value in box) and all(0 < value <= 100000 for value in box[2:]):
                    dimensions = dict(width=box[2], height=box[3])
            except ValueError:
                pass
    return dict(layout=layout if layout in {"pre-paginated", "reflowable"} else "reflowable", spread=spread if spread in {"none", "auto", "both", "landscape", "portrait"} else "auto", position=position, direction=direction, **dimensions)
