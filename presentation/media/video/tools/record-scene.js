#!/usr/bin/env node
// Render a code scene to mp4: headless Chromium steps window.seek(t) at 30fps,
// PNG screenshots piped straight into ffmpeg (no intermediate files).
// Usage: node record-scene.js [sceneId=act1] [--fps=30]
// Modeled on the Corellia explainer recorder.
const { chromium } = require('playwright-core');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const BUILD = path.join(ROOT, 'build');
const PLAYER = 'file://' + path.join(ROOT, 'player.html');

// reuse the system playwright chromium if present; otherwise let playwright resolve.
function findChromium() {
  const base = path.join(process.env.HOME, 'Library/Caches/ms-playwright');
  try {
    const dirs = fs.readdirSync(base).filter(d => d.startsWith('chromium-')).sort();
    if (dirs.length) {
      const p = path.join(base, dirs[dirs.length - 1], 'chrome-mac-arm64',
        'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
      if (fs.existsSync(p)) return p;
    }
  } catch {}
  return undefined; // playwright-core default
}

(async () => {
  const sceneId = (process.argv[2] && !process.argv[2].startsWith('--')) ? process.argv[2] : 'act1';
  const fps = +((process.argv.find(a => a.startsWith('--fps=')) || '').split('=')[1] || 30);
  fs.mkdirSync(BUILD, { recursive: true });

  const browser = await chromium.launch({ executablePath: findChromium(), headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('console', m => { if (m.type() === 'error') console.error('[page]', m.text()); });
  await page.goto(PLAYER + `?scene=${sceneId}&embed=1`);
  const meta = await page.evaluate(() => window.ready);
  const frames = Math.ceil(meta.duration * fps);
  console.log(`${sceneId}: ${meta.duration}s -> ${frames} frames @ ${fps}fps`);

  const out = path.join(BUILD, `${sceneId}.mp4`);
  const ff = spawn('ffmpeg', [
    '-y', '-f', 'image2pipe', '-vcodec', 'png', '-r', String(fps), '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
    '-r', String(fps), out,
  ], { stdio: ['pipe', 'ignore', 'pipe'] });
  let ffErr = '';
  ff.stderr.on('data', d => { ffErr += d; if (ffErr.length > 40000) ffErr = ffErr.slice(-20000); });
  const ffDone = new Promise((res, rej) => ff.on('close', c => (c === 0 ? res() : rej(new Error('ffmpeg exit ' + c + '\n' + ffErr.slice(-2000))))));

  const t0 = Date.now();
  for (let i = 0; i < frames; i++) {
    // Read the canvas backing store directly (toDataURL) — an element screenshot
    // drops canvas shadow/glow layers, so the dots' glow would vanish.
    const dataUrl = await page.evaluate(t => {
      window.seek(t);
      return document.getElementById('cv').toDataURL('image/png');
    }, i / fps);
    const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (i % 60 === 0) console.log(`  frame ${i}/${frames} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
  }
  ff.stdin.end();
  await ffDone;
  await browser.close();
  console.log(`wrote ${out} in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
