import assert from 'node:assert/strict';
import test from 'node:test';
import { zh } from '../src/locales/zh.ts';
import { en } from '../src/locales/en.ts';

test('thinking-off errors distinguish unsupported disabling from an ignored setting', () => {
  assert.match(zh.modelExtra.testThinkingOffUnsupported, /不支持关闭思考模式/);
  assert.match(zh.modelExtra.testThinkingOffUnsupported, /请开启思考/);
  assert.match(zh.modelExtra.testThinkingOffIgnored, /未遵守关闭思考设置/);
  assert.match(en.modelExtra.testThinkingOffUnsupported, /does not support disabling thinking/);
  assert.match(en.modelExtra.testThinkingOffUnsupported, /Enable thinking/);
  assert.match(en.modelExtra.testThinkingOffIgnored, /did not honor Off/);
  for (const messages of [zh.modelExtra, en.modelExtra]) {
    assert.notEqual(messages.testThinkingOffUnsupported, messages.testThinkingOffIgnored);
  }
});
