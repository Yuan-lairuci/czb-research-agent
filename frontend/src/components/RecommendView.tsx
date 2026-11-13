import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Empty, Input, InputNumber, Select, Space, Tag } from 'antd'
import * as api from '../api'
import { ErrorNotice } from './ModelNotice'
import type {
  ByPaperResponse,
  Candidate,
  FetchResponse,
  PaperInfo,
  SearchResponse,
  TrackItem,
  TrackRunResponse,
} from '../types'

interface Props {
  papers: PaperInfo[]
}

function candidateKey(c: Candidate): string {
  return c.s2_id || c.arxiv_id || c.doi || c.title
}

/** 检索推荐视图：学术检索 / 一键入库 / 相似文献 / arXiv 版本追踪 */
export default function RecommendView({ papers }: Props) {
  // —— 学术检索 ——
  const [query, setQuery] = useState('large language model reasoning')
  const [limit, setLimit] = useState(10)
  const [searching, setSearching] = useState(false)
  const [searchRes, setSearchRes] = useState<SearchResponse | null>(null)
  const [searchErr, setSearchErr] = useState<unknown>(null)
  const [feedbackMap, setFeedbackMap] = useState<Record<string, 'like' | 'dislike'>>({})
  const [fetchMap, setFetchMap] = useState<Record<string, FetchResponse | string>>({})

  // —— 相似文献推荐 ——
  const [byId, setById] = useState('')
  const [topK, setTopK] = useState(10)
  const [byLoading, setByLoading] = useState(false)
  const [byRes, setByRes] = useState<ByPaperResponse | null>(null)
  const [byErr, setByErr] = useState<unknown>(null)

  // —— arXiv 版本追踪 ——
  const [trackId, setTrackId] = useState('')
  const [items, setItems] = useState<TrackItem[]>([])
  const [trackBusy, setTrackBusy] = useState(false)
  const [trackErr, setTrackErr] = useState<unknown>(null)
  const [runRes, setRunRes] = useState<TrackRunResponse | null>(null)

  const refreshTracks = useCallback(async () => {
    try {
      const res = await api.trackList()
      setItems(res.items)
      setTrackErr(null)
    } catch (e) {
      setTrackErr(e)
    }
  }, [])

  useEffect(() => {
    void refreshTracks()
  }, [refreshTracks])

  const runSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    setSearchErr(null)
    try {
      setSearchRes(await api.recommendSearch(q, limit))
    } catch (e) {
      setSearchRes(null)
      setSearchErr(e)
    } finally {
      setSearching(false)
    }
  }, [query, limit])

  const runFetch = useCallback(async (c: Candidate) => {
    if (!c.arxiv_id) return
    const key = candidateKey(c)
    setFetchMap((m) => ({ ...m, [key]: 'loading' }))
    try {
      const res = await api.recommendFetch(c.arxiv_id)
      setFetchMap((m) => ({ ...m, [key]: res }))
    } catch (e) {
      const msg = e instanceof api.ApiError ? e.message : String(e)
      setFetchMap((m) => ({ ...m, [key]: msg }))
    }
  }, [])

  const runFeedback = useCallback(async (c: Candidate, like: boolean) => {
    const key = candidateKey(c)
    try {
      await api.recommendFeedback(like, { s2_id: c.s2_id ?? undefined, arxiv_id: c.arxiv_id ?? undefined })
      setFeedbackMap((m) => ({ ...m, [key]: like ? 'like' : 'dislike' }))
    } catch {
      /* 反馈失败静默（不影响主流程） */
    }
  }, [])

  const runByPaper = useCallback(async () => {
    const id = byId.trim()
    if (!id) return
    setByLoading(true)
    setByErr(null)
    try {
      setByRes(await api.recommendByPaper(id, topK))
    } catch (e) {
      setByRes(null)
      setByErr(e)
    } finally {
      setByLoading(false)
    }
  }, [byId, topK])

  const addTrack = useCallback(async () => {
    const id = trackId.trim().replace(/^arxiv:/i, '')
    if (!id) return
    setTrackBusy(true)
    setTrackErr(null)
    try {
      await api.trackAdd(id)
      setTrackId('')
      await refreshTracks()
    } catch (e) {
      setTrackErr(e)
    } finally {
      setTrackBusy(false)
    }
  }, [trackId, refreshTracks])

  const runTrack = useCallback(async () => {
    setTrackBusy(true)
    setTrackErr(null)
    try {
      setRunRes(await api.trackRun())
      await refreshTracks()
    } catch (e) {
      setTrackErr(e)
    } finally {
      setTrackBusy(false)
    }
  }, [refreshTracks])

  return (
    <div className="view view-recommend">
      <section className="view-section">
        <h3 className="section-title">学术检索</h3>
        <Space.Compact className="rec-search-bar">
          <Input
            value={query}
            placeholder="输入研究主题关键词"
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={() => void runSearch()}
          />
          <InputNumber min={1} max={20} value={limit} onChange={(v) => setLimit(v ?? 10)} style={{ width: 76 }} addonAfter="条" />
          <Button type="primary" loading={searching} onClick={() => void runSearch()}>
            检索
          </Button>
        </Space.Compact>
        {searchErr ? <ErrorNotice error={searchErr} /> : null}
        {searchRes && (
          <>
            <div className="rec-meta">
              <Tag color="blue">共 {searchRes.results.length} 条</Tag>
              <Tag color={searchRes.s2_ok ? 'green' : 'default'}>Semantic Scholar {searchRes.s2_ok ? '可用' : '不可用'}</Tag>
              <Tag color={searchRes.arxiv_ok ? 'green' : 'default'}>arXiv {searchRes.arxiv_ok ? '可用' : '不可用'}</Tag>
              {searchRes.degraded && <Tag color="orange">部分源降级</Tag>}
            </div>
            {searchRes.results.length === 0 ? (
              <Empty description="无结果，换个关键词试试" />
            ) : (
              <div className="rec-list">
                {searchRes.results.map((c) => {
                  const key = candidateKey(c)
                  const fetched = fetchMap[key]
                  return (
                    <div className="rec-card" key={key}>
                      <div className="rec-card-title">{c.title}</div>
                      <div className="rec-card-meta">
                        {c.year && <Tag>{c.year}</Tag>}
                        {typeof c.citation_count === 'number' && <Tag color="gold">被引 {c.citation_count}</Tag>}
                        {typeof c.influential_citation_count === 'number' && c.influential_citation_count > 0 && (
                          <Tag color="volcano">高影响被引 {c.influential_citation_count}</Tag>
                        )}
                        <Tag color={c.source === 'arxiv_only' ? 'default' : 'cyan'}>{c.source}</Tag>
                        {c.github_url && <Tag color="purple">GitHub</Tag>}
                      </div>
                      {c.abstract && <div className="rec-card-abstract">{c.abstract}</div>}
                      <div className="rec-card-actions">
                        <Button size="small" disabled={!c.arxiv_id} loading={fetched === 'loading'} onClick={() => void runFetch(c)}>
                          一键入库
                        </Button>
                        <Button size="small" type={feedbackMap[key] === 'like' ? 'primary' : 'default'} onClick={() => void runFeedback(c, true)}>
                          👍 赞
                        </Button>
                        <Button size="small" danger={feedbackMap[key] === 'dislike'} onClick={() => void runFeedback(c, false)}>
                          👎 踩
                        </Button>
                        {c.pdf_url && (
                          <a href={c.pdf_url} target="_blank" rel="noreferrer" className="rec-link">
                            PDF
                          </a>
                        )}
                      </div>
                      {fetched && fetched !== 'loading' && (
                        <Alert
                          className="rec-fetch-alert"
                          type={typeof fetched === 'string' ? 'error' : fetched.parse_status === 'parsed' ? 'success' : 'warning'}
                          title={
                            typeof fetched === 'string'
                              ? '入库失败'
                              : `入库${fetched.parse_status === 'parsed' ? '成功' : fetched.parse_status === 'duplicate' ? '（重复文献）' : '解析失败'}`
                          }
                          description={
                            typeof fetched === 'string'
                              ? fetched
                              : `${fetched.title ?? fetched.arxiv_id} · ${fetched.num_pages} 页 · ${fetched.detail ?? ''}`
                          }
                        />
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
      </section>

      <section className="view-section">
        <h3 className="section-title">相似文献推荐（按馆藏文献）</h3>
        <Space.Compact className="rec-search-bar">
          <Select
            className="rec-paper-select"
            showSearch
            placeholder="选择文献库文献，或直接粘贴 paper_id / arxiv:<id> / DOI:<doi>"
            value={byId || undefined}
            optionFilterProp="label"
            onChange={(v) => setById(v)}
            options={papers.map((p) => ({ value: p.paper_id, label: p.title }))}
            style={{ minWidth: 320 }}
          />
          <InputNumber min={1} max={20} value={topK} onChange={(v) => setTopK(v ?? 10)} style={{ width: 76 }} addonAfter="条" />
          <Button type="primary" loading={byLoading} onClick={() => void runByPaper()}>
            推荐
          </Button>
        </Space.Compact>
        {byErr ? <ErrorNotice error={byErr} /> : null}
        {byRes && (
          <>
            <div className="rec-meta">
              <Tag>参考文献 {byRes.references_considered}</Tag>
              <Tag>施引文献 {byRes.citations_considered}</Tag>
              <Tag color="default">过滤套话引用 {byRes.dropped_perfunctory}</Tag>
            </div>
            {byRes.recommendations.length === 0 ? (
              <Empty description="无推荐结果（该文献可能缺外部标识）" />
            ) : (
              <div className="rec-list">
                {byRes.recommendations.map((r, i) => (
                  <div className="rec-card" key={r.s2_id || r.arxiv_id || i}>
                    <div className="rec-card-title">{r.title}</div>
                    <div className="rec-card-meta">
                      <Tag color="blue">相关度 {(r.score * 100).toFixed(0)}%</Tag>
                      <Tag color={r.relation === 'reference' ? 'geekblue' : 'magenta'}>{r.relation === 'reference' ? '参考文献' : '施引文献'}</Tag>
                      {r.year && <Tag>{r.year}</Tag>}
                      {typeof r.citation_count === 'number' && <Tag color="gold">被引 {r.citation_count}</Tag>}
                    </div>
                    {r.reasons.length > 0 && (
                      <ul className="rec-reasons">
                        {r.reasons.map((reason, j) => (
                          <li key={j}>{reason}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <section className="view-section">
        <h3 className="section-title">arXiv 版本追踪</h3>
        <Space.Compact className="rec-search-bar">
          <Input
            value={trackId}
            placeholder="arXiv id，如 1706.03762"
            onChange={(e) => setTrackId(e.target.value)}
            onPressEnter={() => void addTrack()}
          />
          <Button type="primary" loading={trackBusy} onClick={() => void addTrack()}>
            添加追踪
          </Button>
          <Button loading={trackBusy} onClick={() => void runTrack()}>
            检查更新
          </Button>
        </Space.Compact>
        {trackErr ? <ErrorNotice error={trackErr} /> : null}
        {runRes && (
          <Alert
            className="rec-track-alert"
            type={runRes.updates.length > 0 ? 'warning' : 'success'}
            title={
              runRes.updates.length > 0
                ? `发现 ${runRes.updates.length} 个版本升级`
                : `已检查 ${runRes.checked} 项，暂无升级`
            }
            description={
              runRes.updates.length > 0 ? (
                <ul className="rec-updates">
                  {runRes.updates.map((u) => (
                    <li key={u.arxiv_id}>
                      <b>{u.title || u.arxiv_id}</b>：v{u.old_version} → v{u.new_version}
                    </li>
                  ))}
                  {runRes.errors.map((e, i) => (
                    <li key={i} className="rec-update-err">{e}</li>
                  ))}
                </ul>
              ) : (
                runRes.errors.length > 0 ? runRes.errors.join('；') : undefined
              )
            }
          />
        )}
        {items.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无追踪条目" />
        ) : (
          <div className="rec-track-list">
            {items.map((it) => (
              <div className="rec-track-item" key={it.arxiv_id}>
                <span className="rec-track-title">{it.title || it.arxiv_id}</span>
                <Tag>{it.arxiv_id}</Tag>
                <Tag color={it.tracked_version !== it.latest_version ? 'orange' : 'green'}>
                  v{it.tracked_version ?? '?'} / 最新 v{it.latest_version ?? '?'}
                </Tag>
                <span className="rec-track-time">加入 {it.added_at?.slice(0, 10)} · 检查 {it.last_checked?.slice(0, 10) ?? '未检查'}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
