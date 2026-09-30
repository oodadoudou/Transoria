import assert from "node:assert/strict";
import test from "node:test";

import { nextMatchIndex, previewDestination, relativeResourceHref, resourceAfterHistory, savedEditorHistory, xmlAttribute } from "../src/pages/general-tools/epubEditorActions.ts";
import { EditorState } from "@codemirror/state";
import { history, undo, undoDepth } from "@codemirror/commands";

test("restoring a file keeps local history but never restores stale read-only extensions", () => {
  const base = EditorState.create({ doc: "Hello", extensions: [history(), EditorState.readOnly.of(true)] });
  const edited = base.update({ changes: { from: 5, insert: " world" }, selection: { anchor: 11 } }).state;
  const snapshot = savedEditorHistory(edited, "Hello world");
  let state = EditorState.fromJSON(snapshot.json, { extensions: [history(), EditorState.readOnly.of(false)] }, snapshot.fields);
  assert.equal(state.readOnly, false);
  assert.equal(state.selection.main.head, 11);
  assert.equal(undoDepth(state), 1);
  undo({ state, dispatch: (transaction) => { state = transaction.state; } });
  assert.equal(state.doc.toString(), "Hello");
  assert.equal(savedEditorHistory(edited, "Different content"), undefined);
});

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

test("preview links retain encoded and malformed anchors without crashing", () => {
  assert.deepEqual(previewDestination("Text/a.xhtml#%E7%AB%A0%E8%8A%82"), { path: "Text/a.xhtml", fragment: "章节" });
  assert.deepEqual(previewDestination("Text/a.xhtml#100%"), { path: "Text/a.xhtml", fragment: "100%" });
  assert.deepEqual(previewDestination("Text/a.xhtml#a#b"), { path: "Text/a.xhtml", fragment: "a#b" });
  assert.deepEqual(previewDestination("Text/a.xhtml"), { path: "Text/a.xhtml", fragment: "" });
});

test("undo after creating a chapter selects an existing neighbor", () => {
  const previous = ["Text/one.xhtml", "Text/new.xhtml", "Text/two.xhtml"];
  const files = [
    { path: "Text/one.xhtml", editable: true },
    { path: "Text/two.xhtml", editable: true },
    { path: "Images/cover.png", editable: false },
  ];
  assert.equal(resourceAfterHistory("Text/new.xhtml", previous, files), "Text/one.xhtml");
  assert.equal(resourceAfterHistory("Text/two.xhtml", previous, files), "Text/two.xhtml");
  assert.equal(resourceAfterHistory("missing", previous, files), "Text/one.xhtml");
  assert.equal(resourceAfterHistory("Text/new.xhtml", previous, [{ path: "Images/cover.png", editable: false }]), "");
});
