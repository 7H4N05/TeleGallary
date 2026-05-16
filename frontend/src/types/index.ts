// Shared TypeScript types for TeleGallery frontend

export interface AppSettings {
  app_name: string
  folder_start_template: string
  folder_end_template: string
}

export interface Account {
  id: string
  phone: string
  display_name: string | null
  status: 'active' | 'cooldown' | 'disconnected'
  floodwait_until: string | null
  total_uploaded: number
  created_at: string
}

export interface Session {
  id: string
  name: string
  root_folder: string
  channel_id: string
  account_id: string | null
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'stopped'
  started_at: string | null
  completed_at: string | null
  total_files: number
  uploaded_files: number
  failed_files: number
  created_at: string
}

export interface FolderInfo {
  path: string
  name: string
  photo_count: number
  size_bytes: number
  size_human: string
}

export interface ScanResult {
  root: string
  folders: FolderInfo[]
  total_files: number
  total_size_bytes: number
  total_size_human: string
}

export interface ProgressEvent {
  event_type: string
  session_id: string
  current_folder?: string
  current_file?: string
  current_album?: number
  uploaded_files: number
  total_files: number
  failed_files: number
  speed_bps?: number
  eta_seconds?: number
  floodwait_seconds?: number
  floodwait_account?: string
  message?: string
  timestamp: string
  account_id?: string
  wait_seconds?: number
  remaining_seconds?: number
  from_account_id?: string
  to_account_id?: string
}

export interface LogEntry {
  id: string
  session_id: string | null
  level: 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'
  message: string
  context: Record<string, unknown> | null
  created_at: string
}

export interface FailedFile {
  id: string
  file_id: string
  session_id: string
  filename: string
  path: string
  error_type: string
  error_message: string
  occurred_at: string
  resolved: boolean
}

export interface FolderRecord {
  id: string
  session_id: string
  path: string
  name: string
  order_index: number
  status: 'pending' | 'started' | 'completed' | 'failed'
  start_msg_id: number | null
  end_msg_id: number | null
  total_files: number
  uploaded_files: number
}
