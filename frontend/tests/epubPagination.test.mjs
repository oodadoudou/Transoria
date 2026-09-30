import assert from "node:assert/strict";
import test from "node:test";
import { columnPageOffsets } from "../src/pages/general-tools/epubPreview.ts";

test("vertical pages snap to complete columns, including overlapping ruby fragments", () => {
  const columns = Array.from({ length: 24 }, (_, i) => [12 + i * 37, 38 + i * 37]);
  const offsets = columnPageOffsets([...columns, [25, 43], [288, 307]], 320, columns.at(-1)[1] + 12);
  assert.ok(offsets.length > 1);
  assert.notEqual(offsets[1], 320);
  for (const offset of offsets.slice(1)) {
    assert.ok(!columns.some(([start, end]) => start < offset + 12 && end > offset + 12));
  }
});

test("pagination handles blank, wide, malformed and nonuniform columns without loops", () => {
  assert.deepEqual(columnPageOffsets([], 400, 800), [0, 400]);
  assert.deepEqual(columnPageOffsets([[12, 900]], 400, 912), [0, 400, 800]);
  assert.deepEqual(columnPageOffsets([[NaN, 30], [4, Infinity]], 400, 200), [0]);
  assert.deepEqual(columnPageOffsets([], 0, 100), [0]);
  const result = columnPageOffsets([[12, 55], [90, 140], [170, 230], [275, 330], [370, 440]], 300, 452);
  assert.deepEqual(result, [0, 263]);
});
