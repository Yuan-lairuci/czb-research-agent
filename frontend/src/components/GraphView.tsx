import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Badge, Button, Empty, Modal, Select, Space, Tag } from 'antd'
import * as echarts from 'echarts'
import * as api from '../api'
import { splitByCitations } from '../citations'
import { ErrorNotice } from './ModelNotice'
import type {
  CitationGraph,
  CitationRef,
  FutureWorkResponse,
  GraphAnalyzeResponse,
  NodeMetrics,
  PaperInfo,
  ReviewResponse,
} from '../types'

interface Props {
  papers: PaperInfo[]
}

const STATUS_BADGE: Record<string, 'success' | 'error' | 'warning'> = {
  solved: 'success',
  unresolved: 'error',
  partially: 'warning',
}
const STATUS_TEXT: Record<string, string> = {
  solved: '已解决',
  unresolved: '未解决',
  partially: '部分解决',
}

/** 引用图谱 + 综述视图：构建→分析→ECharts 力导向图→综述引用回看→未来工作时间线 */
export default function GraphView({ papers }: Props) {
  const [pickIds, setPickIds] = useState<string[]>([])
  const [graph, setGraph] = useState<CitationGraph | null>(null)
  const [analysis, setAnalysis] = useState<GraphAnalyzeResponse | null>(null)
  const [review, setReview] = useState<ReviewResponse | null>(null)
  const [future, setFuture] = useState<FutureWorkResponse | null>(null)
  const [selected, setSelected] = useState<NodeMetrics | null>(null)
  const [refModal, setRefModal] = useState<CitationRef | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const chartRef = useRef<HTMLDivElement>(null)
  const chartObj = useRef<echarts.ECharts | null>(null)

  const metricMap = useMemo(() => {
    const map: Record<string, NodeMetrics> = {}
    for (const m of analysis?.metrics ?? graph?.metrics ?? []) map[m.paper_id] = m
    return map
  }, [analysis, graph])

  const runBuild = useCallback(async () => {
    setBusy('build')
    setError(null)
    try {
      const res = await api.graphBuild(pickIds.length > 0 ? pickIds : undefined)
      const g = await api.graphGet(res.graph_id)
      setGraph(g)
      setAnalysis(null)
      setReview(null)
      setFuture(null)
      setSelected(null)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }, [pickIds])

  const runAnalyze = useCallback(async () => {
    if (!graph) return
    setBusy('analyze')
    setError(null)
    try {
      setAnalysis(await api.graphAnalyze(graph.graph_id))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }, [graph])

  const runReview = useCallback(async () => {
    if (!graph) return
    setBusy('review')
    setError(null)
    try {
      setReview(await api.graphReview(graph.graph_id))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }, [graph])

  const runFuture = useCallback(async () => {
    if (!graph) return
    setBusy('future')
    setError(null)
    try {
      setFuture(await api.graphFutureWork(graph.graph_id))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }, [graph])

  // —— ECharts 力导向图：节点大小=PageRank，双色=基石/边缘，hover 指标，点击选中 ——
  useEffect(() => {
    const el = chartRef.current
    if (!el || !graph) return
    if (!chartObj.current) chartObj.current = echarts.init(el)
    const chart = chartObj.current
    const maxPr = Math.max(...graph.nodes.map((n) => metricMap[n.paper_id]?.pagerank ?? 0), 1e-9)
    const data = graph.nodes.map((n) => {
      const m = metricMap[n.paper_id]
      const isCore = m?.category === '基石论文'
      return {
        id: n.paper_id,
        name: n.title,
        value: m?.pagerank ?? 0,
        symbolSize: 14 + ((m?.pagerank ?? 0) / maxPr) * 30,
        category: isCore ? 0 : 1,
        itemStyle: { color: isCore ? '#2563eb' : '#f59e0b' },
        metrics: m,
      }
    })
    const option: echarts.EChartsOption = {
      tooltip: {
        formatter: (p: unknown) => {
          const params = p as { data?: { name?: string; metrics?: NodeMetrics } }
          const m = params.data?.metrics
          return m
            ? `<b>${params.data?.name ?? ''}</b><br/>PageRank: ${m.pagerank.toFixed(4)} · 度中心性: ${m.degree_centrality.toFixed(3)}<br/>中介中心性: ${m.betweenness_centrality.toFixed(3)} · ${m.category}`
            : params.data?.name ?? ''
        },
      },
      legend: { data: ['基石论文', '边缘衍生论文'], top: 8 },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          force: { repulsion: 320, edgeLength: 110, gravity: 0.08 },
          label: { show: true, position: 'right', fontSize: 10, color: '#334155', formatter: '{b}' },
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 6,
          lineStyle: { color: '#94a3b8', width: 1.2, curveness: 0.12, opacity: 0.8 },
          categories: [{ name: '基石论文' }, { name: '边缘衍生论文' }],
          data,
          edges: graph.edges.map((e) => ({ source: e.source, target: e.target })),
        },
      ],
    }
    chart.setOption(option)
    chart.on('click', (params) => {
      const p = params as { dataType?: string; data?: { id?: string } }
      if (p.dataType === 'node' && p.data?.id) {
        setSelected(metricMap[p.data.id] ?? null)
      }
    })
    return () => {
      chart.off('click')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, metricMap])

  useEffect(() => {
    const onResize = () => chartObj.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chartObj.current?.dispose()
      chartObj.current = null
    }
  }, [])

  return (
    <div className="view view-graph">
      <section className="view-section">
        <h3 className="section-title">引用图谱</h3>
        <Space wrap className="graph-actions">
          <Select
            mode="multiple"
            allowClear
            placeholder="选择参与建图的文献（留空=全部馆藏）"
            value={pickIds}
            onChange={setPickIds}
            optionFilterProp="label"
            options={papers.map((p) => ({ value: p.paper_id, label: p.title }))}
            style={{ minWidth: 340 }}
          />
          <Button type="primary" loading={busy === 'build'} onClick={() => void runBuild()}>
            构建图谱
          </Button>
          <Button disabled={!graph} loading={busy === 'analyze'} onClick={() => void runAnalyze()}>
            分析指标
          </Button>
          <Button disabled={!graph} loading={busy === 'review'} onClick={() => void runReview()}>
            生成综述
          </Button>
          <Button disabled={!graph} loading={busy === 'future'} onClick={() => void runFuture()}>
            未来工作
          </Button>
        </Space>
        {error ? <ErrorNotice error={error} /> : null}
        {!graph ? (
          <Empty className="graph-empty" description="点击「构建图谱」从文献库引用关系生成知识图谱" />
        ) : (
          <div className="graph-body">
            <div className="graph-chart-wrap">
              <div className="graph-chart" ref={chartRef} />
              <div className="graph-legend-note">
                节点大小 = PageRank · 蓝色 = 基石论文 · 橙色 = 边缘衍生论文 · 点击节点查看指标
              </div>
            </div>
            <aside className="graph-side">
              <div className="graph-stats">
                <Tag color="blue">图谱 {graph.graph_id.slice(0, 8)}</Tag>
                <Tag>节点 {graph.nodes.length}</Tag>
                <Tag>边 {graph.edges.length}</Tag>
              </div>
              {analysis && (
                <Alert
                  className="graph-interpret"
                  type="info"
                  title="整体解读"
                  description={analysis.overall_interpretation}
                />
              )}
              {selected ? (
                <div className="graph-metrics">
                  <h4>{selected.title}</h4>
                  <div className="metric-row">
                    <span>PageRank</span><b>{selected.pagerank.toFixed(4)}</b>
                  </div>
                  <div className="metric-row">
                    <span>度中心性</span><b>{selected.degree_centrality.toFixed(3)}</b>
                  </div>
                  <div className="metric-row">
                    <span>中介中心性</span><b>{selected.betweenness_centrality.toFixed(3)}</b>
                  </div>
                  <div className="metric-row">
                    <span>分类</span>
                    <Tag color={selected.category === '基石论文' ? 'blue' : 'orange'}>{selected.category}</Tag>
                  </div>
                  <p className="metric-interpret">{selected.interpretation}</p>
                </div>
              ) : (
                <div className="graph-metrics graph-metrics-hint">点击图中节点查看该论文的中心性指标与解读</div>
              )}
            </aside>
          </div>
        )}
      </section>

      {review && (
        <section className="view-section">
          <h3 className="section-title">
            AI 综述
            {review.generated_by === 'template' && <Tag color="orange">模板生成（LLM 降级）</Tag>}
          </h3>
          {review.topic && <p className="review-topic">主题：{review.topic}</p>}
          {review.sections.map((sec, i) => (
            <div className="review-section" key={i}>
              <h4>{sec.heading}</h4>
              <p>
                {splitByCitations(sec.content).map((seg, j) =>
                  seg.refs ? (
                    <span key={j}>
                      {seg.refs.map((n) => (
                        <a
                          key={n}
                          className="review-cite"
                          onClick={() => setRefModal(review.citation_map.find((c) => c.n === n) ?? { n, paper_id: '', title: '未知文献' })}
                        >
                          [{n}]
                        </a>
                      ))}
                    </span>
                  ) : (
                    <span key={j}>{seg.text}</span>
                  ),
                )}
              </p>
            </div>
          ))}
          {review.dropped_citations.length > 0 && (
            <div className="review-dropped">
              未采信引用：{review.dropped_citations.join('；')}
            </div>
          )}
        </section>
      )}

      {future && (
        <section className="view-section">
          <h3 className="section-title">未来工作时间线</h3>
          {future.timeline.length === 0 ? (
            <Empty description="未提取到未来工作条目" />
          ) : (
            <div className="fw-list">
              {future.timeline.map((f, i) => (
                <div className="fw-item" key={i}>
                  <Badge status={STATUS_BADGE[f.status] ?? 'default'} text={STATUS_TEXT[f.status] ?? f.status} />
                  <Tag color="blue">{f.year ?? '—'}</Tag>
                  <div className="fw-body">
                    <div className="fw-item-text">{f.item}</div>
                    <div className="fw-evidence">
                      提出：{f.proposed_by} {f.paper_id ? `· ${f.paper_id.slice(0, 12)}` : ''}
                      {f.evidence ? ` · 依据：${f.evidence}` : ''}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <Modal
        open={!!refModal}
        title={refModal ? `引用 [${refModal.n}]` : ''}
        footer={null}
        onCancel={() => setRefModal(null)}
      >
        {refModal && (
          <div className="ref-modal-body">
            <p><b>{refModal.title}</b></p>
            <p>paper_id：{refModal.paper_id || '（无馆藏记录）'}</p>
          </div>
        )}
      </Modal>
    </div>
  )
}
