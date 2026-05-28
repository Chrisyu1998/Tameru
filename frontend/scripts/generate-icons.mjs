// Generates PWA icons using only Node built-ins (zlib + Buffer).
// Output:
//   public/icon-192.png         manifest icon (standard)
//   public/icon-512.png         manifest icon (large + maskable)
//   public/apple-touch-icon.png iOS Add-to-Home-Screen (180x180)
//   public/favicon.svg          browser tab favicon
//
// Design: cream background with a moss-filled coin (circle) centered.
// The "coin" metaphor matches the spending-intelligence framing and
// drops the original placeholder lowercase-"t" letterform. The coin
// radius is 0.40 * size — the maskable-icon safe zone, so the full
// coin stays visible even when the OS applies aggressive circle /
// rounded-square masking on Android or iOS home-screen.
//
// Swap `ACCENT` and `CREAM` to rebrand; rerun `npm run icons`.

import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = resolve(__dirname, '..', 'public');
mkdirSync(PUBLIC_DIR, { recursive: true });

// Match :root tokens in src/index.css.
const ACCENT = [0x6c, 0x8a, 0x6b]; // --tameru-accent-base
const CREAM = [0xfb, 0xf6, 0xec];  // --tameru-surface

// Render a centered, filled coin: moss circle on cream background.
// Radius = 0.40 * size keeps the full coin inside the maskable safe
// zone (the OS may mask anything outside the central 80% region into
// a circle or rounded square; staying within 0.40r ensures the coin
// never gets clipped). Half-pixel center (cx-0.5, cy-0.5) keeps the
// circle visually centered for even-pixel sizes.
function pixelAt(x, y, size) {
  const cx = size / 2 - 0.5;
  const cy = size / 2 - 0.5;
  const radius = size * 0.40;
  const dx = x - cx;
  const dy = y - cy;
  const dist = Math.sqrt(dx * dx + dy * dy);
  return dist <= radius ? ACCENT : CREAM;
}

function pngChunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii');
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crcInput = Buffer.concat([typeBuf, data]);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(crcInput) >>> 0, 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

function encodePng(size) {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr.writeUInt8(8, 8);  // bit depth
  ihdr.writeUInt8(2, 9);  // color type = RGB
  ihdr.writeUInt8(0, 10); // compression
  ihdr.writeUInt8(0, 11); // filter
  ihdr.writeUInt8(0, 12); // interlace

  // Raw scanlines: each row is [filter=0, R, G, B, R, G, B, ...].
  const rowLen = 1 + size * 3;
  const raw = Buffer.alloc(rowLen * size);
  for (let y = 0; y < size; y++) {
    raw[y * rowLen] = 0;
    for (let x = 0; x < size; x++) {
      const [r, g, b] = pixelAt(x, y, size);
      const off = y * rowLen + 1 + x * 3;
      raw[off] = r;
      raw[off + 1] = g;
      raw[off + 2] = b;
    }
  }
  const idat = deflateSync(raw);

  return Buffer.concat([
    signature,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', idat),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function writePng(name, size) {
  const out = resolve(PUBLIC_DIR, name);
  const buf = encodePng(size);
  writeFileSync(out, buf);
  const sha = createHash('sha256').update(buf).digest('hex').slice(0, 12);
  console.log(`  ${name.padEnd(28)}  ${size}x${size}  ${buf.length} bytes  sha256:${sha}`);
}

function writeFaviconSvg() {
  // Cream background + moss-filled coin centered. Radius 25.6 = 0.40
  // of the 64-unit viewBox, mirroring the PNG generator's safe-zone
  // sizing so PWA installs (PNG) and browser tabs (SVG) match.
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" fill="#fbf6ec"/>
  <circle cx="32" cy="32" r="25.6" fill="#6c8a6b"/>
</svg>
`;
  writeFileSync(resolve(PUBLIC_DIR, 'favicon.svg'), svg);
  console.log(`  favicon.svg                  vector`);
}

console.log('Generating Tameru PWA icons:');
writePng('icon-192.png', 192);
writePng('icon-512.png', 512);
writePng('apple-touch-icon.png', 180);
writeFaviconSvg();
console.log('Done.');
