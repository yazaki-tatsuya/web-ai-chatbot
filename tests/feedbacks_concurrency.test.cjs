const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'feedbacks.html'), 'utf8'
);
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function createButton(sid) {
  return {
    disabled: false,
    getAttribute(name) { return name === 'data-sid' ? sid : null; },
    addEventListener(type, handler) { this[type] = handler; },
  };
}

function createHarness() {
  const status = { textContent: '' };
  const rows = {
    first: { regen: createButton('first'), del: createButton('first') },
    second: { regen: createButton('second'), del: createButton('second') },
  };
  const pending = [];
  const requests = [];
  let reloads = 0;
  const context = vm.createContext({
    document: {
      getElementById() { return status; },
      querySelectorAll(selector) {
        if (selector === '.btn-regen') return [rows.first.regen, rows.second.regen];
        if (selector === '.btn-del') return [rows.first.del, rows.second.del];
        return [];
      },
      querySelector(selector) {
        const match = selector.match(/\.feedback-actions\[data-sid="([^"]+)"\]/);
        const row = match && rows[match[1]];
        return row ? { querySelectorAll() { return [row.regen, row.del]; } } : null;
      },
    },
    window: { confirm() { return true; } },
    location: { reload() { reloads++; } },
    fetch(url, options) {
      requests.push({ url, options });
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
  });
  vm.runInContext(script, context);
  return {
    rows, status, pending, requests,
    get reloads() { return reloads; },
  };
}

test('regeneration locks both actions for the same row but leaves other rows enabled', async () => {
  const h = createHarness();
  const regenerating = h.rows.first.regen.click();
  assert.equal(h.rows.first.regen.disabled, true);
  assert.equal(h.rows.first.del.disabled, true);
  assert.equal(h.rows.second.regen.disabled, false);
  assert.equal(h.rows.second.del.disabled, false);
  assert.match(h.status.textContent, /再生成中/);
  await h.rows.first.del.click();
  await h.rows.first.regen.click();
  assert.equal(h.requests.length, 1);
  h.pending[0].resolve({ ok: true, status: 200, async json() { return { ok: true }; } });
  await regenerating;
  assert.equal(h.reloads, 1);
  assert.equal(h.rows.first.regen.disabled, true);
  assert.equal(h.rows.first.del.disabled, true);
});

test('failed regeneration unlocks both actions for retry', async () => {
  const h = createHarness();
  const regenerating = h.rows.first.regen.click();
  h.pending[0].resolve({ ok: false, status: 502, async json() {
    return { ok: false, error: '一時的な生成失敗' };
  } });
  await regenerating;
  assert.equal(h.reloads, 0);
  assert.equal(h.rows.first.regen.disabled, false);
  assert.equal(h.rows.first.del.disabled, false);
  assert.match(h.status.textContent, /再試行できます/);
  const retry = h.rows.first.regen.click();
  assert.equal(h.requests.length, 2);
  h.pending[1].resolve({ ok: true, status: 200, async json() { return { ok: true }; } });
  await retry;
  assert.equal(h.reloads, 1);
});

test('deletion locks regeneration for the same row until failure or success', async () => {
  const h = createHarness();
  const deleting = h.rows.first.del.click();
  assert.equal(h.rows.first.regen.disabled, true);
  assert.equal(h.rows.first.del.disabled, true);
  assert.equal(h.rows.second.regen.disabled, false);
  await h.rows.first.regen.click();
  assert.equal(h.requests.length, 1);
  h.pending[0].reject(new Error('Network unavailable'));
  await deleting;
  assert.equal(h.rows.first.regen.disabled, false);
  assert.equal(h.rows.first.del.disabled, false);
  assert.match(h.status.textContent, /通信エラー/);
});

test('different rows can be processed independently', async () => {
  const h = createHarness();
  const first = h.rows.first.regen.click();
  const second = h.rows.second.regen.click();
  assert.equal(h.requests.length, 2);
  assert.equal(h.rows.first.del.disabled, true);
  assert.equal(h.rows.second.del.disabled, true);
  h.pending[0].resolve({ ok: false, status: 502, async json() { return { ok: false }; } });
  h.pending[1].resolve({ ok: false, status: 502, async json() { return { ok: false }; } });
  await Promise.all([first, second]);
  assert.equal(h.rows.first.regen.disabled, false);
  assert.equal(h.rows.first.del.disabled, false);
  assert.equal(h.rows.second.regen.disabled, false);
  assert.equal(h.rows.second.del.disabled, false);
});