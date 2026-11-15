import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

const STORAGE_KEY = 'agent-platform.fake-user'
const DEFAULT_USER = import.meta.env.VITE_FAKE_USER ?? 'admin-local'

export interface TenantIdentityOption {
  userId: string
  label: string
  tenantId: string
  tenantName: string
  roleName: string
}

export const TENANT_IDENTITY_OPTIONS: TenantIdentityOption[] = [
  {
    userId: 'admin-local',
    label: '租户 A · 管理员',
    tenantId: 'tenant-a',
    tenantName: '租户 A',
    roleName: '管理员',
  },
  {
    userId: 'member-local',
    label: '租户 A · 成员',
    tenantId: 'tenant-a',
    tenantName: '租户 A',
    roleName: '成员',
  },
  {
    userId: 'admin-other',
    label: '租户 B · 管理员',
    tenantId: 'tenant-b',
    tenantName: '租户 B',
    roleName: '管理员',
  },
]

interface TenantContextValue {
  fakeUser: string
  currentIdentity: TenantIdentityOption
  setFakeUser: (userId: string) => void
}

const fallbackIdentity = TENANT_IDENTITY_OPTIONS.find((item) => item.userId === DEFAULT_USER)
  ?? TENANT_IDENTITY_OPTIONS[0]

const TenantContext = createContext<TenantContextValue>({
  fakeUser: fallbackIdentity.userId,
  currentIdentity: fallbackIdentity,
  setFakeUser: () => undefined,
})

export function TenantProvider({ children }: { children: ReactNode }) {
  const [fakeUser, setFakeUserState] = useState(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return TENANT_IDENTITY_OPTIONS.some((item) => item.userId === stored) ? stored! : DEFAULT_USER
  })

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, fakeUser)
  }, [fakeUser])

  const value = useMemo<TenantContextValue>(() => {
    const currentIdentity = TENANT_IDENTITY_OPTIONS.find((item) => item.userId === fakeUser)
      ?? fallbackIdentity
    return {
      fakeUser,
      currentIdentity,
      setFakeUser: (userId: string) => {
        if (TENANT_IDENTITY_OPTIONS.some((item) => item.userId === userId)) {
          setFakeUserState(userId)
        }
      },
    }
  }, [fakeUser])

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>
}

export function useTenantContext() {
  return useContext(TenantContext)
}
