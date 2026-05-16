import { useEffect, useRef } from 'react'
import { useLogStore } from '@/store/logStore'
import { logsApi } from '@/api/client'
import clsx from 'clsx'

export default function Logs() {
  const { logs, appendMany, clear } = useLogStore()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logsApi.list(undefined, 500).then(appendMany).catch(() => {})
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs.length])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gradient">Logs</h1>
          <p className="text-slate-500 text-sm mt-1">Real-time upload activity</p>
        </div>
        <button className="btn-ghost text-xs" onClick={clear}>Clear</button>
      </div>

      <div className="card p-0 overflow-hidden">
        <div className="h-[600px] overflow-y-auto p-4 space-y-0.5 bg-surface-900 rounded-xl">
          {logs.length === 0 ? (
            <p className="text-slate-600 text-xs font-mono">No logs yet. Start an upload to see activity here.</p>
          ) : (
            logs.map((log, i) => (
              <div key={log.id || i} className={clsx('log-line', `log-${log.level}`)}>
                <span className="text-slate-600 mr-2 select-none">
                  {new Date(log.created_at).toLocaleTimeString()}
                </span>
                <span className={clsx(
                  'mr-2 font-semibold select-none',
                  log.level === 'ERROR' && 'text-red-500',
                  log.level === 'WARN'  && 'text-amber-500',
                  log.level === 'INFO'  && 'text-brand-400',
                  log.level === 'DEBUG' && 'text-slate-600',
                )}>
                  [{log.level}]
                </span>
                {log.message}
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}
