import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSavedSearches } from '../src/pages/general-tools/epubSearchLibrary.ts';

const search = { id: 'one', name: 'Example', query: '(Hello)', replacement: '$1', scope: 'text', caseSensitive: true, regularExpression: true };
test('saved searches retain order, scope and replacement options', () => {
  assert.deepEqual(parseSavedSearches(JSON.stringify([search, { ...search, id: 'two', scope: 'styles' }])), [search, { ...search, id: 'two', scope: 'styles' }]);
});
test('saved searches reject corrupt storage and duplicate identifiers', () => {
  for (const value of ['broken', 'null', '{}']) assert.deepEqual(parseSavedSearches(value), []);
  assert.deepEqual(parseSavedSearches(JSON.stringify([search, search, { ...search, id: 'bad', query: '' }, { ...search, id: 'invalid', scope: 'remote' }])), [search]);
});
