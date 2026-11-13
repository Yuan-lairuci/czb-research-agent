import { useState } from 'react'
import { Alert, Badge, Button, Checkbox, Empty, Input, Spin, Tag, Tooltip } from 'antd'
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

/** 右栏·AI 问答：提问 → /api/qa/ask → answer + sources 卡片 */
export default function QaPanel({ papers, selectedId, indexInfo, onJump }: Props) {
  const [question, setQuestion] = useState('')
  const [onlyCurrent, setOnlyCurrent] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [seq, setSeq] = useState(1)

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
      const response = await api.askQa({
        question: q,
        paper_ids: onlyCurrent && selectedId ? [selectedId] : undefined,
        top_k: 5,
      })
      setTurns((xs) => xs.map((t) => (t.id === id ? { ...t, loading: false, response } : t)))
    } catch (e) {
      setTurns((xs) =>
        xs.map((t) =>
          t.id === id ? { ...t, loading: false, error: e instanceof Error ? e.message : '提问失败' } : t,
        ),
      )
    }
  }

  return (
    <aside className="panel panel-qa">
      <div className="panel-head">
        <span className="panel-title">AI 问答</span>
        <Tooltip title={`已索引 ${totalChunks} 个文本块`}>
          <Tag color={totalChunks > 0 ? 'green' : 'default'}>
            {totalChunks > 0 ? `已索引 ${totalChunks} 块` : '尚未建索引'}
          </Tag>
        </Tooltip>
      </div>

      <div className="qa-thread">
        {turns.length === 0 && (
          <Empty description="针对文献提问" image={Empty.PRESENTED_IMAGE_SIMPLE}>
            <span className="empty-hint">
              {totalChunks === 0
                ? '请先在左侧文献库点击「建索引」，再开始提问'
                : '回答附带溯源卡片，点击可跳回原文页'}
            </span>
          </Empty>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className="qa-turn">
            <div className="qa-question">{turn.question}</div>
            {turn.loading && <Spin size="small" tip="检索与生成中…" />}
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
                  <div className="qa-answer">{turn.response.answer}</div>
                )}
                <div className="qa-sources">
                  <div className="qa-sources-title">
                    来源溯源
                    <Badge
                      count={turn.response.sources.length}
                      style={{ marginLeft: 8, backgroundColor: '#e2e8f0', color: '#475569' }}
                    />
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
          placeholder="例如：这篇论文提出了什么架构？"
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
