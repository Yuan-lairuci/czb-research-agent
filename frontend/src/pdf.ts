/**
 * pdfjs-dist 封装：worker 初始化、加载文档、渲染页到 canvas、文本层（可选）。
 * 对 pdfjs-dist 3.x / 4.x / 5.x 的 TextLayer API 差异做特性探测，宁可降级不报错。
 */
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy, PDFPageProxy, PageViewport } from 'pdfjs-dist'
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc

/** 加载 PDF 文档（传副本，避免 worker 转移后原 buffer 被 detach）。 */
export async function loadDocument(bytes: Uint8Array): Promise<PDFDocumentProxy> {
  return pdfjs.getDocument({ data: bytes.slice() }).promise
}

/** 渲染一页到 canvas（按 scale 光栅化）。 */
export async function renderPageToCanvas(
  page: PDFPageProxy,
  canvas: HTMLCanvasElement,
  scale: number,
): Promise<void> {
  const viewport = page.getViewport({ scale })
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  canvas.width = Math.floor(viewport.width)
  canvas.height = Math.floor(viewport.height)
  await page.render({ canvasContext: ctx, canvas, viewport }).promise
}

interface TextLayerCtor {
  new (args: Record<string, unknown>): { render(): Promise<void> | void }
}

interface PdfjsExtras {
  TextLayer?: TextLayerCtor
  renderTextLayer?: (args: Record<string, unknown>) => { promise: Promise<void> }
}

/**
 * 在容器内渲染文本层（覆盖 canvas 的可选中文本，供选取/引用标记点击）。
 * 成功返回 true；当前 pdfjs 版本不支持或渲染失败返回 false（调用方降级即可）。
 */
export async function renderTextLayer(
  page: PDFPageProxy,
  container: HTMLElement,
  viewport: PageViewport,
): Promise<boolean> {
  const extras = pdfjs as unknown as PdfjsExtras
  try {
    const source = page.streamTextContent()
    if (typeof extras.TextLayer === 'function') {
      const layer = new extras.TextLayer({ textContentSource: source, container, viewport })
      await layer.render()
      return true
    }
    if (typeof extras.renderTextLayer === 'function') {
      await extras.renderTextLayer({ textContentSource: source, container, viewport }).promise
      return true
    }
    return false
  } catch {
    return false
  }
}
