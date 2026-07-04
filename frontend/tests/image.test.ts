/**
 * downscaleImage test — the on-device receipt-photo shrink + JPEG re-encode.
 *
 * jsdom implements neither `createImageBitmap` nor a real canvas, so both are
 * stubbed. We assert the two properties that matter: (1) the output is an
 * `image/jpeg` blob scaled so its long edge is ≤ 1600px, and (2) a null
 * `toBlob` result rejects (so the caller can fall back to the original file).
 */

import { afterEach, describe, expect, test, vi } from 'vitest';
import { downscaleImage } from '@/lib/image';

function installFakeCanvas(toBlobResult: Blob | null) {
  const drawImage = vi.fn();
  const dims = { width: 0, height: 0 };
  const fakeCanvas = {
    set width(v: number) {
      dims.width = v;
    },
    get width() {
      return dims.width;
    },
    set height(v: number) {
      dims.height = v;
    },
    get height() {
      return dims.height;
    },
    getContext: vi.fn(() => ({ drawImage })),
    toBlob: (cb: (b: Blob | null) => void, type: string) => {
      cb(toBlobResult ? new Blob([new Uint8Array([1, 2, 3])], { type }) : null);
    },
  };
  const orig = document.createElement.bind(document);
  vi.spyOn(document, 'createElement').mockImplementation(((tag: string) =>
    tag === 'canvas'
      ? (fakeCanvas as unknown as HTMLCanvasElement)
      : orig(tag)) as typeof document.createElement);
  return { dims, drawImage };
}

describe('downscaleImage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test('re-encodes to a jpeg scaled to the 1600px long edge', async () => {
    const bitmap = { width: 3200, height: 2400, close: vi.fn() };
    vi.stubGlobal('createImageBitmap', vi.fn(async () => bitmap));
    const { dims, drawImage } = installFakeCanvas(new Blob());

    const out = await downscaleImage(
      new Blob([new Uint8Array([9, 9, 9])], { type: 'image/heic' }),
    );

    expect(out.type).toBe('image/jpeg');
    // 3200×2400 → scale 0.5 → 1600×1200.
    expect(dims.width).toBe(1600);
    expect(dims.height).toBe(1200);
    expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 1600, 1200);
    // Decoded bitmap memory is released.
    expect(bitmap.close).toHaveBeenCalled();
  });

  test('leaves already-small images at their native size', async () => {
    const bitmap = { width: 800, height: 600, close: vi.fn() };
    vi.stubGlobal('createImageBitmap', vi.fn(async () => bitmap));
    const { dims } = installFakeCanvas(new Blob());

    await downscaleImage(new Blob([new Uint8Array([1])], { type: 'image/png' }));

    expect(dims.width).toBe(800);
    expect(dims.height).toBe(600);
  });

  test('rejects when canvas.toBlob yields null', async () => {
    const bitmap = { width: 100, height: 100, close: vi.fn() };
    vi.stubGlobal('createImageBitmap', vi.fn(async () => bitmap));
    installFakeCanvas(null);

    await expect(
      downscaleImage(new Blob([new Uint8Array([1])], { type: 'image/jpeg' })),
    ).rejects.toThrow();
  });
});
