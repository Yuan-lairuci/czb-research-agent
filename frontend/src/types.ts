/**
 * 后端契约类型（对齐 backend/app/models/ 下的 pydantic 模型）。
 * 只读镜像：字段增删以后端为准，此处保持最小可用集。
 */

/** 跨面板跳页指令：阅读器按 nonce 消费（QA source 卡片点击 / 引用联动）。 */
export interface JumpRequest {
  paperId: string
  pageNo: number
  nonce: number
}

/** GET /api/library/papers → PaperListResponse.papers[] */
export interface PaperInfo {
  paper_id: string
  title: string
  num_pages: number
  /** pending / parsed / failed */
  parse_status: string
  dedup_group: string | null
  source_filename: string
  sha256: string
  uploaded_at: string
}

export interface PaperListResponse {
  papers: PaperInfo[]
}

/** POST /api/library/dedup/scan → groups[] */
export interface DedupGroup {
  paper_ids: string[]
  same_work: boolean
  suggested_keep: string
  reason: string
}

export interface DedupScanResponse {
  groups: DedupGroup[]
}

/** GET /api/library/papers/{id}/parsed → 解析契约 JSON */
export interface ParsedBlock {
  type: 'text' | 'heading' | 'table' | 'formula' | 'figure'
  text: string
  level?: number
}

export interface ParsedPage {
  page_no: number
  blocks: ParsedBlock[]
}

export interface ParsedReference {
  index: number
  raw: string
  title?: string | null
  arxiv_id?: string | null
}

export interface ParsedContract {
  paper_id: string
  title: string
  num_pages: number
  pages: ParsedPage[]
  references: ParsedReference[]
  meta?: { source_filename?: string; [key: string]: unknown }
}

/** POST /api/qa/index */
export interface IndexResponse {
  paper_id: string
  num_chunks: number
  num_pages: number
}

/** GET /api/qa/status */
export interface QaStatusResponse {
  papers: Record<string, number>
  total_chunks: number
}

/** POST /api/qa/ask */
export interface AskRequest {
  question: string
  paper_ids?: string[]
  top_k?: number
}

export interface SourceOut {
  chunk_id: string
  paper_id: string
  page_no: number
  quote: string
  score: number
  verified: boolean
}

export interface AskResponse {
  answer: string
  sources: SourceOut[]
  verified: boolean
}
