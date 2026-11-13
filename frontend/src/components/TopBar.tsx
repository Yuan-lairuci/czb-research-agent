import { Tooltip } from 'antd'
import HealthStatus from './HealthStatus'

/** 顶栏：品牌 + 导航占位（检索推荐/图谱/综述/写作，FE-2 接入）+ 后端状态 */
const NAV_ITEMS = ['检索推荐', '图谱', '综述', '写作'] as const

export default function TopBar() {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          研
        </span>
        <div className="brand-text">
          <span className="brand-name">科研助手 Agent</span>
          <span className="brand-sub">传智杯 · AI 大模型创新应用挑战赛</span>
        </div>
      </div>
      <nav className="topnav" aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <Tooltip key={item} title="功能模块建设中（FE-2 接入）">
            <button type="button" className="topnav-item" disabled>
              {item}
            </button>
          </Tooltip>
        ))}
      </nav>
      <div className="topbar-right">
        <HealthStatus />
      </div>
    </header>
  )
}
