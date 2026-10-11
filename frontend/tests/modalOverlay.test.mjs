import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const root = fileURLToPath(new URL('../src/', import.meta.url));

test('modal portal preserves content, accessibility, styling and click handlers at the document root', () => {
  const source = readFileSync(path.join(root, 'components/ModalOverlay.tsx'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const body = {};
  const exports = {};
  vm.runInNewContext(compiled, { exports, document: { body }, require: name => {
    if (name === 'react-dom') return { createPortal: (node, target) => ({ node, target }) };
    if (name === 'react/jsx-runtime') return { jsx: (tag, props) => ({ tag, props }) };
    if (name.endsWith('.css')) return { default: { viewport: 'viewport' } };
    throw new Error(`Unexpected import: ${name}`);
  } });
  const onClick = () => undefined;
  const children = {};
  const portal = exports.ModalOverlay({ className: 'request-overlay', role: 'dialog', 'aria-modal': true, onClick, children });
  assert.equal(portal.target, body);
  assert.equal(portal.node.props.className, 'request-overlay viewport');
  assert.equal(portal.node.props.role, 'dialog');
  assert.equal(portal.node.props['aria-modal'], true);
  assert.equal(portal.node.props.onClick, onClick);
  assert.equal(portal.node.props.children, children);
});

test('fullscreen modal backdrops never return to clipped page or sticky-dock containers', () => {
  let count = 0;
  function visitFolder(folder) {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const file = path.join(folder, entry.name);
      if (entry.isDirectory()) { visitFolder(file); continue; }
      if (!file.endsWith('.tsx')) continue;
      const source = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      function visit(node) {
        if (ts.isJsxOpeningElement(node) && node.attributes.properties.some(a =>
          ts.isJsxAttribute(a) && a.name.getText(source) === 'className' && a.initializer &&
          /^\{\w+\.(overlay|dialogOverlay|modalBackdrop)\}$/.test(a.initializer.getText(source)))) {
          assert.equal(node.tagName.getText(source), 'ModalOverlay', file);
          count++;
        }
        ts.forEachChild(node, visit);
      }
      visit(source);
    }
  }
  visitFolder(root);
  assert.ok(count >= 30, 'All shared and editor modal backdrops are covered');
});
