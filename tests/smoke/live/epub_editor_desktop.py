"""Launch an isolated native editor test with synthetic books and temporary state."""
from __future__ import annotations

import argparse
import io
import runpy
import tempfile
import threading
import zipfile
from pathlib import Path

import webview
from PIL import Image, ImageDraw

from transoria.bridge.http_server import serve


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--writing-mode", choices=("horizontal-tb", "vertical-rl", "vertical-lr"), default="horizontal-tb")
    parser.add_argument("--direction", choices=("ltr", "rtl"), default="ltr")
    parser.add_argument("--advanced-css", action="store_true")
    parser.add_argument("--state-dir", type=Path, help="Isolated test cache to retain across native test launches.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    fixtures = runpy.run_path(str(root / "tests/unit/tools/test_tools_epub_content.py"))
    with tempfile.TemporaryDirectory(prefix="transoria-editor-smoke-") as directory:
        folder = Path(directory)
        book = folder / "editor-test.epub"
        fixtures["_book"](book)
        image = Image.new("RGB", (600, 900), "#e7eef0")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 560, 860), outline="#166d65", width=12)
        draw.text((100, 350), "EPUB EDITOR TEST", fill="#181818", font_size=32)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        paragraphs = "".join(f'<p id="p{index}">Paragraph {index}: Hello world. This is a synthetic chapter for pagination, search and source mapping.</p>' for index in range(120))
        fixtures["_rewrite_book"](book, {
            "OEBPS/Images/pixel.png": buffer.getvalue(),
            "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head><body><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 600 900" width="100%" height="100%"><image xlink:href="../Images/pixel.png" width="600" height="900"/></svg><a href="two.xhtml#p55">Chapter two</a></body></html>',
            "OEBPS/Text/two.xhtml": f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test chapter</title><link rel="stylesheet" href="../Styles/book.css"/></head><body><h1>Test chapter</h1>{paragraphs}</body></html>',
            "OEBPS/Styles/book.css": f'body {{font:16px/1.6 serif;color:#202020;margin:24px;writing-mode:{args.writing_mode};direction:{args.direction}}} p {{margin:1em 0}} .unused {{color:blue}}',
        })
        if args.advanced_css:
            with zipfile.ZipFile(book) as archive:
                opf = archive.read("OEBPS/book.opf")
            fixtures["_rewrite_book"](book, {
                "OEBPS/book.opf": opf.replace(b"</manifest>", b'<item id="imported" href="Styles/imported.css" media-type="text/css"/><item id="alternate" href="Styles/alternate.css" media-type="text/css"/></manifest>'),
                "OEBPS/Styles/book.css": '@import "imported.css" layer(book) supports(display:grid) screen; body{font:16px/1.6 serif;margin:24px}',
                "OEBPS/Styles/imported.css": '@namespace x url(http://www.w3.org/1999/xhtml); x|div{display:grid;grid-template-columns:1fr 1fr;gap:20px} x|p{padding:20px;background:#d5ebe2;border:3px solid #216f54} x|ruby{ruby-position:over}',
                "OEBPS/Styles/alternate.css": 'body{background:red!important}',
                "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="../Styles/book.css"/><link rel="alternate stylesheet" title="Night" href="../Styles/alternate.css"/></head><body><h1>Conditional CSS test</h1><div><p>Left grid column: <ruby>漢<rt>kan</rt></ruby></p><p>Right grid column</p></div></body></html>',
            })
        server = serve(port=0, cache_root=args.state_dir or folder / "cache", static_root=root / "frontend/dist")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"URL: {url}\nBOOK: {book}", flush=True)
        try:
            webview.create_window("Transoria EPUB test", url, width=args.width, height=args.height, min_size=(960, 600))
            webview.start()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
