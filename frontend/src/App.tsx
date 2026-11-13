import { useCallback, useEffect, useRef, useState } from 'react'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import TopBar from './components/TopBar'
import LibraryPanel from './components/LibraryPanel'
import ReaderPanel from './components/ReaderPanel'
import QaPanel from './components/QaPanel'
import TranslationPanel from './components/TranslationPanel'
import PdfSmoke from './components/PdfSmoke'
import * as api from './api'
import type { DedupGroup, JumpRequest, PaperInfo, ParsedContract } from './types'

const theme = {
  token: {
    colorPrimary: '#2563eb',
    colorInfo: '#2563eb',
    colorBgLayout: '#f4f7fb',
    colorText: '#1f2a37',
    colorTextSecondary: '#5b6b7c',
    borderRadius: 6,
    fontFamily: "'Segoe UI', 'Microsoft YaHei', -apple-system, sans-serif",
    fontSize: 14,
  },
}

function Workspace() {
  const { message } = AntdApp.useApp()
  const [papers, setPapers] = useState<PaperInfo[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [parsed, setParsed] = useState<ParsedContract | null>(null)
  const [scanGroups, setScanGroups] = useState<DedupGroup[]>([])
  const [indexInfo, setIndexInfo] = useState<Record<string, number>>({})
  const [listError, setListError] = useState<string | null>(null)
  const [listLoading, setListLoading] = useState(true)
  const [jump, setJump] = useState<JumpRequest | null>(null)
  const jumpNonce = useRef(0)

  const refreshList = useCallback(async () => {
    setListLoading(true)
    try {
      const res = await api.listPapers()
      setPapers(res.papers)
      setListError(null)
    } catch (e) {
      setListError(e instanceof Error ? e.message : '无法连接后端')
    } finally {
      setListLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshList()
    api
      .qaStatus()
      .then((s) => setIndexInfo(s.papers))
      .catch(() => undefined) // 索引状态非关键路径，失败静默
  }, [refreshList])

  // 选中文献变化时加载解析契约（标题树/引用联动数据源）
  useEffect(() => {
    if (!selectedId) {
      setParsed(null)
      return
    }
    let cancelled = false
    setParsed(null)
    api
      .getParsed(selectedId)
      .then((contract) => {
        if (!cancelled) setParsed(contract)
      })
      .catch((e: unknown) => {
        if (!cancelled) message.warning(`解析内容加载失败：${e instanceof Error ? e.message : '未知错误'}`)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId, message])

  const jumpToPaper = useCallback((paperId: string, pageNo: number) => {
    jumpNonce.current += 1
    setJump({ paperId, pageNo, nonce: jumpNonce.current })
    setSelectedId((cur) => (cur === paperId ? cur : paperId))
  }, [])

  const handleIndexed = useCallback((paperId: string, numChunks: number) => {
    setIndexInfo((cur) => ({ ...cur, [paperId]: numChunks }))
  }, [])

  const handleDeleted = useCallback(
    (paperId: string) => {
      setPapers((cur) => cur.filter((p) => p.paper_id !== paperId))
      setSelectedId((cur) => (cur === paperId ? null : cur))
      void refreshList()
    },
    [refreshList],
  )

  return (
    <div className="app">
      <TopBar />
      <div className="workspace">
        <LibraryPanel
          papers={papers}
          selectedId={selectedId}
          loading={listLoading}
          error={listError}
          scanGroups={scanGroups}
          indexInfo={indexInfo}
          onSelect={setSelectedId}
          onChanged={refreshList}
          onScanned={setScanGroups}
          onIndexed={handleIndexed}
          onDeleted={handleDeleted}
        />
        <ReaderPanel
          paper={papers.find((p) => p.paper_id === selectedId) ?? null}
          parsed={parsed}
          jump={jump}
          onJumpConsumed={() => setJump(null)}
        />
        <QaPanel
          papers={papers}
          selectedId={selectedId}
          indexInfo={indexInfo}
          onJump={jumpToPaper}
        />
      </div>
      <TranslationPanel />
    </div>
  )
}

export default function App() {
  // DEV 冒烟页：?pdfsmoke=1 走独立 pdfjs 渲染验证（不影响正式界面）
  const isDevSmoke = import.meta.env.DEV && new URLSearchParams(window.location.search).has('pdfsmoke')

  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp>{isDevSmoke ? <PdfSmoke /> : <Workspace />}</AntdApp>
    </ConfigProvider>
  )
}
