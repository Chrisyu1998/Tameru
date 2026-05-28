// Generates PWA icons using only Node built-ins (zlib + Buffer).
// Output:
//   public/icon-192.png         manifest icon (standard)
//   public/icon-512.png         manifest icon (large + maskable)
//   public/apple-touch-icon.png iOS Add-to-Home-Screen (180x180)
//   public/favicon.svg          browser tab favicon
//
// Design: a single coin viewed at a slight overhead angle so both
// the face and the rim/side are visible. Clean geometric strokes —
// the hand-drawn-wobble variant was tried and rejected; the brand
// icon stays crisp even though the in-app category glyphs are
// hand-drawn. Palette is inverted vs. the rest of the UI: moss is
// the background, cream is the stroke, so the coin reads as a
// light-colored object on a deep field.
//
// Shape elements:
//   • Top face — ellipse outline (foreshortened ~2.3:1)
//   • Side rim — two vertical strokes connecting the face's left/
//     right extents down to the bottom rim arc; bottom rim is a
//     half-ellipse dipping below the face by COIN_BOTTOM_DEPTH
//   • $ stamp — small vertical stem + Lucide-CircleDollarSign-style
//     zigzag, proves it's a coin
//
// Output uses 3x3 supersampling for clean anti-aliased edges. Math-
// based ellipse distance approximation (|F| / 2|∇F|) is used for the
// curved strokes; line segments use exact point-to-segment distance.
//
// Swap `MOSS` / `CREAM` to rebrand. Tweak `COIN_*` constants to
// resize. Rerun `npm run icons`.

import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = resolve(__dirname, '..', 'public');
mkdirSync(PUBLIC_DIR, { recursive: true });

// Match :root tokens in src/index.css.
const MOSS = [0x6c, 0x8a, 0x6b];
const CREAM = [0xfb, 0xf6, 0xec];
const BG = MOSS;
const FG = CREAM;

// Coin layout (fractions of icon size). Both faces of a real coin
// are identical circles viewed from the same angle — i.e. identical
// ellipses. So the bottom face uses the same (rx, ry) as the top
// face; only the y-position differs. THICKNESS is the side-wall
// height between the two faces. The earlier revision had the bottom
// face shallower than the top, which read as inconsistent.
const COIN_RX = 0.30;
const COIN_RY = 0.10;
const COIN_THICKNESS = 0.11;
const COIN_CY_OFFSET = -0.075;    // face above center so the bottom
                                  // face doesn't push the silhouette
                                  // off-center

// $ stamp on the face. Set DOLLAR_SIGN to false for a clean coin
// (no glyph). When enabled, the glyph uses a THINNER stroke than the
// coin outline so it reads as engraving rather than competing with
// the coin silhouette.
const DOLLAR_SIGN = true;
const STEM_HALF = 0.050;
const S_HALF_W = 0.032;
const S_HALF_H = 0.020;
const STROKE_W = 0.04;
// Dollar-sign stroke width. ~40% of the coin outline so the glyph
// looks engraved at small sizes (192/180) instead of filling in.
const DOLLAR_STROKE_W = STROKE_W * 0.45;

const MOSS_HEX = `#${MOSS.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
const CREAM_HEX = `#${CREAM.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
const BG_HEX = `#${BG.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
const FG_HEX = `#${FG.map((v) => v.toString(16).padStart(2, '0')).join('')}`;

// Convenience — colors referenced lazily so they're discoverable when
// debugging. Marked-unused vars are intentional; the rebrand workflow
// is "edit MOSS/CREAM, rerun."
void MOSS_HEX; void CREAM_HEX;

/**
 * Per-icon geometric layout. Computed once per size; passed into the
 * pixel loop so we don't recompute the layout for every sample.
 */
function buildLayout(size) {
  const cx = size / 2;
  const cy = size / 2 + size * COIN_CY_OFFSET;
  return {
    cx,
    cy,
    rx: size * COIN_RX,
    ry: size * COIN_RY,
    thick: size * COIN_THICKNESS,
    stemHalf: size * STEM_HALF,
    sHW: size * S_HALF_W,
    sHH: size * S_HALF_H,
  };
}

/**
 * Approximate perpendicular distance from (x,y) to the ellipse rim
 * centered at (cx,cy) with radii (rx,ry). Uses the gradient-based
 * first-order approximation |F| / |∇F| where
 * F = (dx/rx)² + (dy/ry)² - 1. Accurate near the rim (which is all
 * that matters for stroking).
 */
function distToEllipseRim(x, y, cx, cy, rx, ry) {
  const dx = x - cx;
  const dy = y - cy;
  const F = (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry) - 1;
  const gradOver2 = Math.sqrt(
    (dx * dx) / (rx * rx * rx * rx) +
    (dy * dy) / (ry * ry * ry * ry),
  );
  if (gradOver2 === 0) return Math.abs(F);
  return Math.abs(F) / (2 * gradOver2);
}

/** Exact distance from (px,py) to segment (x1,y1)-(x2,y2). */
function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  if (t < 0) t = 0;
  else if (t > 1) t = 1;
  const qx = x1 + t * dx;
  const qy = y1 + t * dy;
  return Math.hypot(px - qx, py - qy);
}

/**
 * Color for a single sub-pixel sample. Coin outline + side wall use
 * `halfStroke`; the $ glyph uses a separate (smaller) `halfDollar`
 * so it reads as engraving instead of competing with the silhouette.
 */
function sampleAt(x, y, L, halfStroke, halfDollar) {
  // Face ellipse outline
  if (distToEllipseRim(x, y, L.cx, L.cy, L.rx, L.ry) < halfStroke) return FG;

  // Side wall verticals — connect face's left/right extents to the
  // bottom rim arc.
  if (distToSegment(x, y, L.cx - L.rx, L.cy, L.cx - L.rx, L.cy + L.thick) < halfStroke) return FG;
  if (distToSegment(x, y, L.cx + L.rx, L.cy, L.cx + L.rx, L.cy + L.thick) < halfStroke) return FG;

  // Bottom rim arc — bottom HALF of the bottom face (the top half is
  // hidden behind the top face). The bottom face is the SAME ellipse
  // as the top, just shifted down by `thick`, so it uses identical
  // (rx, ry). Only paint where y >= cy + thick (the visible half).
  if (y >= L.cy + L.thick) {
    if (distToEllipseRim(x, y, L.cx, L.cy + L.thick, L.rx, L.ry) < halfStroke) return FG;
  }

  if (DOLLAR_SIGN) {
    // $ stem
    if (distToSegment(x, y, L.cx, L.cy - L.stemHalf, L.cx, L.cy + L.stemHalf) < halfDollar) return FG;

    // $ S-zigzag — Lucide CircleDollarSign style.
    const pts = [
      [L.cx + L.sHW, L.cy - 2 * L.sHH],
      [L.cx - L.sHW, L.cy - 2 * L.sHH],
      [L.cx - L.sHW, L.cy],
      [L.cx + L.sHW, L.cy],
      [L.cx + L.sHW, L.cy + 2 * L.sHH],
      [L.cx - L.sHW, L.cy + 2 * L.sHH],
    ];
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      if (distToSegment(x, y, x1, y1, x2, y2) < halfDollar) return FG;
    }
  }

  return BG;
}

/** 3x3 supersampled pixel for clean anti-aliased edges. */
function pixelAt(x, y, layout, halfStroke, halfDollar) {
  let r = 0, g = 0, b = 0;
  const N = 3;
  for (let sy = 0; sy < N; sy++) {
    for (let sx = 0; sx < N; sx++) {
      const px = x + (sx + 0.5) / N;
      const py = y + (sy + 0.5) / N;
      const c = sampleAt(px, py, layout, halfStroke, halfDollar);
      r += c[0]; g += c[1]; b += c[2];
    }
  }
  const inv = 1 / (N * N);
  return [
    Math.round(r * inv),
    Math.round(g * inv),
    Math.round(b * inv),
  ];
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
  ihdr.writeUInt8(8, 8);
  ihdr.writeUInt8(2, 9);
  ihdr.writeUInt8(0, 10);
  ihdr.writeUInt8(0, 11);
  ihdr.writeUInt8(0, 12);

  const layout = buildLayout(size);
  const halfStroke = (size * STROKE_W) / 2;
  const halfDollar = (size * DOLLAR_STROKE_W) / 2;

  const rowLen = 1 + size * 3;
  const raw = Buffer.alloc(rowLen * size);
  for (let y = 0; y < size; y++) {
    raw[y * rowLen] = 0;
    for (let x = 0; x < size; x++) {
      const [r, g, b] = pixelAt(x, y, layout, halfStroke, halfDollar);
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
  // SVG-native primitives keep the source readable and vector-perfect.
  // The bottom-rim arc is drawn as an SVG path with the A (arc) command,
  // giving smooth curves with no polygon approximation needed. Clip to
  // the face's bottom half via a half-ellipse arc that starts/ends at
  // the face's left/right extents.
  const SIZE = 64;
  const L = buildLayout(SIZE);
  const stroke = SIZE * STROKE_W;

  // Bottom-rim arc — bottom half of the bottom face. Uses (rx, ry)
  // identical to the top face for geometric consistency.
  const arcPath =
    `M ${(L.cx - L.rx).toFixed(2)} ${(L.cy + L.thick).toFixed(2)} ` +
    `A ${L.rx.toFixed(2)} ${L.ry.toFixed(2)} 0 0 0 ` +
    `${(L.cx + L.rx).toFixed(2)} ${(L.cy + L.thick).toFixed(2)}`;

  // $ S-zigzag as a single polyline.
  const sPts = [
    [L.cx + L.sHW, L.cy - 2 * L.sHH],
    [L.cx - L.sHW, L.cy - 2 * L.sHH],
    [L.cx - L.sHW, L.cy],
    [L.cx + L.sHW, L.cy],
    [L.cx + L.sHW, L.cy + 2 * L.sHH],
    [L.cx - L.sHW, L.cy + 2 * L.sHH],
  ]
    .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
    .join(' ');

  const dollarStroke = SIZE * DOLLAR_STROKE_W;
  const dollarSvg = DOLLAR_SIGN
    ? `    <g stroke-width="${dollarStroke.toFixed(2)}">
      <line x1="${L.cx}" y1="${(L.cy - L.stemHalf).toFixed(2)}" x2="${L.cx}" y2="${(L.cy + L.stemHalf).toFixed(2)}"/>
      <polyline points="${sPts}"/>
    </g>`
    : '';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SIZE} ${SIZE}">
  <rect width="${SIZE}" height="${SIZE}" fill="${BG_HEX}"/>
  <g fill="none" stroke="${FG_HEX}" stroke-width="${stroke.toFixed(2)}" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="${L.cx}" cy="${L.cy.toFixed(2)}" rx="${L.rx.toFixed(2)}" ry="${L.ry.toFixed(2)}"/>
    <line x1="${(L.cx - L.rx).toFixed(2)}" y1="${L.cy.toFixed(2)}" x2="${(L.cx - L.rx).toFixed(2)}" y2="${(L.cy + L.thick).toFixed(2)}"/>
    <line x1="${(L.cx + L.rx).toFixed(2)}" y1="${L.cy.toFixed(2)}" x2="${(L.cx + L.rx).toFixed(2)}" y2="${(L.cy + L.thick).toFixed(2)}"/>
    <path d="${arcPath}"/>
${dollarSvg}
  </g>
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
