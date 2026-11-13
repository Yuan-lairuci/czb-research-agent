import HealthStatus from './HealthStatus'

export type ViewKey = 'library' | 'reader' | 'qa' | 'recommend' | 'graph' | 'writing' | 'help'

export const NAV_ITEMS: { key: ViewKey; label: string }[] = [
  { key: 'library', label: '文献库' },
  { key: 'reader', label: '阅读器+翻译' },
  { key: 'qa', label: 'AI 问答' },
  { key: 'recommend', label: '检索推荐' },
  { key: 'graph', label: '引用图谱' },
  { key: 'writing', label: '写作 Copilot' },
  { key: 'help', label: '帮助' },
]

export function isViewKey(v: string | null): v is ViewKey {
  return NAV_ITEMS.some((item) => item.key === v)
}

interface Props {
  view: ViewKey
  onViewChange: (v: ViewKey) => void
}

/** 顶栏：品牌 + 七视图主导航（hash 路由）+ 后端状态 */
export default function TopBar({ view, onViewChange }: Props) {
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
          <button
            key={item.key}
            type="button"
            className={item.key === view ? 'topnav-btn topnav-active' : 'topnav-btn'}
            aria-current={item.key === view ? 'page' : undefined}
            onClick={() => onViewChange(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="topbar-right">
        <HealthStatus />
      </div>
    </header>
  )
}
