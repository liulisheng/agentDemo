import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
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
  getCurrentFakeUser: () => string
  setFakeUser: (userId: string) => void
}

const fallbackIdentity = TENANT_IDENTITY_OPTIONS.find((item) => item.userId === DEFAULT_USER)
  ?? TENANT_IDENTITY_OPTIONS[0]

const TenantContext = createContext<TenantContextValue>({
  fakeUser: fallbackIdentity.userId,
  currentIdentity: fallbackIdentity,
  getCurrentFakeUser: () => fallbackIdentity.userId,
  setFakeUser: () => undefined,
})

/** 提供当前租户身份，并让异步操作在回调中读取最新身份。 */
export function TenantProvider({ children }: { children: ReactNode }) {
  const [fakeUser, setFakeUserState] = useState(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return TENANT_IDENTITY_OPTIONS.some((item) => item.userId === stored) ? stored! : DEFAULT_USER
  })

  const currentFakeUser = useRef(fakeUser)
  const getCurrentFakeUser = useCallback(() => currentFakeUser.current, [])
  const setFakeUser = useCallback((userId: string) => {
    if (TENANT_IDENTITY_OPTIONS.some((item) => item.userId === userId)) {
      // 渲染可能延后，异步回调需要立刻看到切换后的身份。
      currentFakeUser.current = userId
      setFakeUserState(userId)
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, fakeUser)
  }, [fakeUser])

  const value = useMemo<TenantContextValue>(() => {
    const currentIdentity = TENANT_IDENTITY_OPTIONS.find((item) => item.userId === fakeUser)
      ?? fallbackIdentity
    return {
      fakeUser,
      currentIdentity,
      getCurrentFakeUser,
      setFakeUser,
    }
  }, [fakeUser, getCurrentFakeUser, setFakeUser])

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>
}

export function useTenantContext() {
  return useContext(TenantContext)
}
