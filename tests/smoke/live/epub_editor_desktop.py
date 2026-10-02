"""Launch an isolated native editor test with synthetic books and temporary state."""
from __future__ import annotations

import argparse
import faulthandler
import io
import runpy
import shutil
import signal
import tempfile
import threading
import zipfile
from pathlib import Path

import webview
from app import _DeferredDialogProvider, _EditorWindow, _build_native_api
from PIL import Image, ImageDraw

from transoria.bridge.http_server import serve


def main() -> None:
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1)
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--port", type=int, default=0, help="Reuse a test origin for restart/persistence checks.")
    parser.add_argument("--writing-mode", choices=("horizontal-tb", "vertical-rl", "vertical-lr"), default="horizontal-tb")
    parser.add_argument("--direction", choices=("ltr", "rtl"), default="ltr")
    parser.add_argument("--advanced-css", action="store_true")
    parser.add_argument("--compatibility", action="store_true")
    parser.add_argument("--inline-text", action="store_true")
    parser.add_argument("--fixed-layout", action="store_true")
    parser.add_argument("--mixed-writing", action="store_true")
    parser.add_argument("--navigation", action="store_true")
    parser.add_argument("--preview-resources", action="store_true", help="Prepare standalone SVG and non-previewable XML books for session-switch checks.")
    parser.add_argument("--state-dir", type=Path, help="Isolated test cache to retain across native test launches.")
    parser.add_argument("--separate-window", action="store_true", help="Use production native window and close handling.")
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
        if args.writing_mode != "horizontal-tb":
            paragraphs = "".join(f'<p id="p{index}">第{index}段：这是测试竖排分页的文字。不同字号与<ruby>汉字<rt>hàn zì</rt></ruby>注音应保持完整。' + "春风吹过窗外，读者能够逐列阅读，不遗漏文字。" * 4 + "</p>" for index in range(120))
        if args.inline_text:
            paragraphs = paragraphs.replace("Hello world", "Hello <em>world</em>")
        fixtures["_rewrite_book"](book, {
            "OEBPS/Images/pixel.png": buffer.getvalue(),
            "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head><body><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 600 900" width="100%" height="100%"><image xlink:href="../Images/pixel.png" width="600" height="900"/></svg><a href="two.xhtml#p55">Chapter two</a></body></html>',
            "OEBPS/Text/two.xhtml": f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test chapter</title><link rel="stylesheet" href="../Styles/book.css"/></head><body><h1>Test chapter</h1>{paragraphs}</body></html>',
            "OEBPS/Styles/book.css": f'body {{font:16px/1.6 serif;color:#202020;margin:24px;writing-mode:{args.writing_mode};direction:{args.direction}}} p {{margin:1em 0}} p:nth-child(5n){{font-size:22px}} .unused {{color:blue}}',
        })
        if args.navigation:
            fixtures["_rewrite_book"](book, {"OEBPS/Text/one.xhtml": f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>First chapter</title><link rel="stylesheet" href="../Styles/book.css"/></head><body><a href="two.xhtml#p55">Chapter two</a><h1>First chapter</h1>{paragraphs}</body></html>'})
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
        if args.compatibility:
            with zipfile.ZipFile(book) as archive:
                opf = archive.read("OEBPS/book.opf")
            fixtures["_rewrite_book"](book, {
                "OEBPS/book.opf": opf.replace(b"</manifest>", b'<item id="font" href="Fonts/test.ttf" media-type="font/ttf"/></manifest>'),
                "OEBPS/Fonts/test.ttf": fixtures["_synthetic_font"](),
                "OEBPS/Styles/book.css": '@font-face{font-family:TestFont;src:url("../Fonts/test.ttf")}body{font:20px/1.5 serif;margin:24px}.embedded{font:64px TestFont}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.grid p{padding:20px;background:#d5ebe2}@supports(display:grid){.grid{border:3px solid #216f54}}',
                "OEBPS/Text/two.xhtml": '<html><head><title>Compatibility test</title><link rel="stylesheet" href="../Styles/book.css"></head><body><h1>Malformed XHTML preview</h1><p class="embedded">ABC</p><div class="grid"><p>Recovered &nbsp; left column<br>Unclosed paragraph<p>Right column with <ruby>漢<rt>kan</rt></ruby></div><table><tr><td>HTML5 table recovery</table></div></body></html>',
            })
        if args.fixed_layout:
            with zipfile.ZipFile(book) as archive:
                opf = archive.read("OEBPS/book.opf")
            fixtures["_rewrite_book"](book, {
                "OEBPS/book.opf": opf.replace(b"</metadata>", b'<meta property="rendition:layout">pre-paginated</meta><meta property="rendition:spread">both</meta></metadata>').replace(b'<itemref idref="one"', b'<itemref properties="page-spread-left" idref="one"').replace(b'<itemref idref="two"', b'<itemref properties="page-spread-right" idref="two"'),
                "OEBPS/Text/one.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta name="viewport" content="width=600,height=900"/><style>body{margin:0;width:600px;height:900px;background:#e7eef0}img{position:absolute;inset:0;width:600px;height:900px}h1{position:absolute;left:60px;top:60px;color:#166d65}</style></head><body><img src="../Images/pixel.png"/><h1>LEFT PAGE</h1></body></html>',
                "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta name="viewport" content="width=600,height=900"/><style>body{margin:0;width:600px;height:900px;background:#e8dbf0}h1{position:absolute;left:60px;top:60px}p{position:absolute;left:60px;bottom:60px}</style></head><body><h1>RIGHT PAGE</h1><p>Bottom of fixed page</p></body></html>',
            })
            if args.direction == "rtl":
                with zipfile.ZipFile(book) as archive:
                    opf = archive.read("OEBPS/book.opf")
                fixtures["_rewrite_book"](book, {"OEBPS/book.opf": opf.replace(b'<spine toc=', b'<spine page-progression-direction="rtl" toc=').replace(b'page-spread-left" idref="one"', b'page-spread-right" idref="one"').replace(b'page-spread-right" idref="two"', b'page-spread-left" idref="two"')})
        if args.mixed_writing:
            fixtures["_rewrite_book"](book, {
                "OEBPS/Text/two.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="../Styles/book.css"/></head><body><h1>Mixed writing modes</h1><div class="columns"><section class="rl"><h2>右到左</h2><p>竖排正文与<ruby>汉字<rt>hàn zì</rt></ruby>注音。</p><p>第二列<span class="number">2026</span>年。</p><aside>Horizontal inset</aside></section><section class="lr"><h2>左到右</h2><p>不同书写方向保留自己的文字排列。</p><p>LTR vertical block</p></section></div><p class="rtl" dir="rtl">עברית English 123 العربية</p><p>Horizontal text below the vertical panels.</p></body></html>',
                "OEBPS/Styles/book.css": 'body{font:20px/1.7 serif;margin:20px;writing-mode:horizontal-tb}.columns{display:flex;gap:24px}.columns section{width:45%;height:230px;border:2px solid #216f54;padding:12px;box-sizing:border-box}.rl{-epub-writing-mode:tb-rl}.lr{-ms-writing-mode:tb-lr}.number{-epub-text-combine:horizontal}aside{writing-mode:horizontal-tb;font-size:14px;background:#d5ebe2}ruby{ruby-position:over}.rtl{direction:rtl;unicode-bidi:isolate}',
            })
        state_dir = args.state_dir or folder / "cache"
        if args.preview_resources:
            with zipfile.ZipFile(book) as archive:
                opf = archive.read("OEBPS/book.opf")
                nav = archive.read("OEBPS/nav.xhtml")
                ncx = archive.read("OEBPS/toc.ncx")
            svg_book = folder / "svg-test.epub"
            xml_book = folder / "xml-test.epub"
            shutil.copyfile(book, svg_book)
            shutil.copyfile(book, xml_book)
            fixtures["_rewrite_book"](svg_book, {
                "OEBPS/book.opf": opf.replace(b'href="Text/one.xhtml" media-type="application/xhtml+xml"', b'href="Text/cover.svg" media-type="image/svg+xml"'),
                "OEBPS/nav.xhtml": nav.replace(b"Text/one.xhtml", b"Text/cover.svg"),
                "OEBPS/toc.ncx": ncx.replace(b"Text/one.xhtml", b"Text/cover.svg"),
                "OEBPS/Text/cover.svg": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 600 900"><image xlink:href="../Images/pixel.png" width="600" height="900"/><text x="50" y="100" font-size="40">SVG SESSION TEST</text></svg>',
            })
            fixtures["_rewrite_book"](xml_book, {
                "OEBPS/book.opf": opf.replace(b'href="Text/one.xhtml" media-type="application/xhtml+xml"', b'href="Text/one.xhtml" media-type="application/xml"'),
                "OEBPS/Text/one.xhtml": '<document><title>XML session: no chapter preview</title></document>',
            })
            print(f"SVG BOOK: {svg_book}\nXML BOOK: {xml_book}", flush=True)
        server = serve(port=args.port, cache_root=state_dir, static_root=root / "frontend/dist")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"URL: {url}\nBOOK: {book}", flush=True)
        try:
            if args.separate_window:
                editor = _EditorWindow(webview, url)
                provider = _DeferredDialogProvider()
                parent = webview.create_window("Transoria test", f"{url}?desktop=1", js_api=_build_native_api(provider, editor), width=args.width, height=args.height, min_size=(960, 600))
                provider.activate(parent)
                callback = lambda: editor.open(str(book))
            else:
                webview.create_window("Transoria EPUB test", url, width=args.width, height=args.height, min_size=(960, 600))
                callback = None
            webview.start(callback, private_mode=False, storage_path=str(state_dir / "desktop-webview"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
