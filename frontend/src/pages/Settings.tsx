import { useEffect, useState } from 'react'
import { Save, Moon, Sun, Eye, Loader2, AlertCircle } from 'lucide-react'
import { settingsApi } from '@/api/client'
import type { AppSettings } from '@/types'

const THEME_KEY = 'telegallery-theme'

function applyTheme(dark: boolean) {
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light')
}

export default function Settings() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [startTpl, setStartTpl] = useState('')
  const [endTpl, setEndTpl] = useState('')
  const [appName, setAppName] = useState('')
  const [dark, setDark] = useState(() => {
    if (typeof localStorage === 'undefined') return true
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light') return false
    if (v === 'dark') return true
    return document.documentElement.classList.contains('dark')
  })

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const s: AppSettings = await settingsApi.get()
        if (cancelled) return
        setStartTpl(s.folder_start_template)
        setEndTpl(s.folder_end_template)
        setAppName(s.app_name)
      } catch (e: unknown) {
        if (!cancelled) setError((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed to load settings')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const preview = (tpl: string) => tpl.replace('{folder_name}', 'HALDI')

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await settingsApi.patch({
        app_name: appName,
        folder_start_template: startTpl,
        folder_end_template: endTpl,
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e: unknown) {
      const d = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(typeof d === 'string' ? d : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const toggleTheme = () => {
    const next = !dark
    setDark(next)
    applyTheme(next)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gradient">Settings</h1>
        <p className="text-slate-500 text-sm mt-1">Configure TeleGallery behaviour (stored in config.yaml)</p>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3 text-sm text-red-400">
          <AlertCircle size={14} />
          {error}
        </div>
      )}

      {loading ? (
        <div className="card flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 className="animate-spin" size={18} />
          Loading settings…
        </div>
      ) : (
        <>
          <div className="card space-y-3">
            <h2 className="font-semibold text-slate-100">Appearance</h2>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-200">Theme</p>
                <p className="text-xs text-slate-500 mt-0.5">Saved in this browser (localStorage)</p>
              </div>
              <button type="button" className="btn-secondary" onClick={toggleTheme}>
                {dark ? <Sun size={14} /> : <Moon size={14} />}
                {dark ? 'Light Mode' : 'Dark Mode'}
              </button>
            </div>
          </div>

          <div className="card space-y-4">
            <h2 className="font-semibold text-slate-100">App &amp; folder markers</h2>
            <div>
              <label className="label">Display name (sidebar / API)</label>
              <input className="input" value={appName} onChange={e => setAppName(e.target.value)} />
            </div>

            <p className="text-xs text-slate-500">
              Folder templates use{' '}
              <code className="bg-surface-600 px-1 rounded text-brand-300">{'{folder_name}'}</code> — saved to{' '}
              <code className="text-slate-400">config.yaml</code>. Restart upload engine if it was already running.
            </p>

            <div>
              <label className="label">Start marker</label>
              <input className="input font-mono text-sm" value={startTpl} onChange={e => setStartTpl(e.target.value)} />
              <div className="flex items-center gap-1.5 mt-2 text-xs text-slate-500">
                <Eye size={11} />
                <span className="font-mono">{preview(startTpl)}</span>
              </div>
            </div>

            <div>
              <label className="label">End marker</label>
              <input className="input font-mono text-sm" value={endTpl} onChange={e => setEndTpl(e.target.value)} />
              <div className="flex items-center gap-1.5 mt-2 text-xs text-slate-500">
                <Eye size={11} />
                <span className="font-mono">{preview(endTpl)}</span>
              </div>
            </div>

            <button type="button" className="btn-primary" onClick={save} disabled={saving}>
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              {saved ? 'Saved ✓' : 'Save to config.yaml'}
            </button>
          </div>
        </>
      )}

      <div className="card space-y-2">
        <h2 className="font-semibold text-slate-100">About</h2>
        <div className="text-sm text-slate-400 space-y-1">
          <p>{appName || 'TeleGallery'} — Telegram photo gallery uploader</p>
          <p className="text-xs text-slate-600 mt-2">Open source • MIT • FastAPI + React + Pyrogram</p>
        </div>
      </div>
    </div>
  )
}
