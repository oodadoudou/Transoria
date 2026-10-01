import assert from "node:assert/strict";
import test from "node:test";
import { SearchRequests } from "../src/pages/general-tools/epubSearchRequests.ts";

test("changed query/options discard stale results and delayed requests", () => {
  const requests = new SearchRequests();
  const first = requests.invalidate();
  requests.enqueue(first);
  assert.equal(requests.take(), first);
  const latest = requests.invalidate();
  assert.equal(requests.isCurrent(first), false);
  assert.equal(requests.enqueue(first), false);
  assert.equal(requests.enqueue(latest), true);
  assert.equal(requests.take(), latest);
  assert.equal(requests.take(), null);
});

test("the latest scope waits for the mutation lock without losing the request", () => {
  const requests = new SearchRequests();
  const oldScope = requests.invalidate();
  requests.enqueue(oldScope);
  const latestScope = requests.invalidate();
  requests.enqueue(latestScope);
  assert.equal(requests.take(), latestScope);
  assert.equal(requests.isCurrent(latestScope), true);
});

test("manual search, empty query, closed bar and switched books cancel pending work", () => {
  for (const action of ["manual", "empty", "closed", "new book"]) {
    const requests = new SearchRequests();
    const automatic = requests.invalidate();
    requests.enqueue(automatic);
    const revision = requests.invalidate();
    assert.equal(requests.take(), null, action);
    assert.equal(requests.enqueue(automatic), false, action);
    assert.equal(requests.isCurrent(revision), true, action);
  }
});

test("an in-flight response cannot publish after a newer configuration", async () => {
  const requests = new SearchRequests();
  const first = requests.invalidate();
  let finish;
  const response = new Promise((resolve) => { finish = resolve; });
  const published = [];
  const operation = response.then(() => {
    if (requests.isCurrent(first)) published.push("old scope");
  });
  const latest = requests.invalidate();
  requests.enqueue(latest);
  finish();
  await operation;
  assert.deepEqual(published, []);
  assert.equal(requests.take(), latest);
});
