import { Empty } from 'antd'
import type { PaperInfo } from '../types'

/** 检索推荐视图（FE-2 建设中占位）。 */
export default function RecommendView({ papers: _papers }: { papers: PaperInfo[] }) {
  return (
    <div className="view view-stub">
      <Empty description="检索推荐模块建设中" />
    </div>
  )
}
