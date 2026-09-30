import assert from "node:assert/strict";
import test from "node:test";

import { nextMatchIndex, previewDestination, relativeResourceHref, reorderedSpine, resourceAfterHistory, resourceAfterMutation, resourcePreviewPath, savedEditorHistory, searchScopePaths, xmlAttribute } from "../src/pages/general-tools/epubEditorActions.ts";
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

test("live and saved searches use identical scopes and spine-first order", () => {
  const files = [
    { path: "book.opf", media_type: "application/oebps-package+xml", editable: true },
    { path: "two.xhtml", media_type: "application/xhtml+xml", editable: true },
    { path: "book.css", media_type: "text/css", editable: true },
    { path: "notes.txt", media_type: "text/plain", editable: true },
    { path: "one.xhtml", media_type: "application/xhtml+xml", editable: true },
    { path: "cover.jpg", media_type: "image/jpeg", editable: false },
  ];
  const spine = ["one.xhtml", "two.xhtml", "one.xhtml", "missing.xhtml"];
  assert.deepEqual(searchScopePaths("all", "one.xhtml", files, spine), ["one.xhtml", "two.xhtml", "book.opf", "book.css", "notes.txt"]);
  assert.deepEqual(searchScopePaths("text", "one.xhtml", files, spine), ["one.xhtml", "two.xhtml", "notes.txt"]);
  assert.deepEqual(searchScopePaths("styles", "one.xhtml", files, spine), ["book.css"]);
  for (const scope of ["current", "selection"]) {
    assert.deepEqual(searchScopePaths(scope, "one.xhtml", files, spine), ["one.xhtml"]);
    assert.deepEqual(searchScopePaths(scope, "cover.jpg", files, spine), []);
  }
  assert.deepEqual(searchScopePaths("unknown", "one.xhtml", files, spine), []);
});

test("image insertion keeps relative paths and quotes XML attributes", () => {
  assert.equal(relativeResourceHref("OEBPS/Text/part/chapter.xhtml", "OEBPS/Images/a #'.png"), "../../Images/a%20%23%27.png");
  assert.equal(xmlAttribute('A & "B" < C'), 'A &amp; &quot;B&quot; &lt; C');
});

test("reading order moves before/after without losing chapters or accepting stale targets", () => {
  const spine = ["one", "two", "three"];
  assert.deepEqual(reorderedSpine(spine, "one", "three", true), ["two", "three", "one"]);
  assert.deepEqual(reorderedSpine(spine, "three", "one", false), ["three", "one", "two"]);
  assert.deepEqual(reorderedSpine(spine, "two", "three", true), ["one", "three", "two"]);
  for (const [source, target, after] of [["one", "one", true], ["one", "two", false], ["missing", "one", true], ["one", "missing", true]]) {
    assert.equal(reorderedSpine(spine, source, target, after), spine);
  }
  assert.deepEqual(spine, ["one", "two", "three"]);
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

test("preview selection uses media types and rejects stale chapters and navigation pages", () => {
  const files = [
    { path: "cover.resource", media_type: "image/svg+xml" },
    { path: "chapter.xhtml", media_type: "application/xhtml+xml" },
    { path: "nav.xhtml", media_type: "application/xhtml+xml" },
    { path: "book.css", media_type: "text/css" },
    { path: "notes.xml", media_type: "application/xml" },
  ];
  const spine = ["nav.xhtml", "notes.xml", "cover.resource", "chapter.xhtml"];
  const pick = (path, preferred) => resourcePreviewPath(path, files, spine, "nav.xhtml", preferred);
  assert.equal(pick("cover.resource"), "cover.resource");
  assert.equal(pick("chapter.xhtml"), "chapter.xhtml");
  for (const preferred of ["previous-book.xhtml", "nav.xhtml", "notes.xml", ""]) assert.equal(pick("book.css", preferred), "cover.resource");
  assert.equal(pick("book.css", "chapter.xhtml"), "chapter.xhtml");
  for (const path of ["nav.xhtml", "notes.xml", "missing"]) assert.equal(pick(path, "chapter.xhtml"), "");
  assert.equal(resourcePreviewPath("book.css", files, [], "nav.xhtml"), "");
});

test("binary resource mutations reload the active source and editable mutations choose a valid file", () => {
  const files = [{ path: "one.xhtml", editable: true }, { path: "two.xhtml", editable: true }, { path: "new.png", editable: false }];
  for (const action of ["rename", "replace", "delete", "add"]) assert.equal(resourceAfterMutation(action, "one.xhtml", "old.png", "new.png", [], files), "one.xhtml");
  assert.equal(resourceAfterMutation("rename", "old.xhtml", "old.xhtml", "two.xhtml", [], files), "two.xhtml");
  assert.equal(resourceAfterMutation("delete", "deleted.xhtml", "deleted.xhtml", "", ["one.xhtml", "deleted.xhtml", "two.xhtml"], files), "one.xhtml");
  assert.equal(resourceAfterMutation("add", "one.xhtml", "", "two.xhtml", [], files), "two.xhtml");
  assert.equal(resourceAfterMutation("delete", "deleted.xhtml", "deleted.xhtml", "", [], []), "");
});
