import { BrowserRouter, Route, Routes, NavLink, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import {
  LayoutDashboard,
  Users,
  ScrollText,
  AlertTriangle,
  History,
  Settings,
  Zap,
} from 'lucide-react'
import { useSSE } from '@/hooks/useSSE'
import { useAccountStore } from '@/store/accountStore'
import { useUploadStore } from '@/store/uploadStore'
import Dashboard from '@/pages/Dashboard'
import Accounts from '@/pages/Accounts'
import Logs from '@/pages/Logs'
import Failed from '@/pages/Failed'
import HistoryPage from '@/pages/History'
import SettingsPage from '@/pages/Settings'
import { FloodwaitBanner } from '@/components/FloodwaitBanner'
import clsx from 'clsx'

const NAV = [
  { to: '/',         icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/accounts', icon: Users,           label: 'Accounts' },
  { to: '/logs',     icon: ScrollText,      label: 'Logs' },
  { to: '/failed',   icon: AlertTriangle,   label: 'Failed' },
  { to: '/history',  icon: History,         label: 'History' },
  { to: '/settings', icon: Settings,        label: 'Settings' },
]

function Sidebar() {
  const { accounts } = useAccountStore()
  const { isUploading, isPaused } = useUploadStore()
  const location = useLocation()

  return (
    <aside className="w-56 shrink-0 bg-surface-800 border-r border-white/[0.06] flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="px-4 py-5 border-b border-white/[0.06]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-violet-600 flex items-center justify-center shadow-lg">
            <Zap size={16} className="text-white" />
          </div>
          <div>
            <p className="text-sm font-bold text-slate-100 leading-none">TeleGallery</p>
            <p className="text-[10px] text-slate-500 mt-0.5">Photo Uploader</p>
          </div>
        </div>
      </div>

      {/* Upload status pill */}
      {isUploading && (
        <div className="mx-3 mt-3">
          <div className={clsx(
            'flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium',
            isPaused
              ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
              : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
          )}>
            <span className={clsx(
              'w-1.5 h-1.5 rounded-full',
              isPaused ? 'bg-amber-400' : 'bg-emerald-400 animate-pulse'
            )} />
            {isPaused ? 'Paused' : 'Uploading…'}
          </div>
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {NAV.map(({ to, icon: Icon, label }) => {
          const active = location.pathname === to
          return (
            <NavLink
              key={to}
              to={to}
              className={active ? 'nav-link-active' : 'nav-link'}
            >
              <Icon size={16} />
              <span>{label}</span>
              {label === 'Accounts' && accounts.length > 0 && (
                <span className="ml-auto text-[10px] bg-surface-600 text-slate-400 rounded-full px-1.5 py-0.5">
                  {accounts.length}
                </span>
              )}
            </NavLink>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-white/[0.06]">
        <p className="text-[10px] text-slate-600">v1.0.0 • Open Source</p>
      </div>
    </aside>
  )
}

function AppInner() {
  useSSE()
  const fetchAccounts = useAccountStore(s => s.fetch)
  const fetchSessions = useUploadStore(s => s.fetchSessions)
  const fetchResumable = useUploadStore(s => s.fetchResumable)

  useEffect(() => {
    const v = localStorage.getItem('telegallery-theme')
    if (v === 'light') document.documentElement.classList.remove('dark')
    else document.documentElement.classList.add('dark')
  }, [])

  useEffect(() => {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      void Notification.requestPermission()
    }
  }, [])

  useEffect(() => {
    fetchAccounts()
    fetchSessions()
    fetchResumable()
  }, [])

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <FloodwaitBanner />
        <div className="max-w-6xl mx-auto px-6 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/failed" element={<Failed />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppInner />
    </BrowserRouter>
  )
}
