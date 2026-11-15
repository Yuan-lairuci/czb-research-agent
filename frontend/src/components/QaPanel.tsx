import { useEffect, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Badge,
  Button,
  Checkbox,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import * as api from '../api'
import type { AskResponse, PaperInfo } from '../types'

interface Props {
  papers: PaperInfo[]
  selectedId: string | null
  indexInfo: Record<string, number>
  onJump: (paperId: string, pageNo: number) => void
}

interface ChatTurn {
  id: number
  question: string
  answer?: string
  response?: AskResponse
  error?: string
  loading: boolean
}

const REFUSAL = '资料中未找到'
const THREAD_KEY = 'czb-qa-thread'

const EXAMPLES = [
  '这篇论文提出了什么架构？',
  '实验部分的主要指标和结果是什么？',
  '论文的作者是谁？来自哪个机构？',
]

/** 会话存档：切换视图/溯源跳转后回到问答，记录不丢 */
function loadThread(): ChatTurn[] {
  try {
    const raw = sessionStorage.getItem(THREAD_KEY)
    if (!raw) return []
    return (JSON.parse(raw) as ChatTurn[]).map((t) => ({ ...t, loading: false }))
  } catch {
    return []
  }
}

/** 右栏·AI 问答：提问 → /api/qa/ask → answer + sources 卡片（多轮记忆） */
export default function QaPanel({ papers, selectedId, indexInfo, onJump }: Props) {
  const { message } = AntdApp.useApp()
  const [question, setQuestion] = useState('')
  const [onlyCurrent, setOnlyCurrent] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>(loadThread)
  const [seq, setSeq] = useState(1)

  useEffect(() => {
    try {
      sessionStorage.setItem(THREAD_KEY, JSON.stringify(turns.filter((t) => !t.loading)))
    } catch {
      /* 存档失败不阻断问答 */
    }
  }, [turns])

  const totalChunks = Object.values(indexInfo).reduce((a, b) => a + b, 0)
  const paperTitle = (id: string) => papers.find((p) => p.paper_id === id)?.title ?? id

  async function ask() {
    const q = question.trim()
    if (!q) return
    const id = seq
    setSeq((n) => n + 1)
    setQuestion('')
    setTurns((xs) => [...xs, { id, question: q, loading: true }])
    try {
      const response = await api.askQaStream(
        {
          question: q,
          paper_ids: onlyCurrent && selectedId ? [selectedId] : undefined,
          top_k: 5,
          history: turns
            .filter((t) => t.response && !t.error)
            .slice(-4)
            .map((t) => ({ question: t.question, answer: t.response!.answer })),
        },
        (_delta, full) => {
          // Token 级流式：增量正文实时上屏（来源卡片在收尾帧统一补齐）
          setTurns((xs) =>
            xs.map((t) =>
              t.id === id
                ? {
                    ...t,
                    response: t.response
                      ? { ...t.response, answer: full }
                      : { answer: full, sources: [], verified: true },
                  }
                : t,
            ),
          )
        },
      )
      setTurns((xs) => xs.map((t) => (t.id === id ? { ...t, loading: false, response } : t)))
    } catch (e) {
      setTurns((xs) =>
        xs.map((t) =>
          t.id === id
            ? {
                ...t,
                loading: false,
                error: api.isModelUnavailable(e)
                  ? '模型服务未配置：问答依赖大模型服务，配置 LLM_API_KEY 后即可启用。'
                  : e instanceof Error
                    ? e.message
                    : '提问失败',
              }
            : t,
        ),
      )
    }
  }

  function clearThread() {
    setTurns([])
    try {
      sessionStorage.removeItem(THREAD_KEY)
    } catch {
      /* ignore */
    }
  }

  function exportMd() {
    const lines = ['# AI 问答记录', '']
    for (const t of turns) {
      lines.push(`## Q：${t.question}`, '')
      if (t.error) lines.push(`（提问失败：${t.error}）`, '')
      if (t.response) {
        lines.push(t.response.answer, '')
        if (t.response.sources.length > 0) {
          lines.push('来源：')
          for (const s of t.response.sources) {
            lines.push(
              `- [${s.paper_id}] 第 ${s.page_no} 页（${s.verified ? '已溯源' : '未溯源'}）：${s.quote}`,
            )
          }
          lines.push('')
        }
      }
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'qa-record.md'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  function copyAnswer(t: ChatTurn) {
    if (!t.response) return
    void navigator.clipboard.writeText(t.response.answer)
    message.success('回答已复制')
  }

  return (
    <aside className="panel panel-qa">
      <div className="panel-head">
        <span className="panel-title">AI 问答</span>
        <Tooltip title={`已索引 ${totalChunks} 个文本块 · 支持多轮追问（记住上文）`}>
          <Tag color={totalChunks > 0 ? 'green' : 'default'}>
            {totalChunks > 0 ? `已索引 ${totalChunks} 块` : '尚未建索引'}
          </Tag>
        </Tooltip>
        <Space size={4} style={{ marginLeft: 'auto' }}>
          <Button size="small" disabled={turns.length === 0} onClick={exportMd}>
            导出
          </Button>
          <Tooltip title="清空对话记录">
            <Button size="small" disabled={turns.length === 0} onClick={clearThread}>
              清空
            </Button>
          </Tooltip>
        </Space>
      </div>

      <div className="qa-thread">
        {turns.length === 0 && (
          <Empty description="针对文献提问" image={Empty.PRESENTED_IMAGE_SIMPLE}>
            <span className="empty-hint">
              {totalChunks === 0
                ? '请先在左侧文献库点击「建索引」，再开始提问'
                : '回答附带溯源卡片，点击可跳回原文页；可连续追问，自动记住上文'}
            </span>
            <div style={{ marginTop: 12 }}>
              {EXAMPLES.map((ex) => (
                <Button
                  key={ex}
                  size="small"
                  style={{ margin: '0 4px 4px 0' }}
                  onClick={() => setQuestion(ex)}
                >
                  {ex}
                </Button>
              ))}
            </div>
          </Empty>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className="qa-turn">
            <div className="qa-question">{turn.question}</div>
            {turn.loading && !turn.response && <Spin size="small" tip="检索与生成中…" />}
            {turn.error && (
              <Alert
                type="error"
                showIcon
                title="提问失败"
                description={turn.error}
                className="qa-error"
              />
            )}
            {turn.response && (
              <div className="qa-answer-block">
                {turn.response.answer.includes(REFUSAL) ? (
                  <Alert
                    type="info"
                    showIcon
                    title="资料中未找到"
                    description="当前文献库内容里没有足够依据回答该问题。可以换种问法，或上传更多相关论文后重建索引。"
                  />
                ) : (
                  <div className="qa-answer">
                    {turn.response.answer}
                    {turn.loading ? '▍' : ''}
                  </div>
                )}
                <div className="qa-sources">
                  <div className="qa-sources-title">
                    来源溯源
                    <Badge
                      count={turn.response.sources.length}
                      style={{ marginLeft: 8, backgroundColor: '#e2e8f0', color: '#475569' }}
                    />
                    <Button size="small" type="text" onClick={() => copyAnswer(turn)}>
                      复制
                    </Button>
                  </div>
                  {turn.response.sources.length === 0 && <span className="empty-hint">未返回来源片段</span>}
                  {turn.response.sources.map((s) => (
                    <button
                      key={s.chunk_id}
                      type="button"
                      className="source-card"
                      onClick={() => onJump(s.paper_id, s.page_no)}
                      title={`跳转到 ${paperTitle(s.paper_id)} 第 ${s.page_no} 页`}
                    >
                      <div className="source-meta">
                        <Tag>{`第 ${s.page_no} 页`}</Tag>
                        <span className="source-paper" title={paperTitle(s.paper_id)}>
                          {paperTitle(s.paper_id)}
                        </span>
                        {s.verified ? (
                          <Tooltip title="已溯源：答案中的引用可在原文中找到">
                            <span className="verified-badge verified">已溯源</span>
                          </Tooltip>
                        ) : (
                          <Tooltip title="未溯源：生成内容缺少原文依据，请谨慎采信">
                            <span className="verified-badge unverified">未溯源</span>
                          </Tooltip>
                        )}
                      </div>
                      <div className="source-quote">{s.quote}</div>
                      <div className="source-foot">
                        <code>{s.chunk_id}</code>
                        <code>{s.paper_id}</code>
                        <span className="source-jump">点击跳转原文 →</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="qa-input">
        {onlyCurrent && selectedId && (
          <div className="qa-filter-hint">已限定：{paperTitle(selectedId)}</div>
        )}
        <Input.TextArea
          rows={3}
          value={question}
          placeholder="例如：这篇论文提出了什么架构？可以直接追问（自动记住上文）"
          onChange={(e) => setQuestion(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault()
              ask()
            }
          }}
        />
        <div className="qa-input-actions">
          <Checkbox
            disabled={!selectedId}
            checked={onlyCurrent}
            onChange={(e) => setOnlyCurrent(e.target.checked)}
          >
            限定当前文献
          </Checkbox>
          <Button type="primary" loading={turns.some((t) => t.loading)} disabled={!question.trim()} onClick={ask}>
            提问
          </Button>
        </div>
      </div>
    </aside>
  )
}
