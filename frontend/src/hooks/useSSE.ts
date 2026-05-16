import { useEffect, useRef } from 'react'
import { useUploadStore } from '@/store/uploadStore'
import { useLogStore } from '@/store/logStore'
import type { ProgressEvent } from '@/types'

/**
 * Connects to the backend SSE stream and dispatches events to stores.
 * Mount once at app root level.
 */
export function useSSE() {
  const setProgress = useUploadStore(s => s.setProgress)
  const setFloodwait = useUploadStore(s => s.setFloodwait)
  const appendRaw = useLogStore(s => s.appendRaw)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const connect = () => {
      const es = new EventSource('/api/upload/events')
      esRef.current = es

      es.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data) as ProgressEvent & { remaining_seconds?: number }

          switch (data.event_type) {
            case 'progress':
            case 'complete':
            case 'error':
              setProgress(data)
              if (data.message) {
                appendRaw(data.message, data.event_type === 'error' ? 'ERROR' : 'INFO')
              }
              if (data.event_type === 'complete' && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                new Notification('TeleGallery', {
                  body: data.message || 'Upload session finished.',
                })
              }
              if (data.event_type === 'error' && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                new Notification('TeleGallery — Error', { body: data.message || 'Upload error' })
              }
              break

            case 'floodwait':
              setFloodwait({
                accountId: data.floodwait_account || data.account_id || '',
                remainingSeconds:
                  data.floodwait_seconds ?? data.wait_seconds ?? 0,
              })
              appendRaw(
                `[WARN] FloodWait ${data.floodwait_seconds ?? data.wait_seconds ?? 0}s on account ${data.floodwait_account || data.account_id || ''}`,
                'WARN',
              )
              if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                new Notification('TeleGallery — FloodWait', {
                  body: `Paused ~${data.floodwait_seconds ?? data.wait_seconds ?? 0}s (Telegram rate limit).`,
                })
              }
              break

            case 'floodwait_tick':
              setFloodwait({
                accountId: data.floodwait_account || data.account_id || '',
                remainingSeconds: data.remaining_seconds ?? 0,
              })
              break

            case 'failover':
              appendRaw(
                `[INFO] ${data.message ?? `Failover ${data.from_account_id?.slice(0, 8) ?? '?'} → ${data.to_account_id?.slice(0, 8) ?? '?'}`}`,
                'INFO',
              )
              if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                new Notification('TeleGallery — Account switch', {
                  body: data.message || 'Upload continued on another Telegram account.',
                })
              }
              break

            case 'log':
              if (data.message) appendRaw(data.message, 'INFO')
              break
          }
        } catch {
          // ignore parse errors
        }
      }

      es.onerror = () => {
        es.close()
        // Reconnect after 3s
        setTimeout(connect, 3000)
      }
    }

    connect()
    return () => esRef.current?.close()
  }, [])
}
