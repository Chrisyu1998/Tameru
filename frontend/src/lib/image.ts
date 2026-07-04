/**
 * Client-side image downscale + JPEG re-encode for receipt uploads.
 *
 * Three jobs in one pass, all before the photo leaves the device:
 *   1. Shrink to a max long-edge so uploads are small and Gemini Vision isn't
 *      billed for pixels receipt OCR doesn't need (a 12MP phone photo is wildly
 *      more than required).
 *   2. Re-encode to JPEG. This is what makes iOS work: Safari captures HEIC by
 *      default, which the upload/Gemini path doesn't want — but Safari *can*
 *      decode HEIC onto a canvas, and `canvas.toBlob('image/jpeg')` exports
 *      JPEG regardless of the source format, so the HEIC problem disappears as
 *      a side effect of re-encoding.
 *   3. Bake EXIF orientation via `createImageBitmap(.., {imageOrientation:
 *      'from-image'})` so a portrait phone photo doesn't reach Gemini rotated.
 */

const MAX_EDGE = 1600;
const JPEG_QUALITY = 0.85;

/**
 * Downscale + JPEG-re-encode an image blob. Returns a fresh `image/jpeg`
 * Blob no larger than MAX_EDGE on its longest side. Rejects if the input
 * can't be decoded or the canvas export fails — the caller (chat.tsx) falls
 * back to uploading the original file in that case.
 */
export async function downscaleImage(file: Blob): Promise<Blob> {
  const source = await _decode(file);
  try {
    // ImageBitmap exposes width/height; a detached <img> reports its intrinsic
    // size via naturalWidth/Height (its .width can be 0 when unattached). Guard
    // against a zero-size decode so the caller falls back to the original file
    // rather than uploading an empty canvas.
    const srcW = (source as HTMLImageElement).naturalWidth || source.width;
    const srcH = (source as HTMLImageElement).naturalHeight || source.height;
    if (!srcW || !srcH) throw new Error("decoded image has zero dimensions");
    const { width, height } = _fitWithin(srcW, srcH, MAX_EDGE);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas context unavailable");
    ctx.drawImage(source, 0, 0, width, height);
    return await _canvasToJpeg(canvas);
  } finally {
    // ImageBitmap holds decoded pixel memory until closed; the <img> fallback
    // has no close() and is GC'd normally.
    if ("close" in source && typeof source.close === "function") source.close();
  }
}

async function _decode(file: Blob): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === "function") {
    try {
      return await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      // Some Safari versions reject the options bag or HEIC via
      // createImageBitmap — fall through to the <img> decode path.
    }
  }
  return await _decodeViaImg(file);
}

function _decodeViaImg(file: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("could not decode image"));
    };
    img.src = url;
  });
}

function _fitWithin(
  w: number,
  h: number,
  maxEdge: number,
): { width: number; height: number } {
  const longest = Math.max(w, h);
  if (longest <= maxEdge || longest === 0) return { width: w, height: h };
  const scale = maxEdge / longest;
  return { width: Math.round(w * scale), height: Math.round(h * scale) };
}

function _canvasToJpeg(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) =>
        blob ? resolve(blob) : reject(new Error("canvas.toBlob returned null")),
      "image/jpeg",
      JPEG_QUALITY,
    );
  });
}
