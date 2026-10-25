const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function createHarness(template) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'templates', template), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const status = { textContent: '' };
  const button = {
    disabled: false,
    addEventListener(type, handler) { this[type] = handler; },
    getAttribute() { return 'test-session'; },
  };
  let failure = true;
  let requests = 0;
  let reloads = 0;
  const context = vm.createContext({
    document: {
      getElementById(id) { return id === 'btn-generate-feedback' ? button : status; },
      querySelectorAll(selector) { return selector === '.btn-regen' ? [button] : []; },
    },
    location: { reload() { reloads++; } },
    async fetch(url, options) {
      requests++;
      assert.match(url, /\/feedback\/generate$/);
      assert.equal(options.method, 'POST');
      return {
        ok: !failure,
        status: failure ? 502 : 200,
        async json() {
          return failure ? { ok: false, error: '生成に失敗しました' } : { ok: true };
        },
      };
    },
  });
  vm.runInContext(script, context);
  return {
    button, status,
    get requests() { return requests; },
    get reloads() { return reloads; },
    allowSuccess() { failure = false; },
  };
}

for (const template of ['feedback.html', 'feedbacks.html']) {
  test(`${template}: generation failure displays retry guidance without reloading`, async () => {
    const h = createHarness(template);
    await h.button.click();
    assert.equal(h.requests, 1);
    assert.equal(h.reloads, 0);
    assert.equal(h.button.disabled, false);
    assert.match(h.status.textContent, /生成に失敗しました/);
    assert.match(h.status.textContent, /再試行できます/);
    h.allowSuccess();
    await h.button.click();
    assert.equal(h.requests, 2);
    assert.equal(h.reloads, 1);
    assert.match(h.status.textContent, /生成完了/);
    assert.doesNotMatch(h.status.textContent, /失敗|再試行/);
  });
}