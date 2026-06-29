#!/usr/bin/env node
// Snapshot specific times of a scene to PNGs for review.
// Usage: node snap.js [sceneId=act1] t1 t2 t3 ...
const { chromium } = require('playwright-core');
const path = require('path'); const fs = require('fs');
const ROOT = path.join(__dirname, '..'); const BUILD = path.join(ROOT, 'build');
const PLAYER = 'file://' + path.join(ROOT, 'player.html');
function findChromium() {
  const base = path.join(process.env.HOME, 'Library/Caches/ms-playwright');
  try { const d = fs.readdirSync(base).filter(x => x.startsWith('chromium-')).sort();
    if (d.length) { const p = path.join(base, d[d.length-1], 'chrome-mac-arm64','Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
      if (fs.existsSync(p)) return p; } } catch {}
  return undefined;
}
(async () => {
  const args = process.argv.slice(2);
  const sceneId = (args[0] && isNaN(+args[0])) ? args.shift() : 'act1';
  const times = args.map(Number);
  fs.mkdirSync(BUILD, { recursive: true });
  const browser = await chromium.launch({ executablePath: findChromium(), headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('console', m => { if (m.type() === 'error') console.error('[page]', m.text()); });
  await page.goto(PLAYER + `?scene=${sceneId}&embed=1`);
  await page.evaluate(() => window.ready);
  for (const t of times) {
    const dataUrl = await page.evaluate(tt => {
      window.seek(tt);
      return document.getElementById('cv').toDataURL('image/png');
    }, t);
    const f = path.join(BUILD, `snap-${sceneId}-${t}.png`);
    fs.writeFileSync(f, Buffer.from(dataUrl.split(',')[1], 'base64'));
    console.log('wrote', f);
  }
  await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
