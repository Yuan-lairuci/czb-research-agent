import { useEffect, useState } from 'react'
import { BASE } from '../api'

/** 后端连通性指示（自取 /health，失败仅降级不打扰）。 */
export default function HealthStatus() {
  const [state, setState] = useState<'pending' | 'ok' | 'bad'>('pending')
  const [version, setVersion] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch(`${BASE}/health`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: { status?: string; version?: string }) => {
        if (cancelled) return
        setVersion(data.version ?? '')
        setState(data.status === 'ok' ? 'ok' : 'bad')
      })
      .catch(() => {
        if (!cancelled) setState('bad')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <span className={`health-chip ${state}`} title={`API: ${BASE}`}>
      <span className="health-dot" aria-hidden />
      {state === 'pending' ? '后端检测中' : state === 'ok' ? `后端已连接 ${version && `v${version}`}` : '后端未连接'}
    </span>
  )
}
