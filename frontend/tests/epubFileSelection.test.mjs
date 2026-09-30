import assert from "node:assert/strict";
import test from "node:test";
import { numberedResourceNames, selectFiles } from "../src/pages/general-tools/epubFileSelection.ts";

test("file selection supports plain, command/control, shift and additive ranges", () => {
  const files = ["a", "b", "c", "d", "e"];
  assert.deepEqual(selectFiles(files, ["e"], "e", "b", false, false), ["b"]);
  assert.deepEqual(selectFiles(files, ["b"], "b", "d", true, false), ["b", "d"]);
  assert.deepEqual(selectFiles(files, ["b", "d"], "b", "b", true, false), ["d"]);
  assert.deepEqual(selectFiles(files, ["e"], "d", "b", false, true), ["b", "c", "d"]);
  assert.deepEqual(selectFiles(files, ["e"], "b", "d", true, true), ["e", "b", "c", "d"]);
  assert.deepEqual(selectFiles(files, ["a"], "hidden", "c", false, true), ["c"]);
  assert.deepEqual(selectFiles(files, ["a"], "a", "hidden", false, false), ["a"]);
});

test("bulk names preserve folder paths and per-resource extensions", () => {
  assert.deepEqual(numberedResourceNames(["Images/a.jpg", "Other/b.png", "Text/one.xhtml"], "resource-", 3), {
    "Images/a.jpg": "Images/resource-3.jpg", "Other/b.png": "Other/resource-4.png", "Text/one.xhtml": "Text/resource-5.xhtml",
  });
});
