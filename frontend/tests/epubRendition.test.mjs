import assert from "node:assert/strict";
import test from "node:test";
import { fixedPageSize, fixedSpreadMate } from "../src/pages/general-tools/epubRendition.ts";

test("fixed pages retain authored viewport and fit both axes at adjustable zoom", () => {
  const layout = { layout: "pre-paginated", width: 1200, height: 600 };
  assert.deepEqual(fixedPageSize(layout, { width: 600, height: 400 }, 100), { width: 1200, height: 600, scale: .5, stageWidth: 600, stageHeight: 400 });
  assert.equal(fixedPageSize(layout, { width: 600, height: 400 }, 200).stageWidth, 1200);
  const device = fixedPageSize({ layout: "pre-paginated", width: "device-width", height: "device-height" }, { width: 300, height: 400 }, 80);
  assert.equal(device.scale, .8);
  assert.equal(device.width, 300);
  assert.equal(fixedPageSize({ layout: "pre-paginated", width: Infinity, height: -1 }, { width: 300, height: 400 }, 100).width, 600);
});

test("spreads respect page placement, spine progression, centers and boundaries", () => {
  const layout = { layout: "pre-paginated", direction: "ltr" };
  assert.equal(fixedSpreadMate(layout, 0, 5), null);
  assert.equal(fixedSpreadMate(layout, 1, 5), 2);
  assert.equal(fixedSpreadMate(layout, 2, 5), 1);
  assert.equal(fixedSpreadMate({ ...layout, position: "right" }, 0, 5), null);
  assert.equal(fixedSpreadMate({ ...layout, direction: "rtl", position: "right" }, 0, 5), 1);
  assert.equal(fixedSpreadMate({ ...layout, spread: "none" }, 1, 5), null);
  assert.equal(fixedSpreadMate({ ...layout, position: "center" }, 1, 5), null);
  assert.equal(fixedSpreadMate({ ...layout, position: "left" }, 4, 5), null);
  assert.equal(fixedSpreadMate({ layout: "reflowable" }, 1, 5), null);
});
