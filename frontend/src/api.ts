/**
 * 后端 API 客户端：统一 BASE、错误解析、PDF 原件获取。
 * 契约见 backend/app/api/{pdf_library,qa}.py。
 */
import type {
  AskRequest,
  AskResponse,
  DedupScanResponse,
  IndexResponse,
  PaperInfo,
  PaperListResponse,
  ParsedContract,
  QaStatusResponse,
} from './types'

// 后端地址：默认直连本地 8000，可用 VITE_API_BASE 覆盖
export const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

/** 本会话内上传过的 PDF 原件（服务端暂无文件下载接口时供阅读器渲染）。 */
export const localPdfCache = new Map<string, File>()

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`
  try {
    const body = await res.json()
    if (body && typeof body.detail === 'string') detail = body.detail
  } catch {
    /* 非 JSON 错误体，保留状态码信息 */
  }
  return new ApiError(res.status, detail)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) throw await parseError(res)
  return (await res.json()) as T
}

export function uploadPaper(file: File): Promise<PaperInfo> {
  const form = new FormData()
  form.append('file', file)
  return request<PaperInfo>('/api/library/papers', { method: 'POST', body: form })
}

export function listPapers(): Promise<PaperListResponse> {
  return request<PaperListResponse>('/api/library/papers')
}

export function deletePaper(paperId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/api/library/papers/${paperId}`, { method: 'DELETE' })
}

export function getParsed(paperId: string): Promise<ParsedContract> {
  return request<ParsedContract>(`/api/library/papers/${paperId}/parsed`)
}

export function dedupScan(): Promise<DedupScanResponse> {
  return request<DedupScanResponse>('/api/library/dedup/scan', { method: 'POST' })
}

export function indexPaper(paperId: string): Promise<IndexResponse> {
  return request<IndexResponse>('/api/qa/index', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paper_id: paperId }),
  })
}

export function qaStatus(): Promise<QaStatusResponse> {
  return request<QaStatusResponse>('/api/qa/status')
}

export function askQa(req: AskRequest): Promise<AskResponse> {
  return request<AskResponse>('/api/qa/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

/**
 * 取 PDF 原件字节：
 * 1) 本会话上传缓存；2) GET /api/library/papers/{id}/file（后端若补文件接口即生效）；
 * 3) GET /api/library/papers/{id}（约定里它下载原件；若返回 JSON 则视为无原件）。
 * 都拿不到返回 null，阅读器降级为解析文本视图。
 */
export async function fetchPaperPdf(paperId: string): Promise<Uint8Array | null> {
  const cached = localPdfCache.get(paperId)
  if (cached) return new Uint8Array(await cached.arrayBuffer())

  const candidates = [`${BASE}/api/library/papers/${paperId}/file`, `${BASE}/api/library/papers/${paperId}`]
  for (const url of candidates) {
    try {
      const res = await fetch(url, { headers: { Accept: 'application/pdf' } })
      if (!res.ok) continue
      const type = res.headers.get('Content-Type') || ''
      if (!type.includes('pdf') && !type.includes('octet-stream')) continue
      return new Uint8Array(await res.arrayBuffer())
    } catch {
      /* 网络异常时继续尝试下一个来源 */
    }
  }
  return null
}
