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

function createHarness({ approved = true, failure = null, sid = 'test-session' } = {}) {
  const status = { textContent: '' };
  const regenButton = createButton(sid);
  const button = createButton(sid);
  const otherRegenButton = createButton('other-session');
  const otherDeleteButton = createButton('other-session');
  const actionGroups = {
    [sid]: [regenButton, button],
    'other-session': [otherRegenButton, otherDeleteButton],
  };
  const confirmations = [];
  const requests = [];
  let reloads = 0;
  let releaseRequest;
  const context = vm.createContext({
    document: {
      getElementById() { return status; },
      querySelectorAll(selector) {
        if (selector === '.btn-regen') return [regenButton, otherRegenButton];
        if (selector === '.btn-del') return [button, otherDeleteButton];
        return [];
      },
      querySelector(selector) {
        const match = selector.match(/\.feedback-actions\[data-sid="([^"]+)"\]/);
        const buttons = match && actionGroups[match[1]];
        return buttons ? { querySelectorAll() { return buttons; } } : null;
      },
    },
    window: {
      confirm(message) {
        confirmations.push(message);
        return approved;
      },
    },
    location: { reload() { reloads++; } },
    async fetch(url, options) {
      requests.push({ url, options });
      if (failure === 'pending') {
        await new Promise(resolve => { releaseRequest = resolve; });
      }
      if (failure === 'network') throw new Error('Network unavailable');
      return {
        ok: failure !== 'http',
        status: failure === 'http' ? 500 : 200,
        async json() { return { ok: failure !== 'http' && failure !== 'api' }; },
      };
    },
  });
  // Execute the actual template script so the tests cover production event handlers.
  vm.runInContext(script, context);
  return {
    button, regenButton, otherRegenButton, otherDeleteButton,
    status, confirmations, requests,
    get reloads() { return reloads; },
    setFailure(value) { failure = value; },
    releaseRequest() { releaseRequest(); },
  };
}

test('cancelling deletion sends no request and leaves the page unchanged', async () => {
  const h = createHarness({ approved: false });
  await h.button.click();
  assert.equal(h.confirmations.length, 1);
  assert.match(h.confirmations[0], /削除しますか/);
  assert.match(h.confirmations[0], /会話ログは削除されません/);
  assert.equal(h.requests.length, 0);
  assert.equal(h.button.disabled, false);
  assert.equal(h.status.textContent, '');
  assert.equal(h.reloads, 0);
});

test('approved deletion posts to the delete endpoint and reloads after success', async () => {
  const h = createHarness();
  await h.button.click();
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/session/test-session/feedback/delete');
  assert.equal(h.requests[0].options.method, 'POST');
  assert.equal(h.button.disabled, true);
  assert.equal(h.regenButton.disabled, true);
  assert.equal(h.otherRegenButton.disabled, false);
  assert.equal(h.otherDeleteButton.disabled, false);
  assert.match(h.status.textContent, /削除完了/);
  assert.equal(h.reloads, 1);
});

test('repeated clicks during deletion do not prompt or send another request', async () => {
  const h = createHarness({ failure: 'pending' });
  const deleting = h.button.click();
  assert.equal(h.button.disabled, true);
  assert.equal(h.regenButton.disabled, true);
  assert.match(h.status.textContent, /削除中/);
  await h.button.click();
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.requests.length, 1);
  h.releaseRequest();
  await deleting;
  assert.equal(h.reloads, 1);
});

for (const failure of ['http', 'network', 'api']) {
  test(`permits retry with confirmation after ${failure} deletion failure`, async () => {
    const h = createHarness({ failure });
    await h.button.click();
    assert.equal(h.button.disabled, false);
    assert.equal(h.regenButton.disabled, false);
    assert.match(h.status.textContent, /削除に失敗|通信エラー/);
    assert.equal(h.reloads, 0);
    h.setFailure(null);
    await h.button.click();
    assert.equal(h.confirmations.length, 2);
    assert.equal(h.requests.length, 2);
    assert.equal(h.reloads, 1);
  });
}

test('missing session ID does not prompt or send a delete request', async () => {
  const h = createHarness({ sid: '' });
  await h.button.click();
  assert.equal(h.confirmations.length, 0);
  assert.equal(h.requests.length, 0);
  assert.equal(h.reloads, 0);
});