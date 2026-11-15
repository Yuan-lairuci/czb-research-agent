import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Empty, Input, Segmented, Spin, Tabs } from 'antd'
import * as api from '../api'
import * as pdf from '../pdf'
import { buildCitationIndex, splitByCitations } from '../citations'
import type { JumpRequest, PaperInfo, ParsedContract } from '../types'
import type { PDFDocumentProxy } from 'pdfjs-dist'

interface Props {
  paper: PaperInfo | null
  parsed: ParsedContract | null
  jump: JumpRequest | null
  onJumpConsumed: () => void
  /** 划词选中文本回调（翻译面板数据源） */
  onSelectText?: (text: string) => void
}

interface HeadingNode {
  text: string
  level: number
  pageNo: number
}

/** 文本层里把 [n] 形态标记变成可点击引用 */
function wrapLayerCitations(container: HTMLElement, onRefClick: (n: number) => void) {
  for (const span of Array.from(container.querySelectorAll('span'))) {
    const text = span.textContent ?? ''
    if (!/\[\d/.test(text)) continue
    const segments = splitByCitations(text)
    if (!segments.some((s) => s.refs)) continue
    span.textContent = ''
    for (const seg of segments) {
      if (seg.refs && seg.refs.length > 0) {
        const link = document.createElement('a')
        link.className = 'cite-marker'
        link.textContent = seg.text
        link.addEventListener('click', (e) => {
          e.preventDefault()
          e.stopPropagation()
          onRefClick(seg.refs![0])
        })
        span.appendChild(link)
      } else {
        span.appendChild(document.createTextNode(seg.text))
      }
    }
  }
}

/** 中栏·PDF 阅读器：pdfjs 渲染 / 页导航 / 标题树 / 引用双向联动 */
export default function ReaderPanel({ paper, parsed, jump, onJumpConsumed, onSelectText }: Props) {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [pdfLoading, setPdfLoading] = useState(false)
  const [pdfNote, setPdfNote] = useState<string | null>(null)
  const [mode, setMode] = useState<'pdf' | 'text'>('pdf')
  const [pageNo, setPageNo] = useState(1)
  const [scale, setScale] = useState(1.2)
  const [flashNonce, setFlashNonce] = useState(0)
  const [highlightRef, setHighlightRef] = useState<number | null>(null)
  const [sideTab, setSideTab] = useState<'outline' | 'refs'>('outline')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const layerRef = useRef<HTMLDivElement>(null)

  const citationIndex = useMemo(() => (parsed ? buildCitationIndex(parsed) : null), [parsed])
  const headings: HeadingNode[] = useMemo(() => {
    if (!parsed) return []
    const nodes: HeadingNode[] = []
    for (const page of parsed.pages) {
      for (const block of page.blocks) {
        if (block.type === 'heading' && block.text) {
          nodes.push({ text: block.text, level: block.level ?? 1, pageNo: page.page_no })
        }
      }
    }
    return nodes
  }, [parsed])

  const totalPages = Math.max(pdfDoc?.numPages ?? 0, parsed?.num_pages ?? 0)
  const currentPage = Math.min(Math.max(pageNo, 1), Math.max(totalPages, 1))

  const gotoPage = useCallback(
    (n: number) => {
      setPageNo(Math.min(Math.max(n, 1), Math.max(totalPages, 1)))
      setFlashNonce((x) => x + 1)
    },
    [totalPages],
  )

  // 载入 PDF 原件：本会话缓存 → 后端文件接口 → 元数据接口；都拿不到降级文本视图
  useEffect(() => {
    let cancelled = false
    setPdfDoc(null)
    setPdfNote(null)
    setPageNo(1)
    if (!paper) return
    setPdfLoading(true)
    api
      .fetchPaperPdf(paper.paper_id)
      .then(async (bytes) => {
        if (cancelled) return
        if (!bytes) {
          setMode('text')
          setPdfNote('当前后端未提供 PDF 原件下载，已切换解析文本视图（标题树/引用联动可用）')
          setPdfLoading(false)
          return
        }
        const doc = await pdf.loadDocument(bytes)
        if (cancelled) return
        setPdfDoc(doc)
        setMode('pdf')
        setPdfLoading(false)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setMode('text')
        setPdfNote(`PDF 渲染失败：${e instanceof Error ? e.message : '未知错误'}，已切换解析文本视图`)
        setPdfLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [paper])

  /** 文本层按 canvas 实际显示尺寸等比缩放（.pdf-canvas 的 max-width 会 CSS 缩小画布）。 */
  const syncLayer = useCallback((vw: number, vh: number) => {
    const canvas = canvasRef.current
    const layer = layerRef.current
    if (!canvas || !layer || vw <= 0) return
    const k = canvas.clientWidth / vw
    layer.style.width = vw + 'px'
    layer.style.height = vh + 'px'
    layer.style.transform = 'scale(' + k + ')'
  }, [])

  // 窗口尺寸变化后重新同步文本层缩放
  useEffect(() => {
    const onResize = () => {
      const canvas = canvasRef.current
      if (canvas && mode === 'pdf') syncLayer(canvas.width, canvas.height)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [mode, syncLayer])

  // 渲染当前页（canvas + 可选文本层）
  useEffect(() => {
    if (!pdfDoc || mode !== 'pdf') return
    let cancelled = false
    pdfDoc
      .getPage(Math.min(Math.max(currentPage, 1), pdfDoc.numPages))
      .then(async (page) => {
        if (cancelled || !canvasRef.current) return
        await pdf.renderPageToCanvas(page, canvasRef.current, scale)
        if (cancelled || !layerRef.current) return
        layerRef.current.innerHTML = ''
        const viewport = page.getViewport({ scale })
        const ok = await pdf.renderTextLayer(page, layerRef.current, viewport)
        syncLayer(viewport.width, viewport.height)
        if (ok && !cancelled) wrapLayerCitations(layerRef.current, focusReference)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfDoc, currentPage, scale, mode])

  // 消费跨面板跳页指令（QA source 卡片点击）
  useEffect(() => {
    if (!jump || jump.paperId !== paper?.paper_id) return
    gotoPage(jump.pageNo)
    onJumpConsumed()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump?.nonce])

  // 高亮引用条目滚动进侧栏视野
  useEffect(() => {
    if (highlightRef === null) return
    document.getElementById(`ref-item-${highlightRef}`)?.scrollIntoView({ block: 'nearest' })
  }, [highlightRef])

  const focusReference = useCallback((n: number) => {
    setSideTab('refs')
    setHighlightRef(n)
  }, [])

  // 划词：mouseup 时取当前选中文本交翻译面板
  const handleMouseUp = useCallback(() => {
    if (!onSelectText) return
    const text = (window.getSelection()?.toString() ?? '').trim()
    if (text.length >= 2) onSelectText(text)
  }, [onSelectText])

  const jumpToReference = useCallback(
    (index: number) => {
      setHighlightRef(index)
      const hit = citationIndex?.firstOccurrence.get(index)
      if (hit) gotoPage(hit.pageNo)
    },
    [citationIndex, gotoPage],
  )

  if (!paper) {
    return (
      <main className="panel panel-reader">
        <div className="reader-empty">
          <Empty description="尚未选择文献">
            <span className="empty-hint">从左侧文献库选择一篇文献，或上传 PDF 开始阅读</span>
          </Empty>
        </div>
      </main>
    )
  }

  return (
    <main className="panel panel-reader" onMouseUp={handleMouseUp}>
      <div className="reader-head">
        <div className="reader-title" title={paper.title}>
          {paper.title || paper.source_filename}
        </div>
        <div className="reader-toolbar">
          <Segmented
            size="small"
            value={mode}
            onChange={(v) => setMode(v as 'pdf' | 'text')}
            options={[
              { label: 'PDF 原版', value: 'pdf', disabled: !pdfDoc },
              { label: '文本视图', value: 'text', disabled: !parsed },
            ]}
          />
          <div className="page-nav">
            <Button size="small" disabled={currentPage <= 1} onClick={() => gotoPage(currentPage - 1)}>
              上一页
            </Button>
            <Input
              size="small"
              className="page-input"
              value={currentPage}
              onChange={(e) => {
                const n = Number(e.target.value.replace(/\D/g, ''))
                if (Number.isFinite(n) && n >= 1) setPageNo(n)
              }}
              onPressEnter={(e) => gotoPage(Number((e.target as HTMLInputElement).value))}
            />
            <span className="page-total">/ {totalPages || '—'}</span>
            <Button size="small" disabled={currentPage >= totalPages} onClick={() => gotoPage(currentPage + 1)}>
              下一页
            </Button>
          </div>
          {mode === 'pdf' && (
            <div className="zoom-nav">
              <Button size="small" disabled={scale <= 0.6} onClick={() => setScale((s) => Math.max(0.6, s - 0.2))}>
                −
              </Button>
              <span className="zoom-label">{Math.round(scale * 100)}%</span>
              <Button size="small" disabled={scale >= 2.4} onClick={() => setScale((s) => Math.min(2.4, s + 0.2))}>
                +
              </Button>
            </div>
          )}
        </div>
      </div>

      {pdfNote && <div className="reader-note">{pdfNote}</div>}

      <div className="reader-body">
        <aside className="reader-side">
          <Tabs
            size="small"
            activeKey={sideTab}
            onChange={(k) => setSideTab(k as 'outline' | 'refs')}
            items={[
              {
                key: 'outline',
                label: `目录 ${headings.length ? `${headings.length}` : ''}`,
                children: (
                  <div className="outline-tree">
                    {headings.length === 0 && <span className="side-empty">解析结果中无标题结构</span>}
                    {headings.map((h, i) => (
                      <button
                        key={`${h.pageNo}-${i}`}
                        type="button"
                        className={`outline-item level-${Math.min(h.level, 4)} ${h.pageNo === currentPage ? 'active' : ''}`}
                        onClick={() => gotoPage(h.pageNo)}
                        title={`第 ${h.pageNo} 页`}
                      >
                        <span className="outline-text">{h.text}</span>
                        <span className="outline-page">p{h.pageNo}</span>
                      </button>
                    ))}
                  </div>
                ),
              },
              {
                key: 'refs',
                label: `参考文献 ${parsed?.references.length ? `${parsed.references.length}` : ''}`,
                children: (
                  <div className="ref-list">
                    {(parsed?.references ?? []).length === 0 && <span className="side-empty">解析结果中无参考文献</span>}
                    {(parsed?.references ?? []).map((ref) => {
                      const count = citationIndex?.counts.get(ref.index)
                      return (
                        <button
                          key={ref.index}
                          id={`ref-item-${ref.index}`}
                          type="button"
                          className={`ref-item ${highlightRef === ref.index ? 'highlight' : ''}`}
                          onClick={() => jumpToReference(ref.index)}
                          title={count ? `正文引用 ${count} 次，点击跳回首次出现页` : '正文中未发现该引用标记'}
                        >
                          <span className="ref-index">[{ref.index}]</span>
                          <span className="ref-raw">{ref.raw}</span>
                          {count ? <span className="ref-count">×{count}</span> : null}
                        </button>
                      )
                    })}
                  </div>
                ),
              },
            ]}
          />
        </aside>

        <section className="reader-stage">
          {pdfLoading && (
            <div className="reader-empty">
              <Spin tip="PDF 加载中…" />
            </div>
          )}
          {!pdfLoading && mode === 'pdf' && (
            <div className="pdf-page" key={`page-${currentPage}-${scale}`}>
              <div className="pdf-canvas-wrap">
                <canvas ref={canvasRef} className="pdf-canvas" />
                <div ref={layerRef} className="textLayer" />
              </div>
              {flashNonce > 0 && <div key={flashNonce} className="page-flash" />}
            </div>
          )}
          {!pdfLoading && mode === 'text' && (
            <div className="text-view" key={`text-${currentPage}-${flashNonce}`}>
              {flashNonce > 0 && <div className="page-flash" />}
              {!parsed && <Spin tip="解析内容加载中…" />}
              {parsed &&
                (parsed.pages.find((p) => p.page_no === currentPage)?.blocks ?? []).map((block, i) => (
                  <div key={i} className={`text-block block-${block.type}`}>
                    {block.type === 'heading' ? (
                      <div className={`text-heading level-${Math.min(block.level ?? 1, 4)}`}>{block.text}</div>
                    ) : (
                      <p>
                        {splitByCitations(block.text).map((seg, j) =>
                          seg.refs && seg.refs.length > 0 ? (
                            <a
                              key={j}
                              className="cite-marker"
                              onClick={(e) => {
                                e.preventDefault()
                                focusReference(seg.refs![0])
                              }}
                              href={`#ref-item-${seg.refs[0]}`}
                            >
                              {seg.text}
                            </a>
                          ) : (
                            <span key={j}>{seg.text}</span>
                          ),
                        )}
                      </p>
                    )}
                  </div>
                ))}
              {parsed && (parsed.pages.find((p) => p.page_no === currentPage)?.blocks ?? []).length === 0 && (
                <Empty description="本页无可展示文本" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
