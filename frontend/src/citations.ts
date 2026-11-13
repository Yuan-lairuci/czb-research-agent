/**
 * 引用联动索引：扫描解析契约正文里的 [n] 形态标记，
 * 支撑 正文标记→参考文献条目 与 参考文献条目→正文首次出现页 的双向联动。
 */
import type { ParsedContract } from './types'

/** [1] [11] [1, 2] [1-3] [1,3-5] 等形态 */
const CITATION_RE = /\[(\d+(?:\s*[,，]\s*\d+)*(?:\s*[-–—]\s*\d+)?)\]/g

/** 展开一个标记内的所有引用号：[1,3-5] → [1,3,4,5] */
export function expandRefs(inner: string): number[] {
  const out: number[] = []
  for (const part of inner.split(/[,，]/)) {
    const range = part.match(/(\d+)\s*[-–—]\s*(\d+)/)
    if (range) {
      const lo = Number(range[1])
      const hi = Number(range[2])
      for (let n = lo; n <= hi && n - lo < 50; n += 1) out.push(n)
    } else {
      const n = Number(part.trim())
      if (Number.isFinite(n)) out.push(n)
    }
  }
  return out
}

export interface CitationOccurrence {
  pageNo: number
  blockIndex: number
}

export interface CitationIndex {
  /** 引用号 → 正文首次出现位置 */
  firstOccurrence: Map<number, CitationOccurrence>
  /** 引用号 → 出现次数 */
  counts: Map<number, number>
}

export function buildCitationIndex(parsed: ParsedContract): CitationIndex {
  const firstOccurrence = new Map<number, CitationOccurrence>()
  const counts = new Map<number, number>()
  parsed.pages.forEach((page, pageIdx) => {
    page.blocks.forEach((block, blockIdx) => {
      if (block.type === 'table' || block.type === 'figure') return
      for (const match of block.text.matchAll(CITATION_RE)) {
        for (const n of expandRefs(match[1])) {
          counts.set(n, (counts.get(n) ?? 0) + 1)
          if (!firstOccurrence.has(n)) firstOccurrence.set(n, { pageNo: page.page_no, blockIndex: pageIdx * 1000 + blockIdx })
        }
      }
    })
  })
  return { firstOccurrence, counts }
}

export interface TextSegment {
  text: string
  /** 非空表示该段是引用标记，点击应联动这些引用号 */
  refs?: number[]
}

/** 把一段文本按引用标记切开，标记段携带引用号（供点击联动）。 */
export function splitByCitations(text: string): TextSegment[] {
  const segments: TextSegment[] = []
  let last = 0
  for (const match of text.matchAll(CITATION_RE)) {
    const start = match.index ?? 0
    if (start > last) segments.push({ text: text.slice(last, start) })
    const refs = expandRefs(match[1])
    segments.push({ text: match[0], refs })
    last = start + match[0].length
  }
  if (last < text.length) segments.push({ text: text.slice(last) })
  return segments
}
