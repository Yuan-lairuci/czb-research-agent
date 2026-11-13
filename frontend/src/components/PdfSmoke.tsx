import { useEffect, useState } from 'react'
import * as pdf from '../pdf'

/**
 * DEV 冒烟页（?pdfsmoke=1）：用 pdfjs 真实渲染后端 PDF 原件若干页，
 * 验证 worker/渲染链路可用。渲染成功打印 [pdfsmoke] ok pages=N。
 */
export default function PdfSmoke() {
  const [status, setStatus] = useState('loading…')
  const [pages, setPages] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function run() {
      try {
        const file = new URLSearchParams(window.location.search).get('file') ?? '1706.03762v7.pdf'
        const res = await fetch(`/smoke-pdf/${file}`)
        if (!res.ok) throw new Error(`fetch ${res.status}`)
        const bytes = new Uint8Array(await res.arrayBuffer())
        const doc = await pdf.loadDocument(bytes)
        if (cancelled) return
        const n = Math.min(doc.numPages, 3)
        for (let i = 1; i <= n; i++) {
          const page = await doc.getPage(i)
          const canvas = document.getElementById(`smoke-canvas-${i}`) as HTMLCanvasElement | null
          if (canvas) await pdf.renderPageToCanvas(page, canvas, 1)
        }
        if (cancelled) return
        setPages(n)
        setStatus('ok')
        console.log(`[pdfsmoke] ok pages=${n} total=${doc.numPages} file=${file}`)
      } catch (e) {
        if (cancelled) return
        setStatus(`fail: ${e instanceof Error ? e.message : String(e)}`)
        console.log(`[pdfsmoke] fail ${e instanceof Error ? e.message : String(e)}`)
      }
    }
    run()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div style={{ padding: 24 }}>
      <h2>pdfjs 冒烟渲染</h2>
      <p>
        status: <strong>{status}</strong> / rendered pages: {pages}
      </p>
      {[1, 2, 3].map((i) => (
        <canvas key={i} id={`smoke-canvas-${i}`} style={{ border: '1px solid #cbd5e1', marginBottom: 12 }} />
      ))}
    </div>
  )
}
