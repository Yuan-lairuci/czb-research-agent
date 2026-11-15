import { useMemo, useState } from 'react'
import { Alert, Badge, Button, Empty, Modal, Popconfirm, Spin, Tag, Tooltip, Upload } from 'antd'
import { App as AntdApp } from 'antd'
import * as api from '../api'
import type { DedupGroup, PaperInfo, VersionDiffResponse } from '../types'

interface Props {
  papers: PaperInfo[]
  selectedId: string | null
  loading: boolean
  error: string | null
  scanGroups: DedupGroup[]
  indexInfo: Record<string, number>
  onSelect: (id: string) => void
  /** 点「阅读」时额外通知（跳转阅读器视图） */
  onOpen?: (id: string) => void
  onChanged: () => void | Promise<void>
  onScanned: (groups: DedupGroup[]) => void
  onIndexed: (id: string, numChunks: number) => void
  onDeleted: (id: string) => void
}

interface DedupBadge {
  isKeep: boolean
  versionLabel: string
  reason: string
}

function formatTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}

function vdStatusLabel(status: string): string {
  if (status === 'added') return '新增'
  if (status === 'removed') return '删除'
  if (status === 'modified') return '修改'
  return '未变'
}

function versionFromFilename(name: string, fallback: string): string {
  const m = name.match(/v(\d+)\.pdf$/i)
  return m ? `v${m[1]}` : fallback.slice(0, 6)
}

/** 左栏·文献库：上传 / 列表 / 删除 / 去重组徽章 / 建索引 */
export default function LibraryPanel({
  papers,
  selectedId,
  loading,
  error,
  scanGroups,
  indexInfo,
  onSelect,
  onOpen,
  onChanged,
  onScanned,
  onIndexed,
  onDeleted,
}: Props) {
  const { message } = AntdApp.useApp()
  const [uploading, setUploading] = useState(0)
  const [scanning, setScanning] = useState(false)
  const [indexing, setIndexing] = useState<Set<string>>(new Set())
  const [vdOpen, setVdOpen] = useState(false)
  const [vdLoading, setVdLoading] = useState(false)
  const [vdData, setVdData] = useState<VersionDiffResponse | null>(null)

  const openVersionDiff = async (paper: PaperInfo) => {
    const group = scanGroups.find((g) => g.paper_ids.includes(paper.paper_id))
    let otherId = group && group.paper_ids.find((id) => id !== paper.paper_id)
    if (!otherId && paper.dedup_group) {
      // 刷新页面后 scanGroups 丢失：用持久化的 dedup_group 找同组另一篇
      otherId = papers.find((p) => p.dedup_group === paper.dedup_group && p.paper_id !== paper.paper_id)
        ?.paper_id
    }
    if (!otherId) return
    setVdOpen(true)
    setVdLoading(true)
    setVdData(null)
    try {
      const data = await api.versionDiff({ paper_id_a: paper.paper_id, paper_id_b: otherId })
      setVdData(data)
    } catch (e) {
      message.error('版本差异获取失败：' + (e instanceof Error ? e.message : '未知错误'))
      setVdOpen(false)
    } finally {
      setVdLoading(false)
    }
  }

  const overwriteOlder = async () => {
    if (!vdData) return
    try {
      await api.deletePaper(vdData.older_id)
      message.success('已覆盖：仅保留新版 ' + vdData.newer_id)
      setVdOpen(false)
      await onChanged()
    } catch (e) {
      message.error('覆盖失败：' + (e instanceof Error ? e.message : '未知错误'))
    }
  }

  const dedupMap = useMemo(() => {
    const map = new Map<string, DedupBadge>()
    for (const group of scanGroups) {
      const keepPaper = papers.find((p) => p.paper_id === group.suggested_keep)
      const versionLabel = versionFromFilename(keepPaper?.source_filename ?? '', group.suggested_keep)
      for (const pid of group.paper_ids) {
        map.set(pid, {
          isKeep: pid === group.suggested_keep,
          versionLabel,
          reason: group.reason,
        })
      }
    }
    return map
  }, [scanGroups, papers])

  const runDedupScan = async () => {
    setScanning(true)
    try {
      const res = await api.dedupScan()
      onScanned(res.groups)
      await onChanged()
      if (res.groups.length === 0) message.success('扫描完成：未发现同工作重复文献')
      else message.success(`扫描完成：发现 ${res.groups.length} 组同工作文献`)
    } catch (e) {
      message.error(`去重扫描失败：${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setScanning(false)
    }
  }

  const runIndex = async (paper: PaperInfo) => {
    setIndexing((cur) => new Set(cur).add(paper.paper_id))
    try {
      const res = await api.indexPaper(paper.paper_id)
      onIndexed(paper.paper_id, res.num_chunks)
      message.success(`已建索引：${res.num_chunks} 块 / ${res.num_pages} 页`)
    } catch (e) {
      message.error(`建索引失败：${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setIndexing((cur) => {
        const next = new Set(cur)
        next.delete(paper.paper_id)
        return next
      })
    }
  }

  const runDelete = async (paper: PaperInfo) => {
    try {
      await api.deletePaper(paper.paper_id)
      onDeleted(paper.paper_id)
      message.success('已删除')
    } catch (e) {
      message.error(`删除失败：${e instanceof Error ? e.message : '未知错误'}`)
    }
  }

  return (
    <aside className="panel panel-library">
      <div className="panel-head">
        <h2 className="panel-title">文献库</h2>
        <span className="panel-count">{papers.length} 篇</span>
      </div>

      <div className="library-upload">
        <Upload
          accept=".pdf,application/pdf"
          showUploadList={false}
          multiple
          customRequest={async ({ file, onSuccess, onError }) => {
            setUploading((n) => n + 1)
            try {
              const info = await api.uploadPaper(file as File)
              api.localPdfCache.set(info.paper_id, file as File)
              onSuccess?.(info)
              message.success(`已入库：${info.title}`)
              await onChanged()
            } catch (e) {
              const err = e instanceof Error ? e : new Error('上传失败')
              onError?.(err)
              message.error(`上传失败：${err.message}`)
            } finally {
              setUploading((n) => n - 1)
            }
          }}
        >
          <div className={`upload-area ${uploading > 0 ? 'busy' : ''}`}>
            {uploading > 0 ? (
              <Spin size="small" tip="上传解析中…" />
            ) : (
              <>
                <span className="upload-plus" aria-hidden>
                  +
                </span>
                <span>拖拽 PDF 到此处或点击上传</span>
                <span className="upload-sub">上传后自动解析标题与参考文献</span>
              </>
            )}
          </div>
        </Upload>
        <Button className="dedup-btn" block onClick={runDedupScan} loading={scanning}>
          去重扫描
        </Button>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          title="文献库加载失败"
          description={error}
          action={
            <Button size="small" onClick={() => void onChanged()}>
              重试
            </Button>
          }
        />
      )}

      <div className="library-list">
        {loading && papers.length === 0 && (
          <div className="panel-empty">
            <Spin />
          </div>
        )}
        {!loading && !error && papers.length === 0 && (
          <Empty description="文献库为空" image={Empty.PRESENTED_IMAGE_SIMPLE}>
            <span className="empty-hint">上传 PDF 开始科研问答</span>
          </Empty>
        )}
        {papers.map((paper) => {
          const badge = dedupMap.get(paper.paper_id)
          const persistedGroup = !badge && paper.dedup_group ? paper.dedup_group : null
          const chunks = indexInfo[paper.paper_id]
          return (
            <div
              key={paper.paper_id}
              className={`paper-card ${paper.paper_id === selectedId ? 'active' : ''} ${
                badge?.isKeep ? 'dedup-keep' : badge ? 'dedup-dup' : ''
              }`}
              onClick={() => onSelect(paper.paper_id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSelect(paper.paper_id)
              }}
            >
              <div className="paper-title" title={paper.title}>
                {paper.title || paper.source_filename}
              </div>
              <div className="paper-meta">
                <span>{paper.num_pages} 页</span>
                <span>{formatTime(paper.uploaded_at)}</span>
                {paper.parse_status === 'failed' ? (
                  <Tooltip title="解析失败，仍可上传入库">
                    <Tag color="red">解析失败</Tag>
                  </Tooltip>
                ) : (
                  chunks !== undefined && <Tag color="green">已索引 {chunks} 块</Tag>
                )}
              </div>
              {(badge || persistedGroup) && (
                <div className="paper-dedup">
                  {badge ? (
                    <Tooltip title={`保留依据：${badge.reason}`}>
                      <Badge
                        className={badge.isKeep ? 'dedup-badge keep' : 'dedup-badge'}
                        status={badge.isKeep ? 'processing' : 'default'}
                        text={`同工作 · 建议保留 ${badge.versionLabel}`}
                      />
                    </Tooltip>
                  ) : (
                    <Tooltip title="曾在去重扫描中归入同组，重新扫描可刷新建议">
                      <Badge className="dedup-badge" status="default" text={`同工作组 ${persistedGroup}`} />
                    </Tooltip>
                  )}
                </div>
              )}
              <div className="paper-actions" onClick={(e) => e.stopPropagation()}>
                <Button
                  size="small"
                  type={paper.paper_id === selectedId ? 'primary' : 'default'}
                  onClick={() => (onOpen ?? onSelect)(paper.paper_id)}
                >
                  阅读
                </Button>
                <Button size="small" loading={indexing.has(paper.paper_id)} onClick={() => void runIndex(paper)}>
                  {chunks !== undefined ? '重建索引' : '建索引'}
                </Button>
                {(badge || persistedGroup) && (
                  <Button size="small" onClick={() => void openVersionDiff(paper)}>
                    版本差异
                  </Button>
                )}
                <Popconfirm
                  title="删除这篇文献？"
                  description="将同时删除文件与解析结果"
                  okText="删除"
                  cancelText="取消"
                  onConfirm={() => void runDelete(paper)}
                >
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </div>
            </div>
          )
        })}
      </div>

      <Modal
        open={vdOpen}
        title="版本差异 · 同工作多版本"
        width={680}
        onCancel={() => setVdOpen(false)}
        footer={[
          <Button key="keep" onClick={() => setVdOpen(false)}>
            保留全部
          </Button>,
          <Popconfirm
            key="overwrite"
            title="删除旧版，仅保留新版？"
            description={vdData ? '将删除旧版 ' + vdData.older_id : ''}
            okText="覆盖"
            cancelText="取消"
            onConfirm={() => void overwriteOlder()}
          >
            <Button danger disabled={!vdData}>
              覆盖（仅保留新版）
            </Button>
          </Popconfirm>,
        ]}
      >
        {vdLoading && <Spin />}
        {vdData && (
          <div className="version-diff-body">
            <p>
              <b>版本判定：</b>
              {vdData.version_evidence}
            </p>
            <p>
              旧版 {vdData.older_title}（{vdData.older_id}） → 新版 {vdData.newer_title}（
              {vdData.newer_id}）
            </p>
            <div
              className="vd-summary"
              style={{ whiteSpace: 'pre-wrap', background: '#f6f8fa', padding: 12, borderRadius: 8 }}
            >
              {vdData.llm_summary}
            </div>
            <ul className="vd-sections">
              {vdData.sections
                .filter((s) => s.status !== 'unchanged')
                .map((s) => (
                  <li key={s.name}>
                    [{vdStatusLabel(s.status)}] {s.name}（{s.old_chars} → {s.new_chars} 字符）
                  </li>
                ))}
            </ul>
          </div>
        )}
      </Modal>
    </aside>
  )
}
