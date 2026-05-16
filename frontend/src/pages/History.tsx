import { useEffect, useState } from 'react'
import { useUploadStore } from '@/store/uploadStore'
import { formatDistanceToNow } from 'date-fns'
import clsx from 'clsx'
import { Clock, Play, Loader2, Folder } from 'lucide-react'
import type { Session } from '@/types'

const STATUS_CONFIG: Record<string, { cls: string; badge: string; label: string }> = {
  completed: { cls: 'text-emerald-400', badge: 'badge-green', label: 'Completed' },
  failed: { cls: 'text-red-400', badge: 'badge-red', label: 'Failed' },
  stopped: { cls: 'text-slate-400', badge: 'badge-gray', label: 'Stopped' },
  paused: { cls: 'text-amber-400', badge: 'badge-yellow', label: 'Paused' },
  running: { cls: 'text-brand-400', badge: 'badge-blue', label: 'Running' },
  pending: { cls: 'text-slate-400', badge: 'badge-gray', label: 'Pending' },
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '—'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms <= 0) return '—'
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 120) return `${min}m ${sec % 60}s`
  const h = Math.floor(min / 60)
  return `${h}h ${min % 60}m`
}

export default function History() {
  const { sessions, fetchSessions, resumeById } = useUploadStore()
  const [resumingId, setResumingId] = useState<string | null>(null)

  useEffect(() => {
    void fetchSessions()
  }, [fetchSessions])

  const canResume = (s: Session) => s.status === 'paused' || s.status === 'running'

  const handleResume = async (id: string) => {
    setResumingId(id)
    try {
      await resumeById(id)
    } finally {
      setResumingId(null)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-gradient">Upload History</h1>
        <p className="text-slate-500 text-sm mt-1">Past upload sessions and results</p>
      </div>

      {sessions.length === 0 ? (
        <div className="card text-center py-12 text-slate-500">
          <Clock size={32} className="mx-auto mb-3 opacity-40" />
          <p className="font-medium">No sessions yet</p>
          <p className="text-sm mt-1">Start your first upload from the Dashboard</p>
        </div>
      ) : (
        <div className="space-y-3">
          {sessions.map(s => {
            const cfg = STATUS_CONFIG[s.status] ?? STATUS_CONFIG.pending
            const pct = s.total_files > 0 ? Math.round((s.uploaded_files / s.total_files) * 100) : 0
            const duration = formatDuration(s.started_at, s.completed_at)

            return (
              <div key={s.id} className="card hover:border-white/10 transition-colors animate-fade-in">
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="font-semibold text-slate-100">{s.name}</p>
                      <span className={clsx('badge', cfg.badge)}>{cfg.label}</span>
                    </div>
                    <div className="flex items-center gap-1 mt-1 text-xs text-slate-500">
                      <Folder size={10} />
                      <span className="truncate">{s.root_folder}</span>
                    </div>
                    <div className="mt-3">
                      <div className="flex justify-between text-xs text-slate-500 mb-1">
                        <span>{s.uploaded_files} / {s.total_files} photos</span>
                        <span>{pct}%</span>
                      </div>
                      <div className="progress-bar">
                        <div className="progress-fill" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                      <span>Duration: <span className="text-slate-400">{duration}</span></span>
                      <span>Started: {s.started_at ? new Date(s.started_at).toLocaleString() : '—'}</span>
                    </div>
                  </div>
                  <div className="text-right shrink-0 flex flex-col items-end gap-2">
                    {canResume(s) && (
                      <button
                        type="button"
                        className="btn-primary text-xs py-1.5 px-3"
                        disabled={resumingId === s.id}
                        onClick={() => void handleResume(s.id)}
                      >
                        {resumingId === s.id ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          <Play size={12} />
                        )}
                        Resume
                      </button>
                    )}
                    {s.failed_files > 0 && (
                      <p className="text-xs text-red-400">{s.failed_files} failed</p>
                    )}
                    <p className="text-xs text-slate-500">
                      {new Date(s.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
