import { create } from 'zustand'
import type { Account } from '@/types'
import { accountsApi } from '@/api/client'

interface AccountStore {
  accounts: Account[]
  loading: boolean
  error: string | null
  fetch: () => Promise<void>
  remove: (id: string) => Promise<void>
  addOrUpdate: (account: Account) => void
}

export const useAccountStore = create<AccountStore>((set, get) => ({
  accounts: [],
  loading: false,
  error: null,

  fetch: async () => {
    set({ loading: true, error: null })
    try {
      const accounts = await accountsApi.list()
      set({ accounts, loading: false })
    } catch (e: any) {
      set({ error: e.message, loading: false })
    }
  },

  remove: async (id: string) => {
    await accountsApi.delete(id)
    set(s => ({ accounts: s.accounts.filter(a => a.id !== id) }))
  },

  addOrUpdate: (account: Account) => {
    set(s => {
      const exists = s.accounts.find(a => a.id === account.id)
      if (exists) {
        return { accounts: s.accounts.map(a => a.id === account.id ? account : a) }
      }
      return { accounts: [...s.accounts, account] }
    })
  },
}))
