import { useMemo } from "react";
import { cn } from "@/lib/utils";

/**
 * Larger hand-drawn ornaments for empty states. Same wobble technique as
 * SketchIcon but scaled up to ~96px and with a small composition (multiple
 * shapes) to feel like a deliberate illustration, not just a big icon.
 *
 * Stroke uses currentColor — wrapper sets it to ink-tertiary for a soft
 * presence that doesn't compete with the empty-state copy.
 */

export type SketchIllustrationKind = "empty-list" | "no-cards" | "all-set";

interface SketchIllustrationProps {
  kind: SketchIllustrationKind;
  size?: number;
  amp?: number;
  seed?: number;
  className?: string;
}

function hash(n: number): number {
  const s = Math.sin(n * 12.9898) * 43758.5453;
  return s - Math.floor(s);
}

function wobblyLine(x1: number, y1: number, x2: number, y2: number, seed: number, amp: number): string {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  if (len === 0) return `M${x1} ${y1}`;
  const steps = Math.max(2, Math.floor(len / 4));
  const nx = -dy / len;
  const ny = dx / len;
  let d = `M${x1.toFixed(2)} ${y1.toFixed(2)}`;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const wob = i === steps ? 0 : (hash(seed + i * 7.13) - 0.5) * amp * 2;
    const x = x1 + dx * t + nx * wob;
    const y = y1 + dy * t + ny * wob;
    d += ` L${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return d;
}

function wobblyPath(points: Array<[number, number]>, seed: number, amp: number, close = false): string {
  if (points.length < 2) return "";
  let d = "";
  for (let i = 0; i < points.length - 1; i++) {
    const seg = wobblyLine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1], seed + i * 11, amp);
    d += i === 0 ? seg : seg.replace(/^M[^L]+L/, " L");
  }
  if (close) {
    const a = points[points.length - 1];
    const b = points[0];
    d += wobblyLine(a[0], a[1], b[0], b[1], seed + points.length * 11, amp).replace(/^M[^L]+L/, " L");
    d += " Z";
  }
  return d;
}

function buildIllustration(kind: SketchIllustrationKind, seed: number, amp: number): string[] {
  switch (kind) {
    case "empty-list": {
      // A single sketched receipt with a few wash lines.
      const top = wobblyLine(28, 18, 68, 18, seed, amp);
      const left = wobblyLine(28, 18, 28, 72, seed + 10, amp);
      const right = wobblyLine(68, 18, 68, 72, seed + 20, amp);
      const teeth: Array<[number, number]> = [[28, 72]];
      for (let i = 0; i < 5; i++) {
        teeth.push([28 + (40 * (i + 0.5)) / 5, 80]);
        teeth.push([28 + (40 * (i + 1)) / 5, 72]);
      }
      const zig = wobblyPath(teeth, seed + 30, amp * 0.6);
      const l1 = wobblyLine(34, 30, 62, 30, seed + 50, amp * 0.5);
      const l2 = wobblyLine(34, 40, 62, 40, seed + 60, amp * 0.5);
      const l3 = wobblyLine(34, 50, 50, 50, seed + 70, amp * 0.5);
      return [top, left, right, zig, l1, l2, l3];
    }
    case "no-cards": {
      // Two stacked cards at slight angles.
      const back = wobblyPath(
        [
          [22, 32],
          [70, 24],
          [76, 56],
          [28, 64],
        ],
        seed,
        amp,
        true,
      );
      const front = wobblyPath(
        [
          [18, 40],
          [66, 36],
          [68, 70],
          [20, 74],
        ],
        seed + 30,
        amp,
        true,
      );
      const stripe = wobblyLine(20, 50, 66, 47, seed + 60, amp * 0.5);
      return [back, front, stripe];
    }
    case "all-set": {
      // A leaf cradling a checkmark.
      const leaf = wobblyPath(
        [
          [16, 64],
          [22, 36],
          [48, 18],
          [80, 22],
          [76, 50],
          [54, 70],
          [16, 64],
        ],
        seed,
        amp,
      );
      const check = wobblyPath(
        [
          [34, 46],
          [44, 56],
          [62, 34],
        ],
        seed + 40,
        amp * 0.5,
      );
      return [leaf, check];
    }
  }
}

export function SketchIllustration({
  kind,
  size = 96,
  amp = 0.6,
  seed = 23,
  className,
}: SketchIllustrationProps) {
  const paths = useMemo(() => buildIllustration(kind, seed, amp), [kind, seed, amp]);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 96 96"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn("inline-block", className)}
      aria-hidden="true"
    >
      {paths.map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}
