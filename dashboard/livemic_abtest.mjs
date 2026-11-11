import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';

const wavPath = process.argv[2];
const label = process.argv[3] || wavPath;

const buf = readFileSync(wavPath);
let sr = 16000, fmtOff = 20, dataOff = 0, dataLen = 0;
let off = 12;
while (off + 8 <= buf.length) {
  const id = buf.toString('ascii', off, off + 4);
  const sz = buf.readUInt32LE(off + 4);
  if (id === 'fmt ') {
    fmtOff = off + 8;
    sr = buf.readUInt16LE(fmtOff + 4);
  } else if (id === 'data') {
    dataOff = off + 8;
    dataLen = sz;
  }
  off += 8 + sz + (sz & 1);
  if (id === 'data') break;
}
const n = Math.floor(dataLen / 2);
const pcm = new Float32Array(n);
for (let i = 0; i < n; i++) pcm[i] = buf.readInt16LE(dataOff + i * 2) / 32768.0;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

const pcmArr = Array.from(pcm);
await page.addInitScript(({ sr, pcmArr: src }) => {
  window.__raw = [];
  window.__liveCallId = null;
  const origSend = window.WebSocket.prototype.send;
  window.WebSocket.prototype.send = function (data) {
    if (this.url.includes('/live') && typeof data === 'string') {
      try {
        const o = JSON.parse(data);
        if (o && o.start && o.call_id) window.__liveCallId = o.call_id;
      } catch { /* ignore */ }
    }
    return origSend.call(this, data);
  };
  const origDesc = Object.getOwnPropertyDescriptor(window.WebSocket.prototype, 'onmessage') || { configurable: true };
  Object.defineProperty(window.WebSocket.prototype, 'onmessage', {
    configurable: true,
    get() { return this.__onmsg; },
    set(fn) {
      this.__onmsg = fn;
      if (typeof fn === 'function') {
        const self = this;
        const wrapped = (e) => {
          try {
            const d = JSON.parse(e.data);
            window.__raw.push({ url: self.url, d });
          } catch { /* non-JSON frame */ }
          return fn.call(self, e);
        };
        self.addEventListener('message', wrapped);
      }
    },
  });
  const pcm = new Float32Array(src);
  const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    if (c && c.audio === false) return nativeGetUserMedia(c);
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    await ctx.resume();
    const len48 = Math.round(pcm.length * 48000 / sr);
    const ab = ctx.createBuffer(1, len48, 48000);
    const ch = ab.getChannelData(0);
    for (let i = 0; i < len48; i++) {
      const pos = i * sr / 48000;
      const f = Math.floor(pos);
      const frac = pos - f;
      const a = pcm[Math.min(f, pcm.length - 1)];
      const b = pcm[Math.min(f + 1, pcm.length - 1)];
      ch[i] = a + (b - a) * frac;
    }
    const srcNode = ctx.createBufferSource();
    srcNode.buffer = ab;
    srcNode.loop = true;
    const dest = ctx.createMediaStreamDestination();
    srcNode.connect(dest);
    srcNode.start(0, 0, Math.ceil(len48 / 48000) + 1);
    return dest.stream;
  };
  window.__holdsmsgs = [];
}, { sr, pcmArr });

await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') console.log('PAGE', m.type(), m.text()); });
page.on('pageerror', (e) => console.log('PAGEERROR', String(e)));
await page.waitForTimeout(1500);
console.log('mock?', await page.evaluate(() => String(navigator.mediaDevices.getUserMedia).includes('createBufferSource')));

const startBtn = page.locator('.live-control button').first();
await startBtn.waitFor({ state: 'visible', timeout: 15000 });
await page.waitForFunction(() => {
  const b = document.querySelector('.live-control button');
  return b && !b.disabled && b.textContent.includes('Start live recording');
}, { timeout: 15000 });
await startBtn.click();
const started = await page.locator('.live-control button').first().textContent();
console.log('started:', started.trim());

const clipSecs = Math.ceil(n / sr) + 2;
const durMs = (clipSecs + 2) * 1000;
const t0 = Date.now();
await page.waitForTimeout(durMs);
await page.waitForTimeout(800);

const stopBtn = page.locator('.live-control button').first();
await stopBtn.click();
await page.waitForTimeout(1200);

const scored = await page.evaluate(() => window.__raw.filter((r) => (r.url.includes('/live') || r.url.endsWith('/ws')) && typeof r.d?.score === 'number' && r.d.call_id === (window.__liveCallId || '')));
scored.forEach((r) => {
  console.log(`[${label}] t=${r.d.t} score=${r.d.score} state=${r.d.state} flags=${JSON.stringify(r.d.context_flags)}`);
});
console.log(`[${label}] raw ws msgs=${await page.evaluate(() => window.__raw.length)}`);
await browser.close();