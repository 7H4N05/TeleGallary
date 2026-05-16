import { create } from 'zustand'
import type { LogEntry } from '@/types'

interface LogStore {
  logs: LogEntry[]
  append: (log: LogEntry) => void
  appendMany: (logs: LogEntry[]) => void
  appendRaw: (message: string, level?: LogEntry['level']) => void
  clear: () => void
}

let _id = 0
const fakeId = () => `local-${++_id}`

export const useLogStore = create<LogStore>((set) => ({
  logs: [],

  append: (log) =>
    set(s => ({ logs: [...s.logs.slice(-999), log] })),

  appendMany: (logs) =>
    set(s => ({ logs: [...s.logs, ...logs].slice(-1000) })),

  appendRaw: (message, level = 'INFO') =>
    set(s => ({
      logs: [
        ...s.logs.slice(-999),
        {
          id: fakeId(),
          session_id: null,
          level,
          message,
          context: null,
          created_at: new Date().toISOString(),
        },
      ],
    })),

  clear: () => set({ logs: [] }),
}))
