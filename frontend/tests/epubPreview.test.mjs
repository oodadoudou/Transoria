import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { interactivePreview } from "../src/pages/general-tools/epubPreview.ts";

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

test("controller restores pages, bounds stepping, repeats anchor navigation and rejects foreign messages", async () => {
  const result = interactivePreview("<html><head></head><body></body></html>", "test123");
  const script = result.match(/<script nonce="test123">([\s\S]*)<\/script>$/)[1];
  const handlers = new Map();
  const messages = [];
  let navigations = 0;
  const parent = { postMessage: (message) => messages.push(message) };
  const context = {
    parent, innerWidth: 400, scrollX: 0, scrollY: 0,
    addEventListener: (event, callback) => handlers.set(event, callback),
    requestAnimationFrame: (callback) => callback(),
    scrollTo: (x, y) => { context.scrollX = x; context.scrollY = y; },
    document: {
      documentElement: { scrollWidth: 1600 }, head: { append() {} }, images: [], fonts: { ready: Promise.resolve() },
      createElement: () => ({ textContent: "" }), addEventListener() {}, querySelectorAll: () => [],
      getElementById: () => ({ scrollIntoView: () => { navigations++; context.scrollX = 800; } }),
    },
  };
  vm.runInNewContext(script, context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(messages[0].event, "ready");
  const send = (data, source = parent) => handlers.get("message")({ source, data: { token: "test123", ...data } });
  send({ event: "configure", mode: "paged", page: 2 });
  assert.equal(messages.at(-1).page, 2);
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
  send({ event: "configure", mode: "continuous", scroll: 125 });
  assert.equal(context.scrollY, 125);
});
