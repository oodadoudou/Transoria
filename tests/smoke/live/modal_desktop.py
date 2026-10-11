"""Verify real desktop WebView modal layout using isolated synthetic fixtures."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import webview


LAYOUT = """(() => {
  const overlay = document.querySelector('[aria-modal="true"]');
  if (!overlay) return null;
  const surface = overlay.firstElementChild;
  const title = surface.querySelector('h2, h3');
  const close = [...surface.querySelectorAll('button')].find(e =>
    /^(关闭|取消|Close|Cancel|×)$/.test(e.textContent.trim()) || /^(关闭|取消|Close|Cancel)$/.test(e.getAttribute('aria-label') || '')) ||
    [...surface.querySelectorAll('button')].at(-1);
  const rect = element => {
    const r = element.getBoundingClientRect();
    return {x:r.x, y:r.y, width:r.width, height:r.height, right:r.right, bottom:r.bottom};
  };
  const accessible = element => {
    if (!element) return false;
    const r = rect(element);
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return r.x >= -1 && r.y >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1 &&
      Boolean(hit && (element.contains(hit) || hit.contains(element)));
  };
  return {viewport:{width:innerWidth,height:innerHeight}, surface:rect(surface),
    portal:overlay.parentElement === document.body,
    title:accessible(title), close:accessible(close), rows:surface.querySelectorAll('tbody tr').length};
})()"""

SCROLL = """(() => {
  const overlay = document.querySelector('[aria-modal="true"]');
  const elements = [...overlay.querySelectorAll('*')];
  const scrollable = elements.filter(e => /auto|scroll/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight);
  for (const element of scrollable) element.scrollTop = element.scrollHeight;
  const table = overlay.querySelector('table');
  const last = table?.querySelector('tbody tr:last-child');
  const tableWrap = table?.parentElement;
  if (tableWrap) tableWrap.scrollLeft = tableWrap.scrollWidth;
  const visible = (e, parent) => {const r=e.getBoundingClientRect(), p=parent.getBoundingClientRect();
    return r.bottom <= Math.min(p.bottom,innerHeight)+1 && r.bottom > Math.max(p.top,0);};
  return {vertical:scrollable.length, lastRow:!last || visible(last,tableWrap),
    horizontal:tableWrap ? tableWrap.scrollLeft : 0,
    responseColumn:!last || (() => {const r=last.lastElementChild.getBoundingClientRect(), p=tableWrap.getBoundingClientRect();
      return visible(last.lastElementChild,tableWrap) && r.left >= Math.max(p.left,0)-1 && r.right <= Math.min(p.right,innerWidth)+1;})(),
    reachedEnd:scrollable.every(e => e.scrollTop >= e.scrollHeight-e.clientHeight-2)};
})()"""


def wait_for(window, expression, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(expression)
        if predicate(result):
            return result
        time.sleep(0.05)
    raise AssertionError(f"Native fixture did not settle: {result}")


def set_zoom(window, value):
    if sys.platform != "darwin":
        raise RuntimeError("Native WKWebView page-zoom checks require macOS")
    from PyObjCTools import AppHelper
    from webview.platforms.cocoa import BrowserView

    done = threading.Event()
    errors = []

    def apply():
        try:
            BrowserView.instances[window.uid].webview.setPageZoom_(value)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    AppHelper.callAfter(apply)
    if not done.wait(10):
        raise TimeoutError("Native page zoom timed out")
    if errors:
        raise errors[0]


def snapshot(window, path):
    from AppKit import NSBitmapImageRep, NSBitmapImageFileTypePNG
    from PyObjCTools import AppHelper
    from webview.platforms.cocoa import BrowserView

    done = threading.Event()
    errors = []

    def complete(image, error):
        try:
            if error:
                raise RuntimeError(str(error))
            bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
            path.write_bytes(bytes(bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})))
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    AppHelper.callAfter(lambda: BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None, complete))
    if not done.wait(10):
        raise TimeoutError("Native screenshot timed out")
    if errors:
        raise errors[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    fixture = f"{args.url.rstrip('/')}/tests/fixtures/modal-desktop.html"
    results = []
    errors = []
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    window = webview.create_window("Transoria Modal Test", f"{fixture}?dialog=request", width=1280, height=800, min_size=(640, 400))

    def verify():
        try:
            for dialog in ("request", "switch", "restart"):
                window.resize(960, 600)
                set_zoom(window, 1.5)
                window.load_url(f"{fixture}?dialog={dialog}&paintLayer=1")
                result = wait_for(window, LAYOUT, lambda r: r and (dialog != "request" or r["rows"] == 200))
                assert result["title"] and result["close"], result
                results.append({"compositedScrollContainer": dialog, "layout": result})
            for dialog in ("request", "switch", "failures", "restart", "completion", "export", "stats", "prompt", "model"):
                for width, height in ((1280, 800), (960, 600), (1440, 900)):
                    for zoom in (0.85, 1.0, 1.25, 1.5):
                        query = urlencode({"dialog": dialog, "theme": "dark" if zoom >= 1 else "light", "locale": "en" if zoom == 1.25 else "zh"})
                        window.resize(width, height)
                        set_zoom(window, zoom)
                        window.load_url(f"{fixture}?{query}")
                        layout = wait_for(window, LAYOUT, lambda r: r and (dialog != "request" or r["rows"] == 200))
                        time.sleep(0.1)
                        layout = window.evaluate_js(LAYOUT)
                        assert layout["title"] and layout["close"], layout
                        bounds, viewport = layout["surface"], layout["viewport"]
                        assert bounds["x"] >= -1 and bounds["right"] <= viewport["width"] + 1, layout
                        assert bounds["y"] >= -1 and bounds["bottom"] <= viewport["height"] + 1, layout
                        scroll = window.evaluate_js(SCROLL)
                        assert scroll["lastRow"] and scroll["responseColumn"] and scroll["reachedEnd"], scroll
                        result = {"dialog": dialog, "window": [width, height], "actualWindow": [window.width, window.height], "zoom": zoom, "layout": layout, "scroll": scroll}
                        results.append(result)
                        print(json.dumps(result), flush=True)
                        if args.screenshots and width == 960 and zoom == 1.5:
                            snapshot(window, args.screenshots / f"{dialog}-960-150.png")
            for kind in ("glossary", "glossary_review"):
                window.load_url(f"{fixture}?dialog=request&kind={kind}")
                result = wait_for(window, LAYOUT, lambda r: r and r["rows"] == 200)
                assert result["title"] and result["close"], result
                results.append({"kind": kind, "layout": result})
            window.load_url(f"{fixture}?dialog=request")
            wait_for(window, LAYOUT, lambda r: r and r["rows"] == 200)
            window.evaluate_js("document.querySelector('[aria-modal=true] tbody tr button').click()")
            wait_for(window, LAYOUT, lambda r: r and r["rows"] == 201)
            for width, height, zoom in ((960, 600, 1.5), (1280, 800, 0.85), (960, 600, 1.25), (1280, 800, 1.0)):
                window.resize(width, height)
                set_zoom(window, zoom)
                time.sleep(0.15)
                result = window.evaluate_js(LAYOUT)
                assert result["title"] and result["close"] and result["rows"] == 201, result
                scroll = window.evaluate_js(SCROLL)
                assert scroll["lastRow"] and scroll["responseColumn"] and scroll["reachedEnd"], scroll
                results.append({"openResize": [width, height, zoom], "layout": result, "scroll": scroll})
            window.evaluate_js("document.querySelector('[aria-modal=true] button[class*=loadMore]').click()")
            wait_for(window, LAYOUT, lambda r: r and r["rows"] == 221)
            window.evaluate_js("[...document.querySelector('[aria-modal=true]').firstElementChild.firstElementChild.querySelectorAll('button')].at(-1).click()")
            wait_for(window, LAYOUT, lambda r: r is None)
            results.append({"expandedResponse": True, "loadOlder": True, "close": True})
        except Exception as error:
            errors.append(str(error))
            print(f"FAIL: {error}", flush=True)
            if args.screenshots:
                snapshot(window, args.screenshots / "failed.png")
        finally:
            if args.report:
                args.report.write_text(json.dumps({"results": results, "errors": errors}, indent=2), encoding="utf-8")
            window.destroy()

    with tempfile.TemporaryDirectory(prefix="transoria-modal-test-") as storage:
        webview.start(verify if args.verify else None, private_mode=True, storage_path=storage)
    if errors:
        raise SystemExit(1)
    if args.verify:
        print(f"PASS: {len(results)} native desktop cases", flush=True)


if __name__ == "__main__":
    main()
