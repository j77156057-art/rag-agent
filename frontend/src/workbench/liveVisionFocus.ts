export interface FocusRegion { x: number; y: number; width: number; height: number }

const clamp = (value: number) => Math.max(0, Math.min(1, value))

/** Pointer coordinates become a stable fraction of the captured frame. */
export function normalizeFocusRegion(
  bounds: { left: number; top: number; width: number; height: number },
  start: { x: number; y: number },
  end: { x: number; y: number },
): FocusRegion | null {
  if (bounds.width <= 0 || bounds.height <= 0) return null
  const x1 = clamp((start.x - bounds.left) / bounds.width)
  const y1 = clamp((start.y - bounds.top) / bounds.height)
  const x2 = clamp((end.x - bounds.left) / bounds.width)
  const y2 = clamp((end.y - bounds.top) / bounds.height)
  const region = { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) }
  return region.width >= 0.03 && region.height >= 0.03 ? region : null
}

export function focusCropRect(region: FocusRegion, width: number, height: number) {
  const x = Math.max(0, Math.min(width - 1, Math.floor(region.x * width)))
  const y = Math.max(0, Math.min(height - 1, Math.floor(region.y * height)))
  const cropWidth = Math.max(1, Math.min(width - x, Math.ceil(region.width * width)))
  const cropHeight = Math.max(1, Math.min(height - y, Math.ceil(region.height * height)))
  return { x, y, width: cropWidth, height: cropHeight }
}

export async function cropFocusFrame(image: Blob, region: FocusRegion): Promise<Blob> {
  const bitmap = await createImageBitmap(image)
  try {
    const rect = focusCropRect(region, bitmap.width, bitmap.height)
    const canvas = document.createElement('canvas')
    canvas.width = rect.width
    canvas.height = rect.height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('无法创建区域画布')
    context.drawImage(bitmap, rect.x, rect.y, rect.width, rect.height, 0, 0, rect.width, rect.height)
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      blob => blob ? resolve(blob) : reject(new Error('区域截图生成失败')), 'image/jpeg', .86,
    ))
  } finally {
    bitmap.close()
  }
}
