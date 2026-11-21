import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'

import { App } from './App'

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({ items: [] }),
  })))
})

function renderApp(path: string) {
  window.history.replaceState({}, '', path)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  )
}

test('application root opens the digital employee management console', async () => {
  renderApp('/')

  expect(await screen.findByRole('region', { name: '数字员工目录' })).toBeInTheDocument()
  const managementNav = screen.getByRole('navigation', { name: '管理导航' })
  expect(within(managementNav).getByRole('link', { name: '资源中心' })).toHaveAttribute('href', '/enterprise/resources')
})

test('legacy open gallery route redirects to Resource Center', async () => {
  renderApp('/workspace/gallery')

  expect(await screen.findByRole('region', { name: '资源中心' })).toBeInTheDocument()
  expect(screen.queryByText('开放广场平台')).not.toBeInTheDocument()
})

test('employee workbench route opens the conversation, timeline and snapshot workspace', () => {
  renderApp('/enterprise/workbench')

  expect(screen.getByRole('navigation', { name: '对话列表' })).toBeInTheDocument()
  expect(screen.getByRole('main', { name: '对话与运行时间线' })).toBeInTheDocument()
  expect(screen.getByRole('complementary', { name: '本次 Run 快照' })).toBeInTheDocument()
  const managementNav = screen.getByRole('navigation', { name: '管理导航' })
  expect(within(managementNav).getByRole('link', { name: '运行工作台' })).toHaveAttribute(
    'href',
    '/enterprise/workbench',
  )
})
