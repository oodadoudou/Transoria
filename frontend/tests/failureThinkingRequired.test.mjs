import assert from 'node:assert/strict';
import test from 'node:test';
import { zh } from '../src/locales/zh.ts';
import { en } from '../src/locales/en.ts';

test('thinking-required failures tell users which setting to change', () => {
  for (const messages of [zh, en]) {
    const hint = messages.failedSubtasksModal.diagnosis.recommendations.thinkingRequired;
    for (const label of ['Low', 'Medium', 'High']) assert.ok(hint.includes(label));
  }
});
