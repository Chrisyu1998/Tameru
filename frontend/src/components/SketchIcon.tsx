import { useMemo } from "react";
import { cn } from "@/lib/utils";

/**
 * Hand-drawn SVG icon set. All glyphs share a 24x24 viewBox, 1.5px stroke,
 * and a deterministic per-segment wobble so the same icon renders identically
 * on every paint (no flicker on remount). Stroke uses currentColor — set the
 * color via Tailwind text utilities on the parent.
 *
 * Reserved for *decorative / ambient* slots: nav, wordmarks, category tiles,
 * empty-state ornaments, ambient sparkles. Functional micro-affordances
 * (chevrons, X, send, edit) stay on lucide.
 */

export type SketchIconKind =
  | "home"
  | "dots"
  | "chat-bubble"
  | "card"
  | "repeat"
  | "settings"
  | "seedling"
  | "sparkle"
  | "receipt"
  | "fork"
  | "car"
  | "plane"
  | "ticket"
  | "bag"
  | "bolt"
  | "heart"
  | "dot"
  | "coffee-mug"
  | "fuel-pump"
  | "pill"
  | "cart"
  | "play"
  | "badge"
  | "popcorn";

interface SketchIconProps {
  kind: SketchIconKind;
  size?: number;
  /** Stroke width override. Defaults to 1.5 — calibrated for 24px glyphs. */
  strokeWidth?: number;
  /** Wobble amplitude in viewBox units. Larger = more chaotic. */
  amp?: number;
  /** Stable seed so repeated mounts look identical. */
  seed?: number;
  className?: string;
  "aria-label"?: string;
}

// Deterministic pseudo-random in [0, 1) — same input, same output, always.
function hash(n: number): number {
  const s = Math.sin(n * 12.9898) * 43758.5453;
  return s - Math.floor(s);
}

/**
 * Build a wobbly polyline `d` string from straight segments between (x1,y1)
 * and (x2,y2). Each segment subdivides into ~6px chunks and offsets each
 * vertex perpendicular to the line by a seeded amount.
 */
function wobblyLine(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  seed: number,
  amp: number,
): string {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  if (len === 0) return `M${x1} ${y1}`;
  const steps = Math.max(2, Math.floor(len / 3.5));
  const nx = -dy / len;
  const ny = dx / len;
  let d = `M${x1.toFixed(2)} ${y1.toFixed(2)}`;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const w = (hash(seed + i * 7.13) - 0.5) * amp * 2;
    // Don't wobble the very last vertex — keeps endpoints meeting cleanly.
    const wob = i === steps ? 0 : w;
    const x = x1 + dx * t + nx * wob;
    const y = y1 + dy * t + ny * wob;
    d += ` L${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return d;
}

/** Wobbly closed polygon — concatenates wobblyLine segments + Z. */
function wobblyPath(points: Array<[number, number]>, seed: number, amp: number, close = false): string {
  if (points.length < 2) return "";
  let d = "";
  for (let i = 0; i < points.length - 1; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[i + 1];
    const seg = wobblyLine(x1, y1, x2, y2, seed + i * 11, amp);
    d += i === 0 ? seg : seg.replace(/^M[^L]+L/, " L");
  }
  if (close) {
    const [xa, ya] = points[points.length - 1];
    const [xb, yb] = points[0];
    d += wobblyLine(xa, ya, xb, yb, seed + points.length * 11, amp).replace(/^M[^L]+L/, " L");
    d += " Z";
  }
  return d;
}

/** Wobbly circle approximated as a 16-vertex polygon. */
function wobblyCircle(cx: number, cy: number, r: number, seed: number, amp: number): string {
  const verts = 16;
  const pts: Array<[number, number]> = [];
  for (let i = 0; i < verts; i++) {
    const a = (i / verts) * Math.PI * 2;
    const wob = (hash(seed + i * 5.7) - 0.5) * amp;
    const rr = r + wob;
    pts.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]);
  }
  return wobblyPath(pts, seed + 99, amp * 0.4, true);
}

/**
 * Returns the SVG path data + optional fill dots for a given icon kind.
 * All paths use stroke=currentColor; some glyphs add small filled dots
 * (returned separately as <circle> elements).
 */
function buildIcon(
  kind: SketchIconKind,
  seed: number,
  amp: number,
): { paths: string[]; dots?: Array<{ cx: number; cy: number; r: number }> } {
  switch (kind) {
    case "home": {
      // pitched roof + box
      const roof = wobblyPath(
        [
          [4, 11],
          [12, 4.5],
          [20, 11],
        ],
        seed,
        amp,
      );
      const box = wobblyPath(
        [
          [6, 10.5],
          [6, 19.5],
          [18, 19.5],
          [18, 10.5],
        ],
        seed + 30,
        amp,
      );
      const door = wobblyPath(
        [
          [10.5, 19.5],
          [10.5, 14.5],
          [13.5, 14.5],
          [13.5, 19.5],
        ],
        seed + 60,
        amp * 0.7,
      );
      return { paths: [roof, box, door] };
    }
    case "dots": {
      return {
        paths: [],
        dots: [
          { cx: 6, cy: 12, r: 1.4 },
          { cx: 12, cy: 12, r: 1.4 },
          { cx: 18, cy: 12, r: 1.4 },
        ],
      };
    }
    case "chat-bubble": {
      // Rounded body approximated with a wobbly polygon, plus a tail
      const body = wobblyPath(
        [
          [4, 7],
          [9, 4.5],
          [15, 4.5],
          [20, 7],
          [20, 13],
          [15, 15.5],
          [10, 15.5],
          [7.5, 19],
          [7.5, 15.5],
          [4, 13],
        ],
        seed,
        amp,
        true,
      );
      return {
        paths: [body],
        dots: [
          { cx: 9, cy: 10, r: 0.9 },
          { cx: 12, cy: 10, r: 0.9 },
          { cx: 15, cy: 10, r: 0.9 },
        ],
      };
    }
    case "card": {
      const outer = wobblyPath(
        [
          [3, 7],
          [21, 7],
          [21, 17],
          [3, 17],
        ],
        seed,
        amp,
        true,
      );
      const stripe = wobblyLine(3, 10.5, 21, 10.5, seed + 50, amp * 0.6);
      const line1 = wobblyLine(6, 14, 11, 14, seed + 70, amp * 0.5);
      return { paths: [outer, stripe, line1] };
    }
    case "repeat": {
      // Two arrows looping — drawn as an open arc with arrow tips
      const top = wobblyPath(
        [
          [6, 8],
          [18, 8],
          [16, 5.5],
        ],
        seed,
        amp,
      );
      const topTail = wobblyLine(18, 8, 16, 10.5, seed + 20, amp);
      const bot = wobblyPath(
        [
          [18, 16],
          [6, 16],
          [8, 18.5],
        ],
        seed + 40,
        amp,
      );
      const botTail = wobblyLine(6, 16, 8, 13.5, seed + 60, amp);
      return { paths: [top, topTail, bot, botTail] };
    }
    case "settings": {
      // Soft cog: a wobbly circle with 6 short tick marks around it
      const ring = wobblyCircle(12, 12, 4.5, seed, amp);
      const ticks: string[] = [];
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * Math.PI * 2;
        const x1 = 12 + Math.cos(a) * 6;
        const y1 = 12 + Math.sin(a) * 6;
        const x2 = 12 + Math.cos(a) * 8.5;
        const y2 = 12 + Math.sin(a) * 8.5;
        ticks.push(wobblyLine(x1, y1, x2, y2, seed + 100 + i * 7, amp * 0.6));
      }
      return { paths: [ring, ...ticks], dots: [{ cx: 12, cy: 12, r: 1.1 }] };
    }
    case "seedling": {
      // Stem + two leaves
      const stem = wobblyLine(12, 20, 12, 11, seed, amp * 0.6);
      const leafR = wobblyPath(
        [
          [12, 13],
          [16.5, 9.5],
          [18.5, 12],
          [14, 15],
          [12, 13],
        ],
        seed + 20,
        amp,
      );
      const leafL = wobblyPath(
        [
          [12, 11],
          [7.5, 7],
          [5.5, 9.5],
          [10, 12.5],
          [12, 11],
        ],
        seed + 50,
        amp,
      );
      return { paths: [stem, leafR, leafL] };
    }
    case "sparkle": {
      // Four-point star drawn as two thin diamonds
      const a = wobblyPath(
        [
          [12, 4],
          [13.6, 11],
          [12, 18],
          [10.4, 11],
        ],
        seed,
        amp * 0.6,
        true,
      );
      const b = wobblyPath(
        [
          [4, 12],
          [11, 13.6],
          [18, 12],
          [11, 10.4],
        ],
        seed + 40,
        amp * 0.6,
        true,
      );
      return { paths: [a, b] };
    }
    case "receipt": {
      // Body with zigzag bottom
      const top = wobblyLine(7, 4, 17, 4, seed, amp);
      const left = wobblyLine(7, 4, 7, 18, seed + 10, amp);
      const right = wobblyLine(17, 4, 17, 18, seed + 20, amp);
      // Zigzag bottom
      const teeth: Array<[number, number]> = [[7, 18]];
      const teethCount = 5;
      for (let i = 0; i < teethCount; i++) {
        const tx = 7 + (10 * (i + 0.5)) / teethCount;
        const tx2 = 7 + (10 * (i + 1)) / teethCount;
        teeth.push([tx, 20]);
        teeth.push([tx2, 18]);
      }
      const zig = wobblyPath(teeth, seed + 30, amp * 0.6);
      const l1 = wobblyLine(9, 8, 15, 8, seed + 50, amp * 0.5);
      const l2 = wobblyLine(9, 11, 15, 11, seed + 60, amp * 0.5);
      const l3 = wobblyLine(9, 14, 13, 14, seed + 70, amp * 0.5);
      return { paths: [top, left, right, zig, l1, l2, l3] };
    }
    case "fork": {
      const tine1 = wobblyLine(8, 3, 8, 9, seed, amp);
      const tine2 = wobblyLine(11, 3, 11, 9, seed + 10, amp);
      const tine3 = wobblyLine(14, 3, 14, 9, seed + 20, amp);
      const head = wobblyPath(
        [
          [7.5, 9],
          [14.5, 9],
          [14.5, 11.5],
          [11.5, 12.5],
        ],
        seed + 30,
        amp,
      );
      const handle = wobblyLine(11, 12.5, 11, 21, seed + 40, amp * 0.7);
      return { paths: [tine1, tine2, tine3, head, handle] };
    }
    case "car": {
      // Cabin (small dome) + body + two wheels
      const dome = wobblyPath(
        [
          [7, 12],
          [9, 8],
          [15, 8],
          [17, 12],
        ],
        seed,
        amp,
      );
      const body = wobblyPath(
        [
          [4, 12],
          [20, 12],
          [20, 16],
          [4, 16],
        ],
        seed + 20,
        amp,
        true,
      );
      const w1 = wobblyCircle(8, 17, 1.6, seed + 40, amp * 0.6);
      const w2 = wobblyCircle(16, 17, 1.6, seed + 60, amp * 0.6);
      return { paths: [dome, body, w1, w2] };
    }
    case "plane": {
      // Stylized paper plane
      const tri = wobblyPath(
        [
          [3, 12],
          [21, 4],
          [13, 21],
          [11, 14],
        ],
        seed,
        amp,
        true,
      );
      const fold = wobblyLine(11, 14, 21, 4, seed + 30, amp * 0.6);
      return { paths: [tri, fold] };
    }
    case "ticket": {
      const body = wobblyPath(
        [
          [3, 7],
          [10, 7],
          [10, 9],
          [10, 11],
          [10, 13],
          [10, 15],
          [10, 17],
          [3, 17],
        ],
        seed,
        amp,
      );
      const right = wobblyPath(
        [
          [11, 7],
          [21, 7],
          [21, 17],
          [11, 17],
          [11, 15],
          [11, 13],
          [11, 11],
          [11, 9],
        ],
        seed + 30,
        amp,
      );
      return { paths: [body, right] };
    }
    case "bag": {
      const handle = wobblyPath(
        [
          [9, 8],
          [9, 6],
          [15, 6],
          [15, 8],
        ],
        seed,
        amp * 0.7,
      );
      const body = wobblyPath(
        [
          [5, 8],
          [19, 8],
          [18, 20],
          [6, 20],
        ],
        seed + 20,
        amp,
        true,
      );
      return { paths: [handle, body] };
    }
    case "bolt": {
      const z = wobblyPath(
        [
          [13, 3],
          [5, 13],
          [11, 13],
          [9, 21],
          [19, 10],
          [13, 10],
          [13, 3],
        ],
        seed,
        amp,
        true,
      );
      return { paths: [z] };
    }
    case "heart": {
      const h = wobblyPath(
        [
          [12, 20],
          [4, 12],
          [4, 8],
          [8, 5],
          [12, 8],
          [16, 5],
          [20, 8],
          [20, 12],
          [12, 20],
        ],
        seed,
        amp * 0.7,
      );
      return { paths: [h] };
    }
    case "coffee-mug": {
      const body = wobblyPath(
        [
          [8, 9],
          [16, 9],
          [15, 19],
          [9, 19],
        ],
        seed,
        amp,
        true,
      );
      const handle = wobblyPath(
        [
          [16, 11],
          [19, 12],
          [19, 15],
          [16, 16],
        ],
        seed + 30,
        amp * 0.7,
      );
      const steam1 = wobblyLine(10.5, 7, 10.5, 4, seed + 60, amp * 1.6);
      const steam2 = wobblyLine(13.5, 7, 13.5, 4, seed + 80, amp * 1.6);
      return { paths: [body, handle, steam1, steam2] };
    }
    case "fuel-pump": {
      const main = wobblyPath(
        [
          [5, 6],
          [13, 6],
          [13, 20],
          [5, 20],
        ],
        seed,
        amp,
        true,
      );
      const display = wobblyPath(
        [
          [7, 9],
          [11, 9],
          [11, 12],
          [7, 12],
        ],
        seed + 20,
        amp * 0.5,
        true,
      );
      const hose = wobblyPath(
        [
          [13, 9],
          [16, 9],
          [16, 14],
          [18.5, 14],
        ],
        seed + 40,
        amp,
      );
      const nozzle = wobblyLine(18.5, 12, 18.5, 16, seed + 60, amp * 0.6);
      return { paths: [main, display, hose, nozzle] };
    }
    case "pill": {
      const top = wobblyLine(8, 9, 16, 9, seed, amp * 0.4);
      const bot = wobblyLine(8, 15, 16, 15, seed + 10, amp * 0.4);
      const leftCap = wobblyPath(
        [
          [8, 9],
          [5, 10.2],
          [5, 13.8],
          [8, 15],
        ],
        seed + 20,
        amp,
      );
      const rightCap = wobblyPath(
        [
          [16, 9],
          [19, 10.2],
          [19, 13.8],
          [16, 15],
        ],
        seed + 30,
        amp,
      );
      const divider = wobblyLine(12, 9, 12, 15, seed + 40, amp * 0.5);
      return { paths: [top, bot, leftCap, rightCap, divider] };
    }
    case "cart": {
      const basket = wobblyPath(
        [
          [5, 8],
          [19, 8],
          [17, 16],
          [7, 16],
        ],
        seed,
        amp,
        true,
      );
      const handle = wobblyLine(5, 8, 3, 5, seed + 20, amp);
      const wheel1 = wobblyCircle(9, 19, 1.3, seed + 40, amp * 0.6);
      const wheel2 = wobblyCircle(15, 19, 1.3, seed + 60, amp * 0.6);
      return { paths: [basket, handle, wheel1, wheel2] };
    }
    case "play": {
      const ring = wobblyCircle(12, 12, 8, seed, amp);
      const tri = wobblyPath(
        [
          [10, 8],
          [16, 12],
          [10, 16],
        ],
        seed + 30,
        amp * 0.7,
        true,
      );
      return { paths: [ring, tri] };
    }
    case "badge": {
      const ring = wobblyCircle(12, 9, 4.5, seed, amp);
      const ribbon1 = wobblyPath(
        [
          [9.5, 12.5],
          [8, 20],
          [12, 17],
        ],
        seed + 30,
        amp,
      );
      const ribbon2 = wobblyPath(
        [
          [14.5, 12.5],
          [16, 20],
          [12, 17],
        ],
        seed + 50,
        amp,
      );
      return {
        paths: [ring, ribbon1, ribbon2],
        dots: [{ cx: 12, cy: 9, r: 1.2 }],
      };
    }
    case "popcorn": {
      const bucket = wobblyPath(
        [
          [6.5, 10],
          [17.5, 10],
          [16, 21],
          [8, 21],
        ],
        seed,
        amp,
        true,
      );
      const stripe1 = wobblyLine(10, 11, 9.5, 20, seed + 20, amp * 0.4);
      const stripe2 = wobblyLine(14, 11, 14.5, 20, seed + 25, amp * 0.4);
      const kernels = wobblyPath(
        [
          [6.5, 10],
          [8, 6.5],
          [10, 9],
          [12, 5],
          [14, 9],
          [16, 6.5],
          [17.5, 10],
        ],
        seed + 40,
        amp * 1.2,
      );
      return { paths: [bucket, stripe1, stripe2, kernels] };
    }
    case "dot": {
      return { paths: [], dots: [{ cx: 12, cy: 12, r: 3 }] };
    }
  }
}

export function SketchIcon({
  kind,
  size = 24,
  strokeWidth = 1.5,
  amp = 0.4,
  seed = 17,
  className,
  "aria-label": ariaLabel,
}: SketchIconProps) {
  const { paths, dots } = useMemo(() => buildIcon(kind, seed, amp), [kind, seed, amp]);

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn("inline-block", className)}
      role={ariaLabel ? "img" : "presentation"}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
    >
      {paths.map((d, i) => (
        <path key={i} d={d} />
      ))}
      {dots?.map((d, i) => (
        <circle key={`dot-${i}`} cx={d.cx} cy={d.cy} r={d.r} fill="currentColor" stroke="none" />
      ))}
    </svg>
  );
}
