import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Empty, Input, Progress, Segmented, Spin, Tag } from 'antd'
import * as api from '../api'
import { ErrorNotice } from './ModelNotice'
import type {
  BlockTranslation,
  PaperInfo,
  PaperTranslateResponse,
  ParsedContract,
  TranslateDirection,
} from '../types'

interface Props {
  paper: PaperInfo | null
  parsed: ParsedContract | null
  selection: { text: string; nonce: number } | null
}

interface BiRow extends BlockTranslation {
  key: string
  page_no: number
}

/** 对照行：优先取翻译接口结果；无结果时用解析契约原文占位（降级演示不空屏） */
function buildRows(pt: PaperTranslateResponse | null, parsed: ParsedContract | null): BiRow[] {
  if (pt) {
    const rows: BiRow[] = []
    for (const page of pt.pages) {
      for (const b of page.blocks) {
        rows.push({ ...b, key: `p${page.page_no}-b${b.block_index}`, page_no: page.page_no })
      }
    }
    return rows
  }
  if (parsed) {
    const rows: BiRow[] = []
    for (const page of parsed.pages) {
      page.blocks.forEach((b, i) => {
        if (b.text.trim()) rows.push({ key: `p${page.page_no}-b${i}`, page_no: page.page_no, block_index: i, src_text: b.text, dst_text: '' })
      })
    }
    return rows
  }
  return []
}

/** 右栏·翻译面板：划词翻译（SSE 流式）+ 整篇翻译中英对照（block 对齐、双向同步滚动） */
export default function TranslationPanel({ paper, parsed, selection }: Props) {
  const [direction, setDirection] = useState<TranslateDirection>('en2zh')
  const [selText, setSelText] = useState('')
  const [selOut, setSelOut] = useState('')
  const [selEngine, setSelEngine] = useState('')
  const [selLoading, setSelLoading] = useState(false)
  const [selError, setSelError] = useState<unknown>(null)
  const [pt, setPt] = useState<PaperTranslateResponse | null>(null)
  const [ptLoading, setPtLoading] = useState(false)
  const [ptError, setPtError] = useState<unknown>(null)
  const [degraded, setDegraded] = useState(false)
  const srcColRef = useRef<HTMLDivElement>(null)
  const dstColRef = useRef<HTMLDivElement>(null)
  const syncing = useRef(false)

  // 阅读器划词 → 同步到翻译卡片
  useEffect(() => {
    if (!selection) return
    setSelText(selection.text)
    setSelOut('')
    setSelEngine('')
    setSelError(null)
  }, [selection?.nonce])

  const runSelTranslate = useCallback(async () => {
    const text = selText.trim()
    if (!text) return
    setSelLoading(true)
    setSelOut('')
    setSelEngine('')
    setSelError(null)
    try {
      const res = await api.translateTextStream(text, direction, (_delta, full) => setSelOut(full))
      setSelOut(res.translated || res.translated === '' ? res.translated : selOut)
      setSelEngine(res.engine)
    } catch (e) {
      setSelError(e)
    } finally {
      setSelLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selText, direction])

  const runPaperTranslate = useCallback(
    async (fill: boolean) => {
      if (!paper) return
      setPtLoading(true)
      setPtError(null)
      try {
        const res = await api.translatePaper(paper.paper_id, direction, fill)
        setPt(res)
        setDegraded(false)
      } catch (e) {
        // 模型未配置：降级为原文对照行布局，右列标注占位
        if (api.isModelUnavailable(e)) {
          setPt(null)
          setDegraded(true)
        } else {
          setPtError(e)
        }
      } finally {
        setPtLoading(false)
      }
    },
    [paper, direction],
  )

  const rows = useMemo(() => buildRows(pt, parsed), [pt, parsed])

  // 双向同步滚动：按对照行对齐（block_index 同序渲染）
  const syncScroll = useCallback((from: HTMLDivElement, to: HTMLDivElement) => {
    if (syncing.current) return
    syncing.current = true
    const fromRows = Array.from(from.querySelectorAll<HTMLElement>('.bi-row'))
    const toRows = Array.from(to.querySelectorAll<HTMLElement>('.bi-row'))
    if (fromRows.length > 0 && toRows.length > 0) {
      const top = from.scrollTop
      let idx = 0
      for (let i = 0; i < fromRows.length; i++) {
        if (fromRows[i].offsetTop <= top + 6) idx = i
        else break
      }
      const dest = toRows[Math.min(idx, toRows.length - 1)]
      if (dest) to.scrollTop = Math.max(dest.offsetTop - 6, 0)
    }
    requestAnimationFrame(() => {
      syncing.current = false
    })
  }, [])

  const lastPage = rows.length > 0 ? rows[rows.length - 1].page_no : 0
  const progress = pt ? (pt.total_pages > 0 ? Math.round((pt.translated_pages / pt.total_pages) * 100) : 0) : 0

  return (
    <section className="panel panel-translate">
      <div className="panel-head">
        <span className="panel-title">翻译对照</span>
        <Segmented
          size="small"
          value={direction}
          onChange={(v) => setDirection(v as TranslateDirection)}
          options={[
            { label: '英 → 中', value: 'en2zh' },
            { label: '中 → 英', value: 'zh2en' },
          ]}
        />
        <div className="translate-actions">
          <Button size="small" type="primary" loading={ptLoading} disabled={!paper} onClick={() => void runPaperTranslate(true)}>
            整篇翻译
          </Button>
          <Button size="small" loading={ptLoading} disabled={!paper} onClick={() => void runPaperTranslate(false)}>
            刷新进度
          </Button>
        </div>
      </div>

      <div className="translate-body">
        <div className="translate-sel">
          <div className="sel-label">划词翻译</div>
          <Input.TextArea
            className="sel-input"
            rows={2}
            value={selText}
            placeholder="在左侧阅读器中选中文字，即出现在这里；也可直接粘贴"
            onChange={(e) => setSelText(e.target.value)}
          />
          <div className="sel-foot">
            <Button size="small" type="primary" loading={selLoading} disabled={!selText.trim()} onClick={() => void runSelTranslate()}>
              翻译
            </Button>
            {selEngine && <Tag color="blue">引擎：{selEngine}</Tag>}
            {selLoading && <Spin size="small" />}
          </div>
          {selError ? (
            <ErrorNotice error={selError} />
          ) : (
            selOut !== '' && <div className="sel-result">{selOut}</div>
          )}
        </div>

        <div className="translate-progress">
          {pt && (
            <>
              <Progress percent={progress} size="small" status={pt.done ? 'success' : 'active'} />
              <span className="progress-text">
                已译 {pt.translated_pages}/{pt.total_pages} 页 {pt.done ? '· 完成' : ''}
              </span>
            </>
          )}
          {degraded && <span className="progress-text">模型服务未配置，下列对照仅展示原文（block 对齐布局）</span>}
          {!pt && !degraded && <span className="progress-text">点击「整篇翻译」生成中英对照（缺页自动补译）</span>}
        </div>

        {ptError ? <ErrorNotice error={ptError} /> : null}

        {rows.length === 0 ? (
          <Empty
            className="translate-empty"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={paper ? '暂无可对照内容' : '请先在文献库选择一篇文献'}
          />
        ) : (
          <div className="bilingual">
            <div
              className="bilingual-col bilingual-src"
              ref={srcColRef}
              onScroll={() => srcColRef.current && dstColRef.current && syncScroll(srcColRef.current, dstColRef.current)}
            >
              {rows.map((row) => (
                <div className="bi-row" key={row.key}>
                  {row.block_index === 0 && <div className="bi-page">第 {row.page_no} 页</div>}
                  <div className="bi-src">{row.src_text}</div>
                </div>
              ))}
            </div>
            <div
              className="bilingual-col bilingual-dst"
              ref={dstColRef}
              onScroll={() => dstColRef.current && srcColRef.current && syncScroll(dstColRef.current, srcColRef.current)}
            >
              {rows.map((row) => (
                <div className="bi-row" key={row.key}>
                  {row.block_index === 0 && <div className="bi-page">第 {row.page_no} 页</div>}
                  {row.dst_text ? (
                    <div className="bi-dst">{row.dst_text}</div>
                  ) : (
                    <div className="bi-dst bi-pending">{degraded ? '模型服务未配置' : '待翻译'}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
        {lastPage > 0 && rows.length > 0 && <div className="bi-hint">共 {lastPage} 页 · {rows.length} 个对照行 · 双向滚动同步</div>}
      </div>
    </section>
  )
}
