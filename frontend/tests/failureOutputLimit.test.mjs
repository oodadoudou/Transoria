import assert from 'node:assert/strict';
import test from 'node:test';
import { zh } from '../src/locales/zh.ts';
import { en } from '../src/locales/en.ts';

test('output-truncated failures name the settings that actually shrink replies', () => {
  for (const messages of [zh, en]) {
    const hint = messages.failedSubtasksModal.diagnosis.recommendations.outputLimit;
    assert.ok(hint.includes(messages.glossaryReview.settings.batchSize), 'batch size label');
    assert.ok(hint.includes(messages.model.inputTokenLimit), 'chunk input limit label');
    assert.ok(hint.includes(messages.model.outputTokenLimit), 'output token limit label');
  }
  assert.equal(zh.failedSubtasksModal.failureTypes.outputLimit, '输出被截断');
});
