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

/** POST /api/library/dedup/version-diff */
export interface SectionChange {
  name: string
  /** added | removed | modified | unchanged */
  status: string
  old_chars: number
  new_chars: number
}

export interface VersionDiffRequest {
  paper_id_a: string
  paper_id_b: string
}

export interface VersionDiffResponse {
  older_id: string
  newer_id: string
  older_title: string
  newer_title: string
  version_evidence: string
  sections: SectionChange[]
  llm_summary: string
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
  /** 同会话此前问答历史（多轮追问记忆） */
  history?: { question: string; answer: string }[]
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

// ============ 模块三 翻译 ============
/** POST /api/translate/text · direction 默认 en2zh */
export type TranslateDirection = 'zh2en' | 'en2zh'

export interface TranslateRequest {
  text: string
  direction?: TranslateDirection
  stream?: boolean
}

/** stream=false → { translated, engine } */
export interface TranslateResponse {
  translated: string
  engine: string
}

/** SSE 流式帧 data: { delta, done, translated?, engine? } */
export interface TranslateStreamFrame {
  delta: string
  done: boolean
  translated?: string
  engine?: string
}

/** POST /api/translate/page → BlockTranslation[] */
export interface BlockTranslation {
  block_index: number
  src_text: string
  dst_text: string
}

export interface TranslatedPage {
  page_no: number
  blocks: BlockTranslation[]
}

/** GET /api/translate/paper/{id}?direction=&fill= */
export interface PaperTranslateResponse {
  paper_id: string
  direction: TranslateDirection
  total_pages: number
  translated_pages: number
  done: boolean
  pages: TranslatedPage[]
}

// ============ 模块四 检索推荐 ============
/** POST /api/recommend/search → results[] */
export interface Candidate {
  title: string
  abstract?: string | null
  year?: number | null
  citation_count?: number | null
  influential_citation_count?: number | null
  s2_id?: string | null
  arxiv_id?: string | null
  doi?: string | null
  pdf_url?: string | null
  github_url?: string | null
  /** s2 | arxiv | both | arxiv_only */
  source: string
}

export interface SearchResponse {
  query: string
  results: Candidate[]
  degraded: boolean
  s2_ok: boolean
  arxiv_ok: boolean
}

/** POST /api/recommend/fetch */
export interface FetchResponse {
  arxiv_id: string
  paper_id?: string | null
  title?: string | null
  num_pages: number
  /** parsed | failed | duplicate */
  parse_status: string
  detail?: string | null
}

/** POST /api/recommend/by-paper → recommendations[] */
export interface Recommendation {
  title: string
  s2_id?: string | null
  arxiv_id?: string | null
  year?: number | null
  citation_count?: number | null
  influential_citation_count?: number | null
  github_url?: string | null
  score: number
  reasons: string[]
  /** reference | citation */
  relation: string
}

export interface ByPaperResponse {
  paper_id: string
  recommendations: Recommendation[]
  references_considered: number
  citations_considered: number
  dropped_perfunctory: number
}

/** POST /api/recommend/feedback */
export interface FeedbackResponse {
  stored: boolean
  paper_key: string
}

/** arXiv 版本追踪 */
export interface TrackItem {
  arxiv_id: string
  base_id: string
  title?: string | null
  tracked_version?: number | null
  latest_version?: number | null
  added_at: string
  last_checked?: string | null
}

export interface TrackListResponse {
  items: TrackItem[]
}

export interface TrackUpdate {
  arxiv_id: string
  base_id: string
  title?: string | null
  old_version?: number | null
  new_version: number
}

export interface TrackRunResponse {
  checked: number
  updates: TrackUpdate[]
  errors: string[]
}

// ============ 模块五 引用图谱 ============
export interface GraphNode {
  paper_id: string
  title: string
  year?: number | null
  arxiv_id?: string | null
}

export interface GraphEdge {
  source: string
  target: string
  ref_index: number
  context: string
}

export interface NodeMetrics {
  paper_id: string
  title: string
  pagerank: number
  degree_centrality: number
  betweenness_centrality: number
  /** 基石论文 | 边缘衍生论文 */
  category: string
  interpretation: string
}

export interface CitationGraph {
  graph_id: string
  created_at: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  metrics: NodeMetrics[]
}

/** POST /api/graph/build */
export interface GraphBuildResponse {
  graph_id: string
  num_nodes: number
  num_edges: number
}

/** POST /api/graph/analyze */
export interface GraphAnalyzeResponse {
  graph_id: string
  metrics: NodeMetrics[]
  overall_interpretation: string
}

/** POST /api/graph/review */
export interface CitationRef {
  n: number
  paper_id: string
  title: string
}

export interface ReviewSection {
  heading: string
  content: string
}

export interface ReviewResponse {
  graph_id: string
  topic?: string | null
  /** llm | template */
  generated_by: string
  sections: ReviewSection[]
  citation_map: CitationRef[]
  dropped_citations: string[]
}

/** POST /api/graph/future-work */
export interface FutureWorkItem {
  item: string
  proposed_by: string
  paper_id: string
  year?: number | null
  /** solved | unresolved | partially */
  status: string
  evidence: string
}

export interface FutureWorkResponse {
  graph_id: string
  timeline: FutureWorkItem[]
}

// ============ 模块六 写作 Copilot ============
export type RefStyle = 'plain' | 'apa' | 'gbt7714'
export type DiagramKind = 'tikz' | 'graphviz' | 'mermaid'
export type ChartType = 'line' | 'bar' | 'radar' | 'scatter'

/** POST /api/writing/outline */
export interface OutlineSection {
  title: string
  points: string[]
}

export interface OutlineResponse {
  idea: string
  sections: OutlineSection[]
}

/** POST /api/writing/references */
export interface ReferenceItem {
  title: string
  authors: string[]
  year?: number | null
  venue?: string | null
  arxiv_id?: string | null
  doi?: string | null
  url?: string | null
  source: string
  formatted: string
}

export interface DroppedReference {
  title?: string | null
  arxiv_id?: string | null
  doi?: string | null
  reason: string
}

export interface ReferencesResponse {
  query: string
  style: string
  references: ReferenceItem[]
  dropped: DroppedReference[]
  sources_used: string[]
}

/** POST /api/writing/diagram */
export interface DiagramResponse {
  kind: string
  code: string
  usage_hint: string
}

/** POST /api/writing/chart → option 为 ECharts option */
export interface ChartResponse {
  option: Record<string, unknown>
  caption: string
}

/** POST /api/writing/zh2en */
export interface Zh2EnResponse {
  translated: string
  engine: string
}

