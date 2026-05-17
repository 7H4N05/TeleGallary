import axios from 'axios'
import type {
  Account,
  AppSettings,
  FailedFile,
  FolderRecord,
  LogEntry,
  ScanResult,
  Session,
} from '@/types'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// ─── Upload ──────────────────────────────────────────────────────────────────

export const uploadApi = {
  getResumable: (): Promise<Session | null> =>
    api.get('/upload/resumable').then(r => r.data),

  scan: (rootFolder: string): Promise<ScanResult> =>
    api.post('/upload/scan', { root_folder: rootFolder }).then(r => r.data),

  validateChannel: (channelId: string, accountId: string): Promise<{
    ok: boolean
    channel_id: string
    title: string
    username?: string
  }> =>
    api.post('/upload/validate-channel', {
      channel_id: channelId,
      account_id: accountId,
    }).then(r => r.data),

  start: (payload: {
    root_folder: string
    channel_id: string
    account_id: string
    session_name?: string
  }): Promise<{ session_id: string; status: string }> =>
    api.post('/upload/start', payload).then(r => r.data),

  pause: (sessionId: string) =>
    api.post(`/upload/${sessionId}/pause`).then(r => r.data),

  resume: (sessionId: string) =>
    api.post(`/upload/${sessionId}/resume`).then(r => r.data),

  stop: (sessionId: string) =>
    api.post(`/upload/${sessionId}/stop`).then(r => r.data),

  retry: (sessionId: string, fileIds: string[]) =>
    api.post(`/upload/${sessionId}/retry`, { file_ids: fileIds }).then(r => r.data),
}

// ─── Accounts ────────────────────────────────────────────────────────────────

export const accountsApi = {
  list: (): Promise<Account[]> =>
    api.get('/accounts/').then(r => r.data),

  sendCode: (phone: string, apiId: number, apiHash: string, displayName?: string) =>
    api.post('/accounts/send-code', {
      phone,
      api_id: apiId,
      api_hash: apiHash,
      display_name: displayName,
    }).then(r => r.data),

  verify: (
    phone: string,
    phoneCodeHash: string,
    code: string,
    password?: string,
  ): Promise<Account> =>
    api.post('/accounts/verify', {
      phone,
      phone_code_hash: phoneCodeHash,
      code,
      password,
    }).then(r => r.data),

  connect: (accountId: string) =>
    api.post(`/accounts/${accountId}/connect`).then(r => r.data),

  delete: (accountId: string) =>
    api.delete(`/accounts/${accountId}`).then(r => r.data),
}

// ─── Sessions ────────────────────────────────────────────────────────────────

export const sessionsApi = {
  list: (): Promise<Session[]> =>
    api.get('/sessions/').then(r => r.data),

  get: (sessionId: string): Promise<Session> =>
    api.get(`/sessions/${sessionId}`).then(r => r.data),

  getFailed: (sessionId: string): Promise<FailedFile[]> =>
    api.get(`/sessions/${sessionId}/failed`).then(r => r.data),
}

// ─── Folders ─────────────────────────────────────────────────────────────────

export const foldersApi = {
  list: (sessionId: string): Promise<FolderRecord[]> =>
    api.get(`/folders/${sessionId}`).then(r => r.data),
}

// ─── Settings ────────────────────────────────────────────────────────────────

export const settingsApi = {
  get: (): Promise<AppSettings> => api.get('/settings/').then(r => r.data),

  patch: (body: Partial<AppSettings>): Promise<AppSettings> =>
    api.patch('/settings/', body).then(r => r.data),
}

// ─── Logs ────────────────────────────────────────────────────────────────────

export const logsApi = {
  list: (sessionId?: string, limit = 200): Promise<LogEntry[]> => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (sessionId) params.set('session_id', sessionId)
    return api.get(`/logs/?${params}`).then(r => r.data)
  },
}

// ─── Health ──────────────────────────────────────────────────────────────────

export const healthApi = {
  check: () => api.get('/health').then(r => r.data),
}

export default api
