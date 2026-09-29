/** Small RGB preview used only to decide whether another model call is useful. */
export interface VisualSample {
  width: number
  height: number
  pixels: Uint8Array
}

const SAMPLE_WIDTH = 32
const SAMPLE_HEIGHT = 18
const MAX_STALE_MS = 20_000

export async function sampleVisualFrame(blob: Blob): Promise<VisualSample | null> {
  if (typeof createImageBitmap !== 'function') return null
  let bitmap: ImageBitmap | null = null
  try {
    bitmap = await createImageBitmap(blob)
    const canvas = document.createElement('canvas')
    canvas.width = SAMPLE_WIDTH
    canvas.height = SAMPLE_HEIGHT
    const context = canvas.getContext('2d', { willReadFrequently: true })
    if (!context) return null
    context.drawImage(bitmap, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT)
    const rgba = context.getImageData(0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT).data
    const pixels = new Uint8Array(SAMPLE_WIDTH * SAMPLE_HEIGHT * 3)
    for (let i = 0; i < SAMPLE_WIDTH * SAMPLE_HEIGHT; i++) {
      const source = i * 4
      const target = i * 3
      pixels[target] = rgba[source]
      pixels[target + 1] = rgba[source + 1]
      pixels[target + 2] = rgba[source + 2]
    }
    return { width: bitmap.width, height: bitmap.height, pixels }
  } catch {
    // Unsupported decoders should not disable visual analysis.
    return null
  } finally {
    bitmap?.close()
  }
}

/** Compare with the last frame actually sent to the model, so small changes accumulate. */
export function shouldAnalyzeVisualFrame(
  previous: VisualSample | null,
  current: VisualSample | null,
  lastAnalyzedAt: number,
  now: number,
): boolean {
  if (!previous || !current || !lastAnalyzedAt) return true
  if (now - lastAnalyzedAt >= MAX_STALE_MS) return true
  if (previous.width !== current.width || previous.height !== current.height ||
      previous.pixels.length !== current.pixels.length) return true
  let difference = 0
  let changed = 0
  const count = current.pixels.length / 3
  for (let i = 0; i < current.pixels.length; i += 3) {
    const delta = Math.max(
      Math.abs(current.pixels[i] - previous.pixels[i]),
      Math.abs(current.pixels[i + 1] - previous.pixels[i + 1]),
      Math.abs(current.pixels[i + 2] - previous.pixels[i + 2]),
    )
    difference += delta
    if (delta >= 18) changed++
  }
  return difference / count >= 3.5 || changed / count >= 0.012
}
