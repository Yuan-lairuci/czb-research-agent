/**
 * 预留第三方集成（骨架阶段仅声明，不引入依赖）：
 * - PDF 渲染：pdfjs-dist 或 react-pdf（后续 PDF 解析/精读模块接入）
 * - 图表可视化：ECharts（后续数据图表模块接入）
 */
export const RESERVED_LIBS = {
  pdf: ['pdfjs-dist', 'react-pdf'],
  chart: ['echarts'],
} as const
