import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { RunTracePage } from './RunTrace'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        run: {
          id: 'run-1',
          status: 'succeeded',
          result: 'fake: hello',
          snapshot: { employee_version: 1, skill_ids: ['skill-1'], tool_ids: ['tool-1'] },
        },
        spans: [
          { id: 'span-1', span_type: 'session', status: 'started', sequence: 1, attributes: {} },
          { id: 'span-2', span_type: 'runtime', status: 'succeeded', sequence: 2, attributes: {} },
        ],
        usage: { input_tokens: 2, output_tokens: 2, total_tokens: 4, source: 'ESTIMATED', estimated: true },
      }),
    }),
  )
})

function renderTrace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/workspace/runs/run-1/trace']}>
        <Routes>
          <Route path="/workspace/runs/:runId/trace" element={<RunTracePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('renders run result, snapshot and ordered trace spans', async () => {
  renderTrace()

  expect(await screen.findByRole('heading', { name: 'Run Trace' })).toBeInTheDocument()
  expect(await screen.findByText('fake: hello')).toBeInTheDocument()
  expect(screen.getByText('Session')).toBeInTheDocument()
  expect(screen.getByText('Runtime')).toBeInTheDocument()
  expect(screen.getByText('4 tokens')).toBeInTheDocument()
})
