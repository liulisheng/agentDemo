import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, test, vi } from 'vitest'

import { EmployeeGallery } from './EmployeeGallery'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: 'employee-admin',
            tenant_id: 'tenant-a',
            name: '行政助手',
            role_name: '事务管家',
            description: '处理办公事务。',
            status: 'ACTIVE',
            published_to_gallery: true,
            resource_counts: { knowledge_base: 2, skill: 1, sop: 3 },
          },
        ],
      }),
    }),
  )
})

function renderGallery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <EmployeeGallery />
    </QueryClientProvider>,
  )
}

test('renders the employee gallery and resource counts', async () => {
  renderGallery()

  expect(await screen.findByRole('heading', { name: '开放广场平台' })).toBeInTheDocument()
  expect(await screen.findByText('行政助手')).toBeInTheDocument()
  expect(await screen.findByText('2 资料')).toBeInTheDocument()
  expect(await screen.findByText('1 Skill')).toBeInTheDocument()
  expect(await screen.findByText('3 SOP')).toBeInTheDocument()
})
