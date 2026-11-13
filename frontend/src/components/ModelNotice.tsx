import type { CSSProperties } from 'react'
import { Alert } from 'antd'
import { isBackendUnreachable, isModelUnavailable } from '../api'

/** LLM 未配置/不可用占位：502 生成服务不可用时展示，不白屏。 */
export function ModelNotice({ style }: { style?: CSSProperties }) {
  return (
    <Alert
      className="model-notice"
      style={style}
      type="warning"
      showIcon
      title="模型服务未配置"
      description="当前未配置大模型密钥，AI 生成类功能（翻译/问答/提纲/绘图等）暂不可用。检索、图谱统计、图表渲染等确定性功能不受影响。"
    />
  )
}

/** 统一错误占位：区分后端不可达 / 模型未配置 / 其他错误。 */
export function ErrorNotice({ error, style }: { error: unknown; style?: CSSProperties }) {
  if (isModelUnavailable(error)) return <ModelNotice style={style} />
  const desc = isBackendUnreachable(error)
    ? '无法连接后端服务，请确认后端已启动（默认 http://localhost:8000）。'
    : error instanceof Error
      ? error.message
      : '未知错误'
  return <Alert style={style} type="error" showIcon title="出错了" description={desc} />
}
