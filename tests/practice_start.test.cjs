const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'practice.html'), 'utf8'
);
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function createHarness() {
  const button = { disabled: false, textContent: 'Realtime開始', addEventListener() {} };
  const devlogMarkup = html.match(/<button\b[^>]*id="toggle-devlog"[^>]*>([^<]*)<\/button>/)[0];
  const devlogButton = {
    textContent: devlogMarkup.match(/>([^<]*)<\/button>/)[1],
    attributes: { 'aria-expanded': devlogMarkup.match(/aria-expanded="([^"]*)"/)[1] },
    addEventListener(type, handler) { this[type] = handler; },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  const status = {
    hidden: /<div\b[^>]*id="status"[^>]*\bhidden\b/.test(html),
    children: [], scrollTop: 0, scrollHeight: 100,
    appendChild(child) { this.children.push(child); },
  };
  const operationMarkup = html.match(/<div\b[^>]*id="operation-status"[^>]*>/)[0];
  const operationStatus = {
    hidden: /\bhidden\b/.test(operationMarkup),
    textContent: '',
    className: operationMarkup.match(/class="([^"]*)"/)[1],
  };
  const elements = {
    'operation-status': operationStatus,
    'start-btn': button,
    'toggle-devlog': devlogButton,
    'end-btn': { disabled: false, textContent: '終了→保存', addEventListener() {} },
    chat: {},
    status,
  };
  const peers = [];
  const tracks = [];
  const audioElements = [];
  const timers = new Map();
  const saves = [];
  const location = { href: '' };
  let nextTimer = 1;
  let saveFailure = null;
  let resolveSave;
  let mediaCalls = 0;
  let fetchCalls = 0;
  let failure = null;
  let resolveMedia;

  class PeerConnection {
    constructor() {
      this.senders = [];
      this.closed = false;
      this.channel = { closed: false, close() { this.closed = true; } };
      peers.push(this);
    }
    createDataChannel() { return this.channel; }
    addTrack(track) { this.senders.push({ track }); }
    getSenders() { return this.senders; }
    async createOffer() { return { sdp: 'offer' }; }
    async setLocalDescription() {}
    async setRemoteDescription() {
      if (failure === 'answer') throw new Error('Invalid SDP answer');
    }
    close() { this.closed = true; }
  }

  const context = vm.createContext({
    console,
    Map,
    Date,
    setTimeout(callback) {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    location,
    RTCPeerConnection: PeerConnection,
    document: {
      getElementById(id) { return elements[id]; },
      createElement(tag) {
        if (tag !== 'audio') return {};
        const audio = {
          paused: false, removed: false,
          pause() { this.paused = true; },
          remove() { this.removed = true; },
        };
        audioElements.push(audio);
        return audio;
      },
      body: { appendChild() {} },
    },
    navigator: {
      mediaDevices: {
        async getUserMedia() {
          mediaCalls++;
          if (failure === 'microphone') throw new Error('Microphone denied');
          if (failure === 'pending') {
            await new Promise(resolve => { resolveMedia = resolve; });
          }
          const track = { stopped: false, stop() { this.stopped = true; } };
          tracks.push(track);
          return { getTracks: () => [track] };
        },
      },
    },
    async fetch(url, options) {
      if (url.endsWith('/transcript')) {
        saves.push(JSON.parse(options.body));
        if (saveFailure === 'pending') {
          await new Promise(resolve => { resolveSave = resolve; });
        }
        if (saveFailure === 'network') throw new Error('Save network failure');
        return { ok: saveFailure !== 'http', status: 500 };
      }
      fetchCalls++;
      if (failure === 'network') throw new Error('Network unavailable');
      return { ok: failure !== 'http', status: 500, async text() { return 'answer'; } };
    },
  });
  // Compile and execute the actual template script, not a copy of the start logic.
  vm.runInContext(script, context);
  return {
    button, peers, tracks, audioElements, saves, location, devlogButton, status, operationStatus,
    appendStatus(text) {
      context.statusMessage = text;
      return vm.runInContext('appendStatus(statusMessage)', context);
    },
    endButton: elements['end-btn'],
    setSaveFailure(value) { saveFailure = value; },
    releaseSave() { resolveSave(); },
    end() { return vm.runInContext('endAndSave()', context); },
    scheduleResponse() { vm.runInContext('scheduleResponseCreate()', context); },
    get timerCount() { return timers.size; },
    get resourcesReleased() {
      return vm.runInContext(
        'pc === null && dataChannel === null && localStream === null && !responseInFlight',
        context
      );
    },
    setFailure(value) { failure = value; },
    releaseMedia() { resolveMedia(); },
    start() { return vm.runInContext('startRealtime()', context); },
    get mediaCalls() { return mediaCalls; },
    get fetchCalls() { return fetchCalls; },
  };
}

test('development log starts hidden with matching button state', () => {
  const h = createHarness();
  assert.equal(h.status.hidden, true);
  assert.equal(h.devlogButton.attributes['aria-expanded'], 'false');
  assert.equal(h.devlogButton.textContent, '開発ログを表示');
  assert.match(html, /aria-controls="status"/);
});

test('user-facing status starts hidden and has an accessible live region', () => {
  const h = createHarness();
  assert.equal(h.operationStatus.hidden, true);
  assert.match(html, /id="operation-status"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"/);
});

test('development log button toggles visibility, label and expanded state', () => {
  const h = createHarness();
  h.devlogButton.click();
  assert.equal(h.status.hidden, false);
  assert.equal(h.devlogButton.attributes['aria-expanded'], 'true');
  assert.equal(h.devlogButton.textContent, '開発ログを隠す');
  assert.equal(h.status.scrollTop, h.status.scrollHeight);
  h.devlogButton.click();
  assert.equal(h.status.hidden, true);
  assert.equal(h.devlogButton.attributes['aria-expanded'], 'false');
  assert.equal(h.devlogButton.textContent, '開発ログを表示');
});

test('development log retains messages recorded while hidden', async () => {
  const h = createHarness();
  await h.appendStatus('非表示中のログ');
  assert.equal(h.status.hidden, true);
  h.devlogButton.click();
  assert.equal(h.status.children[0].textContent, '> 非表示中のログ');
  h.devlogButton.click();
  await h.appendStatus('追加ログ');
  h.devlogButton.click();
  assert.equal(h.status.children.length, 2);
  assert.equal(h.status.children[1].textContent, '> 追加ログ');
});

test('suppresses repeated starts while microphone permission is pending', async () => {
  const h = createHarness();
  h.setFailure('pending');
  const first = h.start();
  assert.equal(h.button.disabled, true);
  assert.equal(h.button.textContent, '接続中…');
  assert.equal(h.operationStatus.hidden, false);
  assert.match(h.operationStatus.textContent, /接続中/);
  await h.start();
  assert.equal(h.peers.length, 1);
  assert.equal(h.mediaCalls, 1);
  h.releaseMedia();
  await first;
  assert.equal(h.fetchCalls, 1);
  assert.match(h.operationStatus.textContent, /接続しました/);
});

test('keeps the start button disabled and prevents another connection after success', async () => {
  const h = createHarness();
  await h.start();
  await h.start();
  assert.equal(h.button.disabled, true);
  assert.equal(h.button.textContent, '接続済み');
  assert.equal(h.peers.length, 1);
  assert.equal(h.fetchCalls, 1);
});

for (const failure of ['microphone', 'network', 'http', 'answer']) {
  test(`cleans up and permits retry after ${failure} failure`, async () => {
    const h = createHarness();
    h.setFailure(failure);
    await h.start();
    assert.equal(h.button.disabled, false);
    assert.equal(h.button.textContent, 'Realtime開始');
    assert.equal(h.peers[0].closed, true);
    assert.equal(h.peers[0].channel.closed, true);
    assert.ok(h.tracks.every(track => track.stopped));
    assert.equal(h.status.hidden, true);
    assert.equal(h.operationStatus.hidden, false);
    assert.match(h.operationStatus.className, /alert-danger/);
    assert.match(h.operationStatus.textContent, /接続に失敗しました/);
    assert.match(h.operationStatus.textContent, /再試行/);
    h.setFailure(null);
    await h.start();
    assert.equal(h.peers.length, 2);
    assert.equal(h.button.disabled, true);
    assert.equal(h.button.textContent, '接続済み');
    assert.equal(h.peers[1].closed, false);
    assert.match(h.operationStatus.textContent, /接続しました/);
    assert.doesNotMatch(h.operationStatus.className, /alert-danger/);
  });
}

test('releases microphone, audio and timer before save completes; prevents duplicate save', async () => {
  const h = createHarness();
  await h.start();
  h.peers[0].ontrack({ streams: [{}] });
  h.scheduleResponse();
  assert.equal(h.timerCount, 1);
  h.setSaveFailure('pending');
  const saving = h.end();
  assert.ok(h.resourcesReleased);
  assert.ok(h.tracks.every(track => track.stopped));
  assert.equal(h.peers[0].closed, true);
  assert.equal(h.peers[0].channel.closed, true);
  assert.equal(h.peers[0].ontrack, null);
  assert.equal(h.peers[0].channel.onmessage, null);
  assert.equal(h.audioElements[0].paused, true);
  assert.equal(h.audioElements[0].srcObject, null);
  assert.equal(h.audioElements[0].removed, true);
  assert.equal(h.timerCount, 0);
  assert.equal(h.endButton.disabled, true);
  assert.match(h.operationStatus.textContent, /保存しています/);
  assert.doesNotMatch(h.operationStatus.className, /alert-danger/);
  await h.end();
  await h.start();
  h.scheduleResponse();
  assert.equal(h.saves.length, 1);
  assert.equal(h.peers.length, 1);
  assert.equal(h.timerCount, 0);
  h.releaseSave();
  await saving;
  assert.ok(h.location.href.startsWith('/feedback/'));
});

for (const failure of ['http', 'network']) {
  test(`permits saving again without restarting audio after ${failure} save failure`, async () => {
    const h = createHarness();
    await h.start();
    h.setSaveFailure(failure);
    await h.end();
    assert.ok(h.resourcesReleased);
    assert.ok(h.tracks.every(track => track.stopped));
    assert.equal(h.button.textContent, '終了済み');
    assert.equal(h.button.disabled, true);
    assert.equal(h.endButton.disabled, false);
    assert.equal(h.endButton.textContent, '保存を再試行');
    assert.equal(h.location.href, '');
    assert.equal(h.status.hidden, true);
    assert.equal(h.operationStatus.hidden, false);
    assert.match(h.operationStatus.className, /alert-danger/);
    assert.match(h.operationStatus.textContent, /保存に失敗しました/);
    assert.match(h.operationStatus.textContent, /保存を再試行/);
    h.setSaveFailure(null);
    await h.end();
    assert.equal(h.saves.length, 2);
    assert.deepEqual(h.saves[0].transcript, h.saves[1].transcript);
    assert.equal(h.peers.length, 1);
    assert.ok(h.location.href.startsWith('/feedback/'));
    assert.match(h.operationStatus.textContent, /保存しました/);
    assert.doesNotMatch(h.operationStatus.className, /alert-danger/);
  });
}

test('stops a microphone acquired after ending during the permission prompt', async () => {
  const h = createHarness();
  h.setFailure('pending');
  const starting = h.start();
  await h.end();
  h.releaseMedia();
  await starting;
  assert.ok(h.resourcesReleased);
  assert.ok(h.tracks.every(track => track.stopped));
  assert.equal(h.fetchCalls, 0);
  assert.equal(h.button.textContent, '終了済み');
  assert.equal(h.button.disabled, true);
});

test('can end and save before any connection is started', async () => {
  const h = createHarness();
  await h.end();
  assert.ok(h.resourcesReleased);
  assert.equal(h.saves.length, 1);
  assert.equal(h.peers.length, 0);
  await h.start();
  assert.equal(h.peers.length, 0);
});
