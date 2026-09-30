import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { interactivePreview, previewAtZoom } from "../src/pages/general-tools/epubPreview.ts";

test("preview controller parses and runs in an opaque sandbox with a nonce", () => {
  const markup = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:"><html><head></head><body>Test</body></html>`;
  const result = interactivePreview(markup, "test123");
  assert.match(result, /script-src 'nonce-test123'/);
  const script = result.match(/<script nonce="test123">([\s\S]*)<\/script>$/)[1];
  assert.doesNotThrow(() => new vm.Script(script));
  assert.match(script, /event.source !== parent/);
  assert.match(script, /event.data\?\.token !== token/);
  assert.match(script, /document.fonts.ready/);
  assert.match(script, /column-width/);
  assert.match(script, /data-transoria-line/);
  assert.match(interactivePreview(markup.replaceAll("'", "&#x27;"), "test123"), /script-src 'nonce-test123'/);
});

for (const [writingMode, direction, sign] of [["horizontal-tb", "ltr", 1], ["horizontal-tb", "rtl", -1], ["vertical-rl", "ltr", -1], ["vertical-lr", "ltr", 1]]) {
test(`controller restores and navigates ${writingMode}/${direction} pages and continuous offsets`, async () => {
  const result = interactivePreview("<html><head></head><body></body></html>", "test123");
  const script = result.match(/<script nonce="test123">([\s\S]*)<\/script>$/)[1];
  const handlers = new Map();
  const messages = [];
  let navigations = 0;
  const parent = { postMessage: (message) => messages.push(message) };
  const context = {
    parent, innerWidth: 400, scrollX: 0, scrollY: 0,
    getComputedStyle: () => ({ getPropertyValue: (key) => key === "writing-mode" ? writingMode : direction }),
    addEventListener: (event, callback) => handlers.set(event, callback),
    requestAnimationFrame: (callback) => callback(),
    scrollTo: (x, y) => { context.scrollX = x; context.scrollY = y; },
    document: {
      body: {}, documentElement: { scrollWidth: 1600 }, head: { append() {} }, images: [], fonts: { ready: Promise.resolve() },
      createElement: () => ({ textContent: "" }), addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      getElementById: () => ({ scrollIntoView: () => { navigations++; context.scrollX = sign * 800; } }),
    },
  };
  vm.runInNewContext(script, context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(messages[0].event, "ready");
  const send = (data, source = parent) => handlers.get("message")({ source, data: { token: "test123", ...data } });
  send({ event: "configure", mode: "paged", page: 2 });
  assert.equal(messages.at(-1).page, 2);
  assert.equal(context.scrollX, sign * 800);
  send({ event: "step", direction: 1 });
  send({ event: "step", direction: 1 });
  assert.equal(messages.at(-1).page, 3);
  send({ event: "step", direction: -1 });
  assert.equal(messages.at(-1).page, 2);
  send({ event: "configure", mode: "paged", fragment: "chapter", navigation: 1 });
  send({ event: "configure", mode: "paged", fragment: "chapter", navigation: 1 });
  assert.equal(navigations, 1);
  send({ event: "configure", mode: "paged", fragment: "chapter", navigation: 2 });
  assert.equal(navigations, 2);
  const count = messages.length;
  send({ event: "step", direction: -1 }, {});
  send({ event: "step", direction: -1, token: "foreign" });
  assert.equal(messages.length, count);
  send({ event: "configure", mode: "continuous", scroll: 125, scrollX: sign * 240 });
  assert.equal(context.scrollY, 125);
  assert.equal(context.scrollX, sign * 240);
  assert.equal(messages.at(-1).scrollX, sign * 240);
});
}

test("wrapping does not clip vertical flow or override author whitespace and writing modes", () => {
  const original = "<style>img{max-width:100%!important;max-height:calc(100vh - 24px)!important;}body{writing-mode:vertical-rl;white-space:pre}</style>";
  const markup = previewAtZoom(original, 80, true);
  assert.match(markup, /max-width:80%/);
  assert.match(markup, /max-height:calc\(80vh/);
  assert.match(markup, /writing-mode:vertical-rl;white-space:pre/);
  assert.doesNotMatch(markup, /overflow-x:hidden|white-space:normal|word-break:break-word/);
  assert.equal(previewAtZoom(original, 100, false), original);
});

for (const [writingMode, sign] of [["vertical-rl", -1], ["vertical-lr", 1]]) {
test(`native column rectangles determine complete ${writingMode} pages and edge clipping`, async () => {
  const result = interactivePreview("<html><head></head><body></body></html>", "columns123");
  const script = result.match(/<script nonce="columns123">([\s\S]*)<\/script>$/)[1];
  const handlers = new Map(), messages = [];
  const parent = { postMessage: (message) => messages.push(message) };
  const positions = Array.from({ length: 24 }, (_, i) => [12 + i * 37, 38 + i * 37]);
  let visited = false;
  const context = {
    parent, innerWidth: 320, scrollX: 0, scrollY: 0,
    getComputedStyle: () => ({ getPropertyValue: (key) => key === "writing-mode" ? writingMode : "ltr" }),
    addEventListener: (event, callback) => handlers.set(event, callback), requestAnimationFrame: (callback) => callback(),
    scrollTo: (x, y) => { context.scrollX = x; context.scrollY = y; },
    document: {
      body: {}, documentElement: { scrollWidth: 910, append() {} }, head: { append() {} }, images: [],
      fonts: { ready: Promise.resolve(), addEventListener: (event, callback) => handlers.set(event, callback) },
      createElement: () => ({ textContent: "", style: {}, remove() {}, setAttribute() {} }),
      createTreeWalker: () => { visited = false; return { nextNode: () => visited ? null : (visited = true, { textContent: "column text" }) }; },
      createRange: () => ({ selectNodeContents() {}, getClientRects: () => positions.map(([start, end]) => ({ left: sign < 0 ? 320 - end : start, right: sign < 0 ? 320 - start : end, width: end - start, height: 270 })) }),
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
    },
  };
  vm.runInNewContext(script, context);
  await new Promise((resolve) => setImmediate(resolve));
  const send = (data) => handlers.get("message")({ source: parent, data: { token: "columns123", ...data } });
  send({ event: "configure", mode: "paged" });
  assert.equal(messages.at(-1).pages, 3);
  assert.ok(messages.at(-1)[sign < 0 ? "clipLeft" : "clipRight"] > 0);
  send({ event: "step", direction: 1 });
  assert.equal(messages.at(-1).page, 1);
  assert.equal(context.scrollX, sign * 296);
  send({ event: "step", direction: 1 });
  assert.equal(messages.at(-1).page, 2);
  assert.equal(context.scrollX, sign * 592);
  assert.equal(messages.at(-1)[sign < 0 ? "clipLeft" : "clipRight"], 0);
  assert.equal(messages.at(-1)[sign < 0 ? "clipRight" : "clipLeft"], 12);
  send({ event: "step", direction: -1 });
  assert.equal(context.scrollX, sign * 296);
  assert.doesNotMatch(script, /documentElement\.style\.clipPath/);
  assert.equal(typeof handlers.get("loadingdone"), "function");
});
}
