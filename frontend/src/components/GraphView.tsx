import { Empty } from 'antd'
import type { PaperInfo } from '../types'

/** 引用图谱视图（FE-2 建设中占位）。 */
export default function GraphView({ papers: _papers }: { papers: PaperInfo[] }) {
  return (
    <div className="view view-stub">
      <Empty description="引用图谱模块建设中" />
    </div>
  )
}
