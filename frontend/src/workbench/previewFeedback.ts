/** Internal handoff: the receiver acknowledges every request, including blocks. */
export type FeedbackDeliveryStatus = 'processing' | 'awaiting_review' | 'pending' | 'failed'
export interface PreviewFeedbackRequest {
  prompt: string
  projectId: string
  images?: Blob[]
  onStatus?: (status: FeedbackDeliveryStatus, detail?: string) => void
}

export function blobDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('无法读取截图'))
    reader.readAsDataURL(blob)
  })
}
