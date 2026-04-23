// Generates PWA icons using only Node built-ins (zlib + Buffer).
// Output:
//   public/icon-192.png         manifest icon (standard)
//   public/icon-512.png         manifest icon (large + maskable)
//   public/apple-touch-icon.png iOS Add-to-Home-Screen (180x180)
//   public/favicon.svg          browser tab favicon
//
// Placeholder design: moss-accent square with a cream lowercase "t" glyph.
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

// Render a lowercase "t" as two rectangles centered in the square.
// Safe-zone friendly (maskable icons need ≥ 40% center area preserved).
function pixelAt(x, y, size) {
  const stemX = size * 0.44;
  const stemW = size * 0.12;
  const stemYTop = size * 0.22;
  const stemYBottom = size * 0.78;

  const crossYTop = size * 0.36;
  const crossYBottom = size * 0.44;
  const crossXLeft = size * 0.32;
  const crossXRight = size * 0.68;

  const inStem =
    x >= stemX && x < stemX + stemW && y >= stemYTop && y < stemYBottom;
  const inCross =
    x >= crossXLeft && x < crossXRight && y >= crossYTop && y < crossYBottom;

  return inStem || inCross ? CREAM : ACCENT;
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
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#6c8a6b"/>
  <rect x="28.16" y="14.08" width="7.68" height="35.84" fill="#fbf6ec"/>
  <rect x="20.48" y="23.04" width="23.04" height="5.12" fill="#fbf6ec"/>
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
