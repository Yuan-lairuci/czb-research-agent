import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Empty, Input, InputNumber, Segmented, Select, Space, Tag, message } from 'antd'
import * as echarts from 'echarts'
import * as api from '../api'
import { ErrorNotice } from './ModelNotice'
import type {
  ChartResponse,
  ChartType,
  DiagramKind,
  DiagramResponse,
  OutlineResponse,
  RefStyle,
  ReferencesResponse,
} from '../types'

const STYLE_OPTIONS = [
  { value: 'plain', label: '纯文本' },
  { value: 'apa', label: 'APA' },
  { value: 'gbt7714', label: 'GB/T 7714' },
]
const CHART_OPTIONS = [
  { value: 'line', label: '折线图' },
  { value: 'bar', label: '柱状图' },
  { value: 'radar', label: '雷达图' },
  { value: 'scatter', label: '散点图' },
]
const KIND_OPTIONS = [
  { value: 'tikz', label: 'TikZ' },
  { value: 'graphviz', label: 'Graphviz' },
  { value: 'mermaid', label: 'Mermaid' },
]

/** 写作 Copilot 视图：大纲 / 参考文献核验分栏 / 图表代码 / 图表渲染 / 中译英润色 */
export default function WritingView() {
  // —— 研究大纲 ——
  const [idea, setIdea] = useState('')
  const [outline, setOutline] = useState<OutlineResponse | null>(null)
  const [outlineBusy, setOutlineBusy] = useState(false)
  const [outlineErr, setOutlineErr] = useState<unknown>(null)

  // —— 参考文献 ——
  const [topic, setTopic] = useState('')
  const [style, setStyle] = useState<RefStyle>('gbt7714')
  const [refLimit, setRefLimit] = useState(15)
  const [refs, setRefs] = useState<ReferencesResponse | null>(null)
  const [refsBusy, setRefsBusy] = useState(false)
  const [refsErr, setRefsErr] = useState<unknown>(null)

  // —— 图表代码 ——
  const [kind, setKind] = useState<DiagramKind>('mermaid')
  const [desc, setDesc] = useState('')
  const [diagram, setDiagram] = useState<DiagramResponse | null>(null)
  const [diagramBusy, setDiagramBusy] = useState(false)
  const [diagramErr, setDiagramErr] = useState<unknown>(null)

  // —— 图表渲染 ——
  const [csvText, setCsvText] = useState('month,accuracy,loss\n1,0.72,0.58\n2,0.81,0.41\n3,0.86,0.33\n4,0.89,0.27')
  const [chartType, setChartType] = useState<ChartType>('line')
  const [chartTitle, setChartTitle] = useState('模型性能随训练轮次变化')
  const [chart, setChart] = useState<ChartResponse | null>(null)
  const [chartBusy, setChartBusy] = useState(false)
  const [chartErr, setChartErr] = useState<unknown>(null)
  const chartElRef = useRef<HTMLDivElement>(null)
  const chartObj = useRef<echarts.ECharts | null>(null)

  // —— 中译英 ——
  const [zhText, setZhText] = useState('')
  const [enOut, setEnOut] = useState('')
  const [enBusy, setEnBusy] = useState(false)
  const [enErr, setEnErr] = useState<unknown>(null)

  useEffect(() => {
    return () => {
      chartObj.current?.dispose()
      chartObj.current = null
    }
  }, [])

  useEffect(() => {
    const el = chartElRef.current
    if (!el || !chart) return
    if (!chartObj.current) chartObj.current = echarts.init(el)
    chartObj.current.setOption(chart.option as echarts.EChartsOption)
    chartObj.current.resize()
  }, [chart])

  const runOutline = useCallback(async () => {
    if (!idea.trim()) return
    setOutlineBusy(true)
    setOutlineErr(null)
    try {
      setOutline(await api.writingOutline(idea.trim()))
    } catch (e) {
      setOutline(null)
      setOutlineErr(e)
    } finally {
      setOutlineBusy(false)
    }
  }, [idea])

  const runRefs = useCallback(async () => {
    const t = topic.trim()
    if (!t) return
    setRefsBusy(true)
    setRefsErr(null)
    try {
      setRefs(await api.writingReferences({ topic: t, style, limit: refLimit }))
    } catch (e) {
      setRefs(null)
      setRefsErr(e)
    } finally {
      setRefsBusy(false)
    }
  }, [topic, style, refLimit])

  const runDiagram = useCallback(async () => {
    const d = desc.trim()
    if (!d) return
    setDiagramBusy(true)
    setDiagramErr(null)
    try {
      setDiagram(await api.writingDiagram(kind, d))
    } catch (e) {
      setDiagram(null)
      setDiagramErr(e)
    } finally {
      setDiagramBusy(false)
    }
  }, [desc, kind])

  const runChart = useCallback(async () => {
    if (!csvText.trim()) return
    setChartBusy(true)
    setChartErr(null)
    try {
      setChart(await api.writingChart({ csv_text: csvText, chart_type: chartType, title: chartTitle.trim() || undefined }))
    } catch (e) {
      setChart(null)
      setChartErr(e)
    } finally {
      setChartBusy(false)
    }
  }, [csvText, chartType, chartTitle])

  const runZh2En = useCallback(async () => {
    const t = zhText.trim()
    if (!t) return
    setEnBusy(true)
    setEnErr(null)
    try {
      const res = await api.writingZh2En(t)
      setEnOut(res.translated)
    } catch (e) {
      setEnOut('')
      setEnErr(e)
    } finally {
      setEnBusy(false)
    }
  }, [zhText])

  const copyText = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制到剪贴板')
    } catch {
      message.error('复制失败，请手动选择文本')
    }
  }, [])

  return (
    <div className="view view-writing">
      <section className="view-section">
        <h3 className="section-title">研究大纲生成</h3>
        <Input.TextArea
          rows={2}
          value={idea}
          placeholder="输入研究想法，例如：面向长文档的检索增强生成方法"
          onChange={(e) => setIdea(e.target.value)}
        />
        <Space className="writing-actions">
          <Button type="primary" loading={outlineBusy} disabled={!idea.trim()} onClick={() => void runOutline()}>
            生成六段式大纲
          </Button>
        </Space>
        {outlineErr ? <ErrorNotice error={outlineErr} /> : null}
        {outline && (
          <div className="outline-grid">
            {outline.sections.map((sec, i) => (
              <div className="outline-card" key={i}>
                <div className="outline-card-title">
                  <Tag color="blue">{i + 1}</Tag>
                  {sec.title}
                </div>
                <ul>
                  {sec.points.map((p, j) => (
                    <li key={j}>{p}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="view-section">
        <h3 className="section-title">参考文献核验</h3>
        <Space.Compact className="writing-bar">
          <Input
            value={topic}
            placeholder="输入主题，如 retrieval augmented generation"
            onChange={(e) => setTopic(e.target.value)}
            onPressEnter={() => void runRefs()}
          />
          <Select value={style} onChange={(v) => setStyle(v as RefStyle)} options={STYLE_OPTIONS} style={{ width: 110 }} />
          <InputNumber min={1} max={50} value={refLimit} onChange={(v) => setRefLimit(v ?? 15)} style={{ width: 76 }} addonAfter="条" />
          <Button type="primary" loading={refsBusy} disabled={!topic.trim()} onClick={() => void runRefs()}>
            生成
          </Button>
        </Space.Compact>
        {refsErr ? <ErrorNotice error={refsErr} /> : null}
        {refs && (
          <>
            <div className="rec-meta">
              {refs.sources_used.map((s) => (
                <Tag key={s} color="cyan">{s}</Tag>
              ))}
            </div>
            <div className="refs-columns">
              <div className="refs-col refs-verified">
                <h4>已核验（{refs.references.length}）</h4>
                {refs.references.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无核验通过条目" />
                ) : (
                  refs.references.map((r, i) => (
                    <div className="ref-item" key={i}>
                      <div className="ref-formatted">{r.formatted}</div>
                      <div className="ref-meta">
                        {r.authors.length > 0 && <Tag>{r.authors.slice(0, 3).join(', ')}{r.authors.length > 3 ? ' 等' : ''}</Tag>}
                        {r.year && <Tag>{r.year}</Tag>}
                        {r.venue && <Tag color="geekblue">{r.venue}</Tag>}
                        <Tag color="default">{r.source}</Tag>
                        <Button size="small" type="text" onClick={() => void copyText(r.formatted)}>
                          复制
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </div>
              <div className="refs-col refs-dropped">
                <h4>已剔除（{refs.dropped.length}）</h4>
                {refs.dropped.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无剔除条目" />
                ) : (
                  refs.dropped.map((d, i) => (
                    <div className="ref-item ref-item-dropped" key={i}>
                      <div className="ref-formatted">{d.title || d.arxiv_id || d.doi || '（未知条目）'}</div>
                      <div className="ref-meta">
                        <Tag color="red">{d.reason}</Tag>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </>
        )}
      </section>

      <section className="view-section">
        <h3 className="section-title">图表代码生成</h3>
        <Space.Compact className="writing-bar">
          <Segmented value={kind} onChange={(v) => setKind(v as DiagramKind)} options={KIND_OPTIONS} />
          <Input
            value={desc}
            placeholder="描述图表，如：三个模块组成的检索增强流程，依次为检索、重排、生成"
            onChange={(e) => setDesc(e.target.value)}
            onPressEnter={() => void runDiagram()}
          />
          <Button type="primary" loading={diagramBusy} disabled={!desc.trim()} onClick={() => void runDiagram()}>
            生成
          </Button>
        </Space.Compact>
        {diagramErr ? <ErrorNotice error={diagramErr} /> : null}
        {diagram && (
          <div className="diagram-box">
            <div className="diagram-head">
              <Tag color="blue">{diagram.kind}</Tag>
              <Button size="small" onClick={() => void copyText(diagram.code)}>复制代码</Button>
            </div>
            <pre className="diagram-code">{diagram.code}</pre>
            <div className="diagram-hint">{diagram.usage_hint}</div>
          </div>
        )}
      </section>

      <section className="view-section">
        <h3 className="section-title">数据图表（ECharts 渲染）</h3>
        <div className="chart-form">
          <Input.TextArea
            rows={4}
            value={csvText}
            placeholder="粘贴 CSV 数据，首行为表头"
            onChange={(e) => setCsvText(e.target.value)}
            className="chart-csv"
          />
          <Space direction="vertical" className="chart-ctrl">
            <Select value={chartType} onChange={(v) => setChartType(v as ChartType)} options={CHART_OPTIONS} style={{ width: 120 }} />
            <Input value={chartTitle} placeholder="图表标题（Figure Caption 用）" onChange={(e) => setChartTitle(e.target.value)} />
            <Button type="primary" loading={chartBusy} disabled={!csvText.trim()} onClick={() => void runChart()}>
              渲染图表
            </Button>
          </Space>
        </div>
        {chartErr ? <ErrorNotice error={chartErr} /> : null}
        {chart && (
          <div className="chart-result">
            <div className="chart-canvas" ref={chartElRef} />
            <div className="chart-caption">Figure Caption：{chart.caption}</div>
          </div>
        )}
      </section>

      <section className="view-section">
        <h3 className="section-title">中译英学术润色</h3>
        <Input.TextArea
          rows={3}
          value={zhText}
          placeholder="粘贴中文摘要或段落，输出学术英文"
          onChange={(e) => setZhText(e.target.value)}
        />
        <Space className="writing-actions">
          <Button type="primary" loading={enBusy} disabled={!zhText.trim()} onClick={() => void runZh2En()}>
            润色翻译
          </Button>
          {enOut && <Button onClick={() => void copyText(enOut)}>复制结果</Button>}
        </Space>
        {enErr ? <ErrorNotice error={enErr} /> : null}
        {enOut && <div className="sel-result">{enOut}</div>}
      </section>
    </div>
  )
}
