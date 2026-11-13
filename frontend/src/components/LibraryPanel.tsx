import { useMemo, useState } from 'react'
import { Alert, Badge, Button, Empty, Popconfirm, Spin, Tag, Tooltip, Upload } from 'antd'
import { App as AntdApp } from 'antd'
import * as api from '../api'
import type { DedupGroup, PaperInfo } from '../types'

interface Props {
  papers: PaperInfo[]
  selectedId: string | null
  loading: boolean
  error: string | null
  scanGroups: DedupGroup[]
  indexInfo: Record<string, number>
  onSelect: (id: string) => void
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
  onChanged,
  onScanned,
  onIndexed,
  onDeleted,
}: Props) {
  const { message } = AntdApp.useApp()
  const [uploading, setUploading] = useState(0)
  const [scanning, setScanning] = useState(false)
  const [indexing, setIndexing] = useState<Set<string>>(new Set())

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
                  onClick={() => onSelect(paper.paper_id)}
                >
                  阅读
                </Button>
                <Button size="small" loading={indexing.has(paper.paper_id)} onClick={() => void runIndex(paper)}>
                  {chunks !== undefined ? '重建索引' : '建索引'}
                </Button>
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
    </aside>
  )
}
