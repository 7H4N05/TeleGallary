import { useUploadStore } from '@/store/uploadStore'
import { Clock } from 'lucide-react'

export function FloodwaitBanner() {
  const floodwait = useUploadStore(s => s.floodwait)
  if (!floodwait || floodwait.remainingSeconds <= 0) return null

  const mins = Math.floor(floodwait.remainingSeconds / 60)
  const secs = floodwait.remainingSeconds % 60
  const timeStr = mins > 0
    ? `${mins}m ${secs}s`
    : `${secs}s`

  return (
    <div className="bg-amber-500/10 border-b border-amber-500/20 px-6 py-2.5 flex items-center gap-3 animate-fade-in">
      <Clock size={14} className="text-amber-400 shrink-0" />
      <p className="text-sm text-amber-300 font-medium">
        Telegram FloodWait — resuming in{' '}
        <span className="font-mono font-bold">{timeStr}</span>
        {floodwait.accountId && (
          <span className="text-amber-500 ml-2">({floodwait.accountId.slice(0, 8)}…)</span>
        )}
      </p>
    </div>
  )
}
