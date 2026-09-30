from __future__ import annotations

import html
from html.parser import HTMLParser

BLOCKS = {"address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd", "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul"}
HIDDEN = {"head", "script", "style", "noscript", "template"}


class VisibleText(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        self.lines.extend(index + 1 for index, char in enumerate(source) if char == "\n")
        self.characters: list[str] = []
        self.positions: list[tuple[int, int] | None] = []
        self.hidden: list[str] = []
        self.feed(source)
        self.close()
        self.text = "".join(self.characters)

    def source_offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def boundary(self) -> None:
        if self.characters and self.characters[-1] != "\n":
            self.characters.append("\n")
            self.positions.append(None)

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.split(":")[-1]
        if tag in HIDDEN:
            self.hidden.append(tag)
        if not self.hidden and tag in BLOCKS:
            self.boundary()

    def handle_startendtag(self, tag, attrs) -> None:
        tag = tag.split(":")[-1]
        if tag in BLOCKS and not self.hidden:
            self.boundary()

    def handle_endtag(self, tag) -> None:
        tag = tag.split(":")[-1]
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]
        if tag in BLOCKS and not self.hidden:
            self.boundary()

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        offset = self.source_offset()
        for index, char in enumerate(data):
            if char.isspace():
                # Formatting whitespace follows browser collapse, not source indentation.
                if not self.characters or self.characters[-1].isspace():
                    continue
                char = " "
            self.characters.append(char)
            self.positions.append((offset + index, offset + index + 1))

    def entity(self, token: str) -> None:
        if self.hidden:
            return
        start = self.source_offset()
        raw = token + (";" if self.source[start + len(token):start + len(token) + 1] == ";" else "")
        for char in html.unescape(raw):
            self.characters.append(char)
            self.positions.append((start, start + len(raw)))

    def handle_entityref(self, name) -> None:
        self.entity("&" + name)

    def handle_charref(self, name) -> None:
        self.entity("&#" + name)

    def source_range(self, start: int, end: int) -> tuple[int, int]:
        spans = [span for span in self.positions[start:end] if span is not None]
        if not spans:
            raise ValueError("The match contains no editable text.")
        return spans[0][0], spans[-1][1]

    def replacement_edits(self, start: int, end: int, replacement: str) -> list[tuple[int, int, str]]:
        positions = self.positions[start:end]
        if any(span is None for span in positions):
            raise ValueError("Text replacement cannot cross paragraph or structural boundaries.")
        first, last = positions[0], positions[-1]
        if (start and self.positions[start - 1] == first) or (end < len(self.positions) and self.positions[end] == last):
            raise ValueError("Text replacement cannot split an encoded entity.")
        spans: list[tuple[int, int]] = []
        for span in positions:
            if span is None:
                continue
            if spans and (span[0] <= spans[-1][1] or self.source[spans[-1][1]:span[0]].isspace()):
                spans[-1] = (spans[-1][0], max(spans[-1][1], span[1]))
            else:
                spans.append(span)
        # Keep intervening markup untouched; replace only the matched text runs.
        return [(left, right, html.escape(replacement, quote=False) if index == 0 else "") for index, (left, right) in enumerate(spans)]
