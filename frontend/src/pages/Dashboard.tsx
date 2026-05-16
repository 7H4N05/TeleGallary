import { useState, useEffect } from 'react'
import {
  Play, Pause, Square, RotateCcw,
  FolderOpen, Upload, ChevronRight, X,
  CheckCircle, AlertCircle, Loader2, Image
} from 'lucide-react'
import { useUploadStore } from '@/store/uploadStore'
import { useAccountStore } from '@/store/accountStore'
import clsx from 'clsx'

function StatCard({ label, value, sub, color }: {
  label: string; value: string | number; sub?: string; color?: string
}) {
  return (
    <div className="stat-card">
      <p className="stat-label">{label}</p>
      <p className={clsx('stat-value', color || 'text-slate-100')}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  )
}

function ResumableBanner() {
  const { resumableSession, isUploading, resumeById, fetchResumable } = useUploadStore()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void fetchResumable()
  }, [fetchResumable])

  if (isUploading || !resumableSession) return null

  const label =
    resumableSession.status === 'paused'
      ? 'Paused upload'
      : 'Interrupted upload'

  return (
    <div className="card border-amber-500/25 bg-amber-500/[0.06] flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 animate-fade-in">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-amber-200">{label}</p>
        <p className="text-xs text-slate-500 mt-1 truncate">{resumableSession.name}</p>
        <p className="text-xs text-slate-600 mt-0.5 font-mono truncate">{resumableSession.root_folder}</p>
        <p className="text-xs text-slate-500 mt-2">
          {resumableSession.uploaded_files} / {resumableSession.total_files} photos • status:{' '}
          <span className="text-amber-400/90">{resumableSession.status}</span>
        </p>
      </div>
      <button
        type="button"
        className="btn-primary shrink-0 justify-center"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          try {
            await resumeById(resumableSession.id)
          } catch (e: unknown) {
            console.error(e)
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
        Resume
      </button>
    </div>
  )
}

function ProgressSection() {
  const { progress, isUploading, isPaused } = useUploadStore()

  if (!isUploading && !progress) return null

  const pct = progress && progress.total_files > 0
    ? Math.round((progress.uploaded_files / progress.total_files) * 100)
    : 0

  const speed = progress?.speed_bps
    ? `${(progress.speed_bps).toFixed(1)} files/s`
    : null

  const eta = progress?.eta_seconds
    ? progress.eta_seconds > 60
      ? `${Math.round(progress.eta_seconds / 60)}m remaining`
      : `${Math.round(progress.eta_seconds)}s remaining`
    : null

  return (
    <div className="card space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-100 flex items-center gap-2">
          <Loader2 size={15} className={clsx('text-brand-400', !isPaused && 'animate-spin')} />
          Live Upload Progress
        </h3>
        {isPaused && <span className="badge badge-yellow">Paused</span>}
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-slate-400 mb-2">
          <span>{progress?.current_folder || 'Starting…'}</span>
          <span className="font-mono font-medium text-brand-400">{pct}%</span>
        </div>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-3">
        <StatCard
          label="Uploaded"
          value={progress?.uploaded_files ?? 0}
          color="text-emerald-400"
        />
        <StatCard
          label="Total"
          value={progress?.total_files ?? 0}
        />
        <StatCard
          label="Failed"
          value={progress?.failed_files ?? 0}
          color={(progress?.failed_files ?? 0) > 0 ? 'text-red-400' : undefined}
        />
        <StatCard
          label="ETA"
          value={eta || '—'}
          sub={speed || undefined}
        />
      </div>

      {/* Current file */}
      {progress?.current_file && (
        <div className="bg-surface-700 rounded-lg px-3 py-2 border border-white/[0.05]">
          <p className="text-xs text-slate-500 mb-0.5">Current file</p>
          <p className="text-xs font-mono text-slate-300 truncate">
            {progress.current_folder && (
              <span className="text-brand-400">{progress.current_folder} / </span>
            )}
            {progress.current_file}
          </p>
          {progress.current_album !== undefined && (
            <p className="text-[10px] text-slate-500 mt-0.5">
              Album #{progress.current_album + 1}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function FolderSelector({ onScan }: { onScan: (path: string) => void }) {
  const [path, setPath] = useState('')
  const { scanning } = useUploadStore()

  return (
    <div className="card space-y-3">
      <h3 className="font-semibold text-slate-100 flex items-center gap-2">
        <FolderOpen size={15} className="text-brand-400" />
        Source Folder
      </h3>
      <div className="flex gap-2">
        <input
          className="input flex-1"
          placeholder="/path/to/Wedding/Photos"
          value={path}
          onChange={e => setPath(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && path && onScan(path)}
        />
        <button
          className="btn-secondary"
          onClick={() => path && onScan(path)}
          disabled={!path || scanning}
        >
          {scanning ? <Loader2 size={14} className="animate-spin" /> : 'Scan'}
        </button>
      </div>
    </div>
  )
}

function ScanPreview({ channelId, setChannelId, accountId, setAccountId, onStart }: {
  channelId: string
  setChannelId: (v: string) => void
  accountId: string
  setAccountId: (v: string) => void
  onStart: () => void
}) {
  const { scanResult } = useUploadStore()
  const { accounts } = useAccountStore()

  if (!scanResult) return null

  return (
    <div className="card space-y-4 animate-slide-up">
      {/* Summary */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-100 flex items-center gap-2">
          <Image size={15} className="text-brand-400" />
          Scan Result
        </h3>
        <div className="flex gap-2">
          <span className="badge badge-blue">{scanResult.total_files} photos</span>
          <span className="badge badge-gray">{scanResult.total_size_human}</span>
        </div>
      </div>

      {/* Folder tree */}
      <div className="bg-surface-700 rounded-lg border border-white/[0.05] max-h-40 overflow-y-auto">
        {scanResult.folders.map((f, i) => (
          <div
            key={i}
            className="flex items-center justify-between px-3 py-2 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] transition-colors"
          >
            <div className="flex items-center gap-2">
              <FolderOpen size={12} className="text-brand-400 shrink-0" />
              <span className="text-sm text-slate-300 font-medium">{f.name}</span>
            </div>
            <div className="flex gap-2 text-xs text-slate-500">
              <span>{f.photo_count} photos</span>
              <span>·</span>
              <span>{f.size_human}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Upload config */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Telegram Channel ID</label>
          <input
            className="input"
            placeholder="-100123456789 or @channel"
            value={channelId}
            onChange={e => setChannelId(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Account</label>
          <select
            className="input"
            value={accountId}
            onChange={e => setAccountId(e.target.value)}
          >
            <option value="">Select account…</option>
            {accounts
              .filter(a => a.status === 'active')
              .map(a => (
                <option key={a.id} value={a.id}>
                  {a.display_name || a.phone}
                </option>
              ))}
          </select>
        </div>
      </div>

      <button
        className="btn-primary w-full justify-center"
        onClick={onStart}
        disabled={!channelId || !accountId}
      >
        <Upload size={15} />
        Start Upload
      </button>
    </div>
  )
}

function UploadControls() {
  const { isUploading, isPaused, pause, resume, stop } = useUploadStore()

  if (!isUploading) return null

  return (
    <div className="card">
      <div className="flex gap-2">
        {isPaused ? (
          <button className="btn-primary" onClick={resume}>
            <Play size={14} /> Resume
          </button>
        ) : (
          <button className="btn-secondary" onClick={pause}>
            <Pause size={14} /> Pause
          </button>
        )}
        <button className="btn-danger ml-auto" onClick={stop}>
          <Square size={14} /> Stop
        </button>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { scan, startUpload, scanResult, clearScan, isUploading } = useUploadStore()
  const [channelId, setChannelId] = useState('')
  const [accountId, setAccountId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const handleScan = async (path: string) => {
    setError(null)
    try {
      await scan(path)
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message)
    }
  }

  const handleStart = async () => {
    if (!scanResult || !channelId || !accountId) return
    setError(null)
    try {
      await startUpload({
        root_folder: scanResult.root,
        channel_id: channelId,
        account_id: accountId,
      })
      clearScan()
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message)
    }
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gradient">Dashboard</h1>
        <p className="text-slate-500 text-sm mt-1">
          Upload photo collections to Telegram as browsable albums
        </p>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400 animate-fade-in">
          <AlertCircle size={14} />
          {error}
          <button className="ml-auto" onClick={() => setError(null)}>
            <X size={14} />
          </button>
        </div>
      )}

      <ResumableBanner />

      <ProgressSection />
      <UploadControls />

      {!isUploading && (
        <>
          <FolderSelector onScan={handleScan} />
          <ScanPreview
            channelId={channelId}
            setChannelId={setChannelId}
            accountId={accountId}
            setAccountId={setAccountId}
            onStart={handleStart}
          />
        </>
      )}
    </div>
  )
}
