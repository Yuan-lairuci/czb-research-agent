import { useEffect, useState } from 'react'

interface Health {
  status: string
  version: string
}

// 后端地址：默认直连本地 8000，可用 VITE_API_BASE 覆盖
const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export default function HealthStatus() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data: Health) => setHealth(data))
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <section className="health-card">
      <h2>后端状态</h2>
      {health && (
        <p className="ok">
          状态：{health.status}（版本 {health.version}）
        </p>
      )}
      {error && <p className="err">无法连接后端：{error}</p>}
      {!health && !error && <p>检测中…</p>}
    </section>
  )
}
