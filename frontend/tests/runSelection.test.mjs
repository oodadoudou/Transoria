import assert from "node:assert/strict";
import test from "node:test";

import { applyRunSelection } from "../src/store/runSelection.ts";

test("rejected selections report the operation error to the run page", async () => {
  const error = { code: "bridge.invalid_argument" };
  const reported = [];
  const result = await applyRunSelection(async () => null, () => error, (value) => reported.push(value));
  assert.equal(result, false);
  assert.deepEqual(reported, [error]);
});

test("successful selections clear earlier operation errors", async () => {
  const reported = [];
  const result = await applyRunSelection(async () => ({ model: "new" }), () => assert.fail("unused"), (value) => reported.push(value));
  assert.equal(result, true);
  assert.deepEqual(reported, [null]);
});
