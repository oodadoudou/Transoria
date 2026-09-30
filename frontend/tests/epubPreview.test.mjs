import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { interactivePreview, previewAtZoom, PreviewCommands, wheelNavigation } from "../src/pages/general-tools/epubPreview.ts";

test("wheel paging accumulates trackpad deltas, throttles momentum and reverses", () => {
  const state = { time: 0, turn: -Infinity, amount: 0 };
  assert.equal(wheelNavigation(20, 100, state), 0);
  assert.equal(wheelNavigation(20, 110, state), 0);
  assert.equal(wheelNavigation(20, 120, state), 1);
  assert.equal(wheelNavigation(100, 150, state), 0);
  assert.equal(wheelNavigation(-80, 500, state), -1);
  assert.equal(wheelNavigation(NaN, 900, state), 0);
  assert.equal(wheelNavigation(0, 900, state), 0);
  assert.equal(wheelNavigation(30, 1000, state), 0);
  assert.equal(wheelNavigation(30, 1300, state), 0);
});

test("source columns, preview clicks, scroll origins and chapter boundaries stay synchronized", async () => {
  const script = interactivePreview("<html></html>", "sync123").match(/<script nonce="sync123">([\s\S]*)<\/script>$/)[1];
  const handlers = new Map(), frames = [], messages = [];
  const parent = { postMessage: (message) => messages.push(message) };
  let time = 1000;
  const context = {
    parent, innerWidth: 400, innerHeight: 200, scrollX: 0, scrollY: 0,
    Date: { now: () => time },
    getComputedStyle: () => ({ getPropertyValue: (key) => key === "writing-mode" ? "horizontal-tb" : "ltr" }),
    addEventListener: (event, callback) => handlers.set(event, callback),
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
    scrollTo: (x, y) => { context.scrollX = x; context.scrollY = y; },
  };
  const nodes = [100, 200, 300].map((column, index) => ({
    tagName: index === 2 ? "DIV" : "P", textContent: "Paragraph", dataset: { transoriaLine: "1", transoriaColumn: String(column) },
    getBoundingClientRect: () => ({ width: 300, height: 180, left: 0, right: 300, top: index * 200 - context.scrollY, bottom: index * 200 + 180 - context.scrollY }),
    scrollIntoView: () => { context.scrollY = index * 200; },
  }));
  context.document = {
    body: {}, documentElement: { scrollWidth: 400, scrollHeight: 600 }, head: { append() {} }, images: [], fonts: { ready: Promise.resolve() },
    createElement: () => ({ textContent: "" }), addEventListener: (event, callback) => handlers.set(event, callback),
    querySelector: () => null, querySelectorAll: () => nodes,
  };
  vm.runInNewContext(script, context);
  await new Promise((resolve) => setImmediate(resolve));
  const flush = () => { while (frames.length) frames.shift()(); };
  const send = (data) => handlers.get("message")({ source: parent, data: { token: "sync123", ...data } });
  send({ event: "configure", mode: "continuous", revision: 7 }); flush();
  send({ event: "line", line: 1, column: 250 });
  assert.equal(context.scrollY, 200);
  assert.equal(messages.at(-1).anchorColumn, 200);
  assert.equal(messages.at(-1).origin, "source");
  handlers.get("scroll")(); flush();
  assert.equal(messages.at(-1).origin, "source");
  time = 2000; context.scrollY = 410;
  handlers.get("scroll")(); flush();
  assert.equal(messages.at(-1).anchorColumn, 300);
  assert.equal(messages.at(-1).origin, "user");
  handlers.get("click")({ target: { closest: (selector) => selector.startsWith("a[") ? null : nodes[2] } });
  assert.equal(messages.at(-1).event, "locate");
  assert.equal(messages.at(-1).column, 300);
  let prevented = 0;
  handlers.get("wheel")({ deltaY: 80, deltaX: 0, deltaMode: 0, preventDefault: () => prevented++ });
  assert.equal(messages.at(-1).event, "boundary");
  assert.equal(messages.at(-1).direction, 1);
  assert.equal(messages.at(-1).revision, 7);
  assert.equal(prevented, 1);
  time = 2400; context.scrollY = 0;
  handlers.get("wheel")({ deltaY: -80, deltaX: 0, deltaMode: 0, preventDefault() {} });
  assert.equal(messages.at(-1).direction, -1);
  handlers.get("keydown")({ ctrlKey: true, key: "f", preventDefault() {} });
  assert.equal(messages.at(-1).event, "find");
});

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
  let time = 1000;
  const parent = { postMessage: (message) => messages.push(message) };
  const context = {
    parent, innerWidth: 400, innerHeight: 200, scrollX: 0, scrollY: 0,
    Date: { now: () => time },
    getComputedStyle: () => ({ getPropertyValue: (key) => key === "writing-mode" ? writingMode : direction }),
    addEventListener: (event, callback) => handlers.set(event, callback),
    requestAnimationFrame: (callback) => callback(),
    scrollTo: (x, y) => { context.scrollX = x; context.scrollY = y; },
    document: {
      body: {}, documentElement: { scrollWidth: 1600, scrollHeight: 1600 }, head: { append() {} }, images: [], fonts: { ready: Promise.resolve() },
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
  send({ event: "configure", mode: "paged", page: -1, revision: 19 });
  assert.equal(messages.at(-1).page, 3);
  assert.equal(messages.at(-1).revision, 19);
  assert.deepEqual({ ...messages.at(-2) }, { type: "epub-preview", token: "test123", event: "configured", revision: 19 });
  send({ event: "configure", mode: "continuous", page: -1 });
  assert.equal(writingMode.startsWith("vertical") ? context.scrollX : context.scrollY, writingMode.startsWith("vertical") ? sign * 1200 : 1400);
  const wheel = { deltaY: 80, deltaX: 0, deltaMode: 0, preventDefault() {} };
  handlers.get("wheel")(wheel);
  assert.equal(messages.at(-1).event, "boundary");
  assert.equal(messages.at(-1).direction, 1);
  const afterBoundary = messages.length;
  handlers.get("wheel")({ ...wheel, ctrlKey: true });
  assert.equal(messages.length, afterBoundary);
  time += 1000;
  send({ event: "configure", mode: "paged", page: 1 });
  handlers.get("wheel")(wheel);
  assert.equal(messages.at(-1).page, 2);
  assert.equal(context.scrollX, sign * 800);
  send({ event: "configure", mode: "continuous", scroll: 125, scrollX: sign * 240 });
  handlers.get("wheel")(wheel);
  if (writingMode.startsWith("vertical")) assert.equal(context.scrollX, sign * 320);
});
}

test("preview commands queue early clicks and reject stale layout acknowledgments", () => {
  const commands = new PreviewCommands("first-document");
  assert.equal(commands.configure(), null);
  assert.equal(commands.step(1), 0);
  assert.equal(commands.step(2), 0);
  commands.loaded = true;
  const old = commands.configure();
  const latest = commands.configure();
  assert.equal(commands.acknowledge(old), false);
  assert.equal(commands.acknowledge(latest), true);
  assert.equal(commands.drain(), 3);
  assert.equal(commands.drain(), 0);
  assert.equal(commands.step(-2), -2);
  const next = new PreviewCommands("next-document");
  assert.equal(next.acknowledge(latest), false);
  assert.equal(next.drain(), 0);
});

test("overlapping configuration frames discard an obsolete navigation request", async () => {
  const script = interactivePreview("<html></html>", "race123").match(/<script nonce="race123">([\s\S]*)<\/script>$/)[1];
  const handlers = new Map(), frames = [], messages = [];
  const parent = { postMessage: (message) => messages.push(message) };
  const context = {
    parent, innerWidth: 400, scrollX: 0, scrollY: 0,
    getComputedStyle: () => ({ getPropertyValue: (key) => key === "writing-mode" ? "horizontal-tb" : "ltr" }),
    addEventListener: (event, callback) => handlers.set(event, callback), requestAnimationFrame: (callback) => frames.push(callback),
    scrollTo: (x, y) => { context.scrollX = x; context.scrollY = y; },
    document: {
      body: {}, documentElement: { scrollWidth: 1600 }, head: { append() {} }, images: [], fonts: { ready: Promise.resolve() },
      createElement: () => ({ textContent: "" }), addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
    },
  };
  vm.runInNewContext(script, context);
  await new Promise((resolve) => setImmediate(resolve));
  for (const [revision, page] of [[1, -1], [2, 1]]) handlers.get("message")({ source: parent, data: { token: "race123", event: "configure", mode: "paged", revision, page } });
  while (frames.length) frames.shift()();
  assert.deepEqual(messages.filter((message) => message.event === "configured").map((message) => message.revision), [2]);
  assert.equal(context.scrollX, 400);
});

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
