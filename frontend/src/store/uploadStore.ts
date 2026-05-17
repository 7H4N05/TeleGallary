import { create } from 'zustand'
import type { ProgressEvent, ScanResult, Session } from '@/types'
import { sessionsApi, uploadApi } from '@/api/client'

interface FloodwaitState {
  accountId: string
  remainingSeconds: number
}

interface UploadStore {
  // Session list
  sessions: Session[]
  resumableSession: Session | null
  // Active session
  activeSessionId: string | null
  activeSession: Session | null
  // Scan
  scanResult: ScanResult | null
  scanning: boolean
  // Progress
  progress: ProgressEvent | null
  // Floodwait
  floodwait: FloodwaitState | null
  // Control state
  isUploading: boolean
  isPaused: boolean

  // Actions
  fetchSessions: () => Promise<void>
  scan: (rootFolder: string) => Promise<ScanResult>
  startUpload: (payload: {
    root_folder: string
    channel_id: string
    account_id: string
    session_name?: string
  }) => Promise<string>
  pause: () => Promise<void>
  resume: () => Promise<void>
  stop: () => Promise<void>
  fetchResumable: () => Promise<void>
  resumeById: (sessionId: string) => Promise<void>
  setProgress: (e: ProgressEvent) => void
  setFloodwait: (fw: FloodwaitState | null) => void
  setActiveSession: (id: string | null) => void
  clearScan: () => void
}

export const useUploadStore = create<UploadStore>((set, get) => ({
  sessions: [],
  resumableSession: null,
  activeSessionId: null,
  activeSession: null,
  scanResult: null,
  scanning: false,
  progress: null,
  floodwait: null,
  isUploading: false,
  isPaused: false,

  fetchSessions: async () => {
    const sessions = await sessionsApi.list()
    set({ sessions })
  },

  fetchResumable: async () => {
    try {
      const s = await uploadApi.getResumable()
      set({ resumableSession: s })
    } catch {
      set({ resumableSession: null })
    }
  },

  resumeById: async (sessionId: string) => {
    await uploadApi.resume(sessionId)
    set({
      activeSessionId: sessionId,
      isUploading: true,
      isPaused: false,
      resumableSession: null,
    })
    void get().fetchSessions()
  },

  scan: async (rootFolder: string) => {
    set({ scanning: true })
    const result = await uploadApi.scan(rootFolder)
    set({ scanResult: result, scanning: false })
    return result
  },

  startUpload: async (payload) => {
    const res = await uploadApi.start(payload)
    set({
      activeSessionId: res.session_id,
      isUploading: true,
      isPaused: false,
      resumableSession: null,
    })
    return res.session_id
  },

  pause: async () => {
    const id = get().activeSessionId
    if (id) {
      await uploadApi.pause(id)
      set({ isPaused: true })
      void get().fetchResumable()
    }
  },

  resume: async () => {
    const id = get().activeSessionId
    if (id) {
      await uploadApi.resume(id)
      set({ isPaused: false })
      void get().fetchResumable()
    }
  },

  stop: async () => {
    const id = get().activeSessionId
    if (id) {
      await uploadApi.stop(id)
      set({ isUploading: false, isPaused: false, activeSessionId: null })
      void get().fetchResumable()
    }
  },

  setProgress: (e: ProgressEvent) => {
    set({ progress: e })
    if (e.event_type === 'complete') {
      void get().fetchSessions()
      void get().fetchResumable()
      set({ isUploading: false })
    }
  },

  setFloodwait: (fw) => set({ floodwait: fw }),

  setActiveSession: (id) => set({ activeSessionId: id }),

  clearScan: () => set({ scanResult: null }),
}))
