import assert from "node:assert/strict";
import test from "node:test";

import { nextMatchIndex, relativeResourceHref, xmlAttribute } from "../src/pages/general-tools/epubEditorActions.ts";

test("search starts at the source cursor, crosses files, and wraps", () => {
  const paths = ["Text/one.xhtml", "Text/two.xhtml", "Styles/book.css"];
  const matches = [
    { path: paths[0], start: 4 },
    { path: paths[0], start: 14 },
    { path: paths[1], start: 3 },
    { path: paths[2], start: 8 },
  ];
  assert.equal(nextMatchIndex(matches, paths, paths[0], 5), 1);
  assert.equal(nextMatchIndex(matches, paths, paths[0], 15), 2);
  assert.equal(nextMatchIndex(matches, paths, paths[1], 4), 3);
  assert.equal(nextMatchIndex(matches, paths, paths[2], 9), 0);
  assert.equal(nextMatchIndex(matches, paths, "missing.xhtml", 0), 0);
});

test("image insertion keeps relative paths and quotes XML attributes", () => {
  assert.equal(relativeResourceHref("OEBPS/Text/part/chapter.xhtml", "OEBPS/Images/a #'.png"), "../../Images/a%20%23%27.png");
  assert.equal(xmlAttribute('A & "B" < C'), 'A &amp; &quot;B&quot; &lt; C');
});
