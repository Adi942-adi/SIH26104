import { chromium } from 'playwright';

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' });
const title = await page.title();
const liveControl = await page.locator('.live-control').count();
const codecText = await page.locator('header').innerText();
const waveText = await page.locator('.signal-column .waveform-text, .signal-column section span').first().innerText().catch(() => '');
const body = await page.locator('body').innerText();

console.log('title:', title);
console.log('.live-control count:', liveControl);
console.log('header has 16kHz label:', body.toLowerCase().includes('16 khz wideband pcm'));
console.log('waveform has live-ingest label:', body.toLowerCase().includes('live ingest stream'));
console.log('approval panel removed:', !/(executive transfer|40,00,000|northstar|arjun mehta|payment awaiting)/i.test(body));
console.log('errors:', errors.length ? errors : 'none');
await browser.close();