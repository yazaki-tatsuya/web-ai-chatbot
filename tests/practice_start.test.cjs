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
  const elements = {
    'start-btn': button,
    'end-btn': { addEventListener() {} },
    chat: {},
    status: { appendChild() {} },
  };
  const peers = [];
  const tracks = [];
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
    setTimeout,
    clearTimeout,
    RTCPeerConnection: PeerConnection,
    document: {
      getElementById(id) { return elements[id]; },
      createElement() { return {}; },
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
    async fetch() {
      fetchCalls++;
      if (failure === 'network') throw new Error('Network unavailable');
      return { ok: failure !== 'http', status: 500, async text() { return 'answer'; } };
    },
  });
  // Compile and execute the actual template script, not a copy of the start logic.
  vm.runInContext(script, context);
  return {
    button, peers, tracks,
    setFailure(value) { failure = value; },
    releaseMedia() { resolveMedia(); },
    start() { return vm.runInContext('startRealtime()', context); },
    get mediaCalls() { return mediaCalls; },
    get fetchCalls() { return fetchCalls; },
  };
}

test('suppresses repeated starts while microphone permission is pending', async () => {
  const h = createHarness();
  h.setFailure('pending');
  const first = h.start();
  assert.equal(h.button.disabled, true);
  assert.equal(h.button.textContent, '接続中…');
  await h.start();
  assert.equal(h.peers.length, 1);
  assert.equal(h.mediaCalls, 1);
  h.releaseMedia();
  await first;
  assert.equal(h.fetchCalls, 1);
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
    h.setFailure(null);
    await h.start();
    assert.equal(h.peers.length, 2);
    assert.equal(h.button.disabled, true);
    assert.equal(h.button.textContent, '接続済み');
    assert.equal(h.peers[1].closed, false);
  });
}