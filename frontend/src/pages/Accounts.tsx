import { useState } from 'react'
import { Plus, Trash2, Wifi, WifiOff, Clock, Loader2, ShieldCheck, X, Users } from 'lucide-react'
import { useAccountStore } from '@/store/accountStore'
import { accountsApi } from '@/api/client'
import type { Account } from '@/types'
import clsx from 'clsx'
import { formatDistanceToNow } from 'date-fns'

function AccountCard({ account, onDelete }: { account: Account; onDelete: () => void }) {
  const [connecting, setConnecting] = useState(false)

  const handleConnect = async () => {
    setConnecting(true)
    try {
      await accountsApi.connect(account.id)
    } finally {
      setConnecting(false)
    }
  }

  const statusBadge = {
    active: <span className="badge badge-green"><Wifi size={10} />Active</span>,
    cooldown: <span className="badge badge-yellow"><Clock size={10} />Cooldown</span>,
    disconnected: <span className="badge badge-gray"><WifiOff size={10} />Offline</span>,
  }[account.status]

  return (
    <div className="card flex items-center gap-4 animate-fade-in">
      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-500 to-violet-600 flex items-center justify-center text-white font-bold text-sm shrink-0">
        {(account.display_name || account.phone).charAt(0).toUpperCase()}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="font-medium text-slate-100">{account.display_name || account.phone}</p>
          {statusBadge}
        </div>
        <p className="text-xs text-slate-500 mt-0.5">{account.phone}</p>
        {account.floodwait_until && (
          <p className="text-xs text-amber-500 mt-0.5">
            Cooldown until {new Date(account.floodwait_until).toLocaleTimeString()}
          </p>
        )}
        <p className="text-xs text-slate-600 mt-0.5">
          {account.total_uploaded.toLocaleString()} photos uploaded •{' '}
          added {formatDistanceToNow(new Date(account.created_at), { addSuffix: true })}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {account.status === 'disconnected' && (
          <button
            className="btn-secondary text-xs py-1.5"
            onClick={handleConnect}
            disabled={connecting}
          >
            {connecting ? <Loader2 size={12} className="animate-spin" /> : <Wifi size={12} />}
            Connect
          </button>
        )}
        <button className="btn-ghost text-red-400 hover:text-red-300" onClick={onDelete}>
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  )
}

type LoginStep = 'idle' | 'form' | 'code' | 'password'

function AddAccountModal({ onClose, onAdded }: {
  onClose: () => void
  onAdded: (a: Account) => void
}) {
  const [step, setStep] = useState<LoginStep>('form')
  const [phone, setPhone] = useState('')
  const [apiId, setApiId] = useState('')
  const [apiHash, setApiHash] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [codeHash, setCodeHash] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sendCode = async () => {
    setLoading(true); setError(null)
    try {
      const res = await accountsApi.sendCode(phone, Number(apiId), apiHash, displayName)
      setCodeHash(res.phone_code_hash)
      setStep('code')
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  const verify = async () => {
    setLoading(true); setError(null)
    try {
      const account = await accountsApi.verify(phone, codeHash, code, password || undefined)
      onAdded(account)
      onClose()
    } catch (e: any) {
      const detail = e.response?.data?.detail || e.message
      if (detail?.includes('password')) setStep('password')
      else setError(detail)
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in">
      <div className="card w-full max-w-md mx-4 space-y-4 animate-slide-up">
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-slate-100">Add Telegram Account</h2>
          <button className="btn-ghost" onClick={onClose}><X size={16} /></button>
        </div>

        {error && (
          <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {step === 'form' && (
          <>
            <div>
              <label className="label">Display Name (optional)</label>
              <input className="input" placeholder="Wedding Account" value={displayName} onChange={e => setDisplayName(e.target.value)} />
            </div>
            <div>
              <label className="label">Phone Number</label>
              <input className="input" placeholder="+1234567890" value={phone} onChange={e => setPhone(e.target.value)} />
            </div>
            <div>
              <label className="label">API ID</label>
              <input className="input" placeholder="12345678" value={apiId} onChange={e => setApiId(e.target.value)} />
            </div>
            <div>
              <label className="label">API Hash</label>
              <input className="input font-mono text-xs" placeholder="abc123..." value={apiHash} onChange={e => setApiHash(e.target.value)} />
            </div>
            <p className="text-xs text-slate-500">
              Get API credentials at{' '}
              <a href="https://my.telegram.org" target="_blank" className="text-brand-400 hover:underline">my.telegram.org</a>
            </p>
            <button className="btn-primary w-full justify-center" onClick={sendCode} disabled={!phone || !apiId || !apiHash || loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
              Send Verification Code
            </button>
          </>
        )}

        {step === 'code' && (
          <>
            <p className="text-sm text-slate-400">Enter the code sent to <span className="text-slate-200 font-medium">{phone}</span></p>
            <input className="input text-2xl font-mono tracking-[0.5em] text-center" maxLength={6} placeholder="• • • • • •" value={code} onChange={e => setCode(e.target.value)} />
            <button className="btn-primary w-full justify-center" onClick={verify} disabled={code.length < 5 || loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : 'Verify'}
            </button>
          </>
        )}

        {step === 'password' && (
          <>
            <p className="text-sm text-slate-400">Two-step verification required</p>
            <input className="input" type="password" placeholder="Your 2FA password" value={password} onChange={e => setPassword(e.target.value)} />
            <button className="btn-primary w-full justify-center" onClick={verify} disabled={!password || loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : 'Confirm'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

export default function Accounts() {
  const { accounts, fetch, remove, addOrUpdate } = useAccountStore()
  const [showAdd, setShowAdd] = useState(false)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gradient">Accounts</h1>
          <p className="text-slate-500 text-sm mt-1">Manage your Telegram accounts</p>
        </div>
        <button className="btn-primary" onClick={() => setShowAdd(true)}>
          <Plus size={14} /> Add Account
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className="card text-center py-12 text-slate-500">
          <Users size={32} className="mx-auto mb-3 opacity-40" />
          <p className="font-medium">No accounts yet</p>
          <p className="text-sm mt-1">Add a Telegram account to start uploading</p>
        </div>
      ) : (
        <div className="space-y-3">
          {accounts.map(a => (
            <AccountCard key={a.id} account={a} onDelete={() => remove(a.id)} />
          ))}
        </div>
      )}

      {showAdd && (
        <AddAccountModal
          onClose={() => setShowAdd(false)}
          onAdded={account => { addOrUpdate(account); setShowAdd(false) }}
        />
      )}
    </div>
  )
}
