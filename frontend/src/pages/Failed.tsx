import { useEffect, useState } from 'react'
import { useUploadStore } from '@/store/uploadStore'
import { sessionsApi, uploadApi } from '@/api/client'
import type { FailedFile } from '@/types'
import { RotateCcw, Download, AlertTriangle } from 'lucide-react'
import clsx from 'clsx'

function csvEscape(cell: string): string {
  const s = String(cell).replace(/"/g, '""')
  if (/[",\r\n]/.test(s)) return `"${s}"`
  return s
}

export default function Failed() {
  const { activeSessionId, sessions, fetchSessions } = useUploadStore()
  const [failed, setFailed] = useState<FailedFile[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sessionId, setSessionId] = useState(activeSessionId || '')
  const [retrying, setRetrying] = useState(false)

  const load = async (sid: string) => {
    if (!sid) return
    const data = await sessionsApi.getFailed(sid)
    setFailed(data)
  }

  useEffect(() => {
    void fetchSessions()
  }, [fetchSessions])

  useEffect(() => {
    if (sessionId) load(sessionId)
  }, [sessionId])

  const toggle = (id: string) => {
    setSelected(s => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  const retrySelected = async () => {
    if (!sessionId || selected.size === 0) return
    setRetrying(true)
    try {
      await uploadApi.retry(sessionId, [...selected])
      setSelected(new Set())
      await load(sessionId)
    } finally {
      setRetrying(false)
    }
  }

  const exportCsv = () => {
    const rows = [
      ['filename', 'path', 'error_type', 'error_message', 'occurred_at'],
      ...failed.map(f => [f.filename, f.path, f.error_type, f.error_message, f.occurred_at]),
    ]
    const csv = rows.map(r => r.map(c => csvEscape(String(c))).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = 'failed_uploads.csv'; a.click()
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gradient">Failed Uploads</h1>
          <p className="text-slate-500 text-sm mt-1">Retry or export failed files</p>
        </div>
        <div className="flex gap-2">
          {selected.size > 0 && (
            <button className="btn-primary" onClick={retrySelected} disabled={retrying}>
              <RotateCcw size={14} /> Retry {selected.size} selected
            </button>
          )}
          {failed.length > 0 && (
            <button className="btn-secondary" onClick={exportCsv}>
              <Download size={14} /> Export CSV
            </button>
          )}
        </div>
      </div>

      {/* Session selector */}
      <div className="card">
        <label className="label">Select Session</label>
        <select
          className="input"
          value={sessionId}
          onChange={e => setSessionId(e.target.value)}
        >
          <option value="">Choose a session…</option>
          {sessions.map(s => (
            <option key={s.id} value={s.id}>
              {s.name} — {new Date(s.created_at).toLocaleDateString()} ({s.failed_files} failed)
            </option>
          ))}
        </select>
      </div>

      {failed.length === 0 && sessionId ? (
        <div className="card text-center py-10 text-slate-500">
          <AlertTriangle size={28} className="mx-auto mb-2 opacity-40" />
          <p>No failed uploads for this session 🎉</p>
        </div>
      ) : (
        <div className="card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] bg-surface-700">
                <th className="w-8 p-3">
                  <input
                    type="checkbox"
                    checked={selected.size === failed.length && failed.length > 0}
                    onChange={e => setSelected(e.target.checked ? new Set(failed.map(f => f.file_id)) : new Set())}
                    className="accent-brand-500"
                  />
                </th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider">File</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider">Error</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider">Time</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-400 uppercase tracking-wider">Status</th>
              </tr>
            </thead>
            <tbody>
              {failed.map(f => (
                <tr
                  key={f.id}
                  className={clsx(
                    'border-b border-white/[0.04] hover:bg-white/[0.02] cursor-pointer transition-colors',
                    selected.has(f.file_id) && 'bg-brand-500/5'
                  )}
                  onClick={() => toggle(f.file_id)}
                >
                  <td className="p-3">
                    <input type="checkbox" checked={selected.has(f.file_id)} onChange={() => {}} className="accent-brand-500" />
                  </td>
                  <td className="px-3 py-3">
                    <p className="font-medium text-slate-200 truncate max-w-[200px]">{f.filename}</p>
                    <p className="text-xs text-slate-500 truncate max-w-[200px]">{f.path}</p>
                  </td>
                  <td className="px-3 py-3">
                    <p className="text-red-400 text-xs font-mono">{f.error_type}</p>
                    <p className="text-slate-500 text-xs truncate max-w-[200px]">{f.error_message}</p>
                  </td>
                  <td className="px-3 py-3 text-xs text-slate-500 whitespace-nowrap">
                    {new Date(f.occurred_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-3">
                    {f.resolved
                      ? <span className="badge badge-green">Resolved</span>
                      : <span className="badge badge-red">Failed</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
