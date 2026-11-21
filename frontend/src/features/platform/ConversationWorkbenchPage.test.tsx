import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'

import { ConversationWorkbenchPage } from './ConversationWorkbenchPage'

const { api, identity } = vi.hoisted(() => ({
  api: {
    listDeployments: vi.fn(),
    getDigitalEmployee: vi.fn(),
    listConversations: vi.fn(),
    createSession: vi.fn(),
    createConversation: vi.fn(),
    bindConversationAgent: vi.fn(),
    listConversationMessages: vi.fn(),
    submitConversationTurn: vi.fn(),
    getRun: vi.fn(),
    getRunTrace: vi.fn(),
    getToolApproval: vi.fn(),
    approveToolApproval: vi.fn(),
    rejectToolApproval: vi.fn(),
  },
  identity: { fakeUser: 'admin-local' },
}))

vi.mock('../../api/client', () => api)
vi.mock('../management/TenantContext', () => ({
  useTenantContext: () => ({ fakeUser: identity.fakeUser }),
}))

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  identity.fakeUser = 'admin-local'
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ConversationWorkbenchPage />
    </QueryClientProvider>,
  )
  return { ...view, queryClient }
}

function baseApiState() {
  api.listDeployments.mockResolvedValue({
    items: [{ id: 'deploy-1', employee_id: 'employee-1', version: 3, status: 'ACTIVE' }],
  })
  api.getDigitalEmployee.mockResolvedValue({ id: 'employee-1', name: '海豚调度数仓开发' })
  api.listConversations.mockResolvedValue({
    items: [{
      conversation_id: 'conversation-1',
      title: 'ODS 到 DWD 工作流',
      employee: { employee_id: 'employee-1', employee_name: '海豚调度数仓开发' },
      session_id: 'session-1',
      last_message: { message_id: 'message-1', preview: '继续生成工作流', source: 'USER' },
      last_run: { run_id: 'run-1', status: 'AWAITING_APPROVAL' },
      pending_approval_count: 1,
      updated_at: '2026-11-19T08:30:00Z',
    }],
    next_cursor: null,
  })
  api.listConversationMessages.mockResolvedValue({
    items: [{ id: 'message-1', sender_id: 'admin-local', source: 'user', text: '继续生成工作流' }],
  })
  api.getRun.mockResolvedValue({
    id: 'run-1',
    session_id: 'session-1',
    status: 'AWAITING_APPROVAL',
    result: '请检查工作流预览。',
    snapshot: {
      employee_id: 'employee-1',
      employee_version: 3,
      skill_ids: ['skill-1'],
      tool_ids: ['tool-read', 'tool-preview'],
      execution_profile_id: 'profile-1',
    },
  })
  api.getRunTrace.mockResolvedValue({
    run: { id: 'run-1', status: 'AWAITING_APPROVAL' },
    spans: [{
      id: 'span-1',
      span_type: 'tool',
      status: 'awaiting_approval',
      sequence: 1,
      attributes: { tool_name: 'workflow_create', approval_ref: 'approval-1' },
    }],
    usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, source: 'test', estimated: true },
  })
  api.getToolApproval.mockResolvedValue({
    approval_id: 'approval-1',
    run_id: 'run-1',
    requested_by: 'admin-local',
    preview: '将创建 2 节点 OFFLINE 工作流。',
    preview_sha256: 'a'.repeat(64),
    expires_at: '2026-11-19T09:00:00Z',
    state: 'PENDING',
  })
}

test('loads tenant scoped Conversation, Run, Trace and exact Approval preview', async () => {
  baseApiState()
  const user = userEvent.setup()
  api.approveToolApproval.mockResolvedValue({ approval_id: 'approval-1', state: 'APPROVED' })

  renderPage()

  expect(await screen.findByText('ODS 到 DWD 工作流')).toBeInTheDocument()
  expect(await screen.findByText('请检查工作流预览。')).toBeInTheDocument()
  expect(await screen.findByText('将创建 2 节点 OFFLINE 工作流。')).toBeInTheDocument()
  expect(api.listConversations).toHaveBeenCalledWith('employee-1', expect.any(Object), 'admin-local')
  expect(api.listConversationMessages).toHaveBeenCalledWith('conversation-1', 'admin-local')
  expect(api.getRun).toHaveBeenCalledWith('run-1', 'admin-local')
  expect(api.getRunTrace).toHaveBeenCalledWith('run-1', 'admin-local')
  expect(api.getToolApproval).toHaveBeenCalledWith('approval-1', 'admin-local')

  await user.click(screen.getByRole('button', { name: '确认此预览' }))
  expect(api.approveToolApproval).toHaveBeenCalledWith(
    'approval-1',
    { preview_sha256: 'a'.repeat(64), idempotency_key: expect.any(String) },
    'admin-local',
  )
})

test('hides the Approval preview and decisions when the Run requester is another user', async () => {
  baseApiState()
  api.getToolApproval.mockResolvedValue({
    approval_id: 'approval-1', run_id: 'run-1', requested_by: 'member-local',
    preview: '同租户其他用户的预览正文', preview_sha256: 'b'.repeat(64),
    expires_at: '2026-11-19T09:00:00Z', state: 'PENDING',
  })

  renderPage()

  await screen.findByText('请检查工作流预览。')
  await waitFor(() => expect(api.getToolApproval).toHaveBeenCalledWith('approval-1', 'admin-local'))
  expect(screen.queryByText('同租户其他用户的预览正文')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '确认此预览' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '拒绝此预览' })).not.toBeInTheDocument()
})

test('rejects an Approval through the current requester identity', async () => {
  baseApiState()
  api.rejectToolApproval.mockResolvedValue({ approval_id: 'approval-1', state: 'REJECTED' })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole('button', { name: '拒绝此预览' }))
  expect(api.rejectToolApproval).toHaveBeenCalledWith('approval-1', 'admin-local')
})

test('submits a turn with an idempotency key and disables duplicate submission while pending', async () => {
  baseApiState()
  let finishSubmission: ((value: { conversation_id: string; message_id: string; run_id: string; status: string }) => void) | undefined
  api.submitConversationTurn.mockImplementation(() => new Promise((resolve) => { finishSubmission = resolve }))
  renderPage()

  const input = await screen.findByLabelText('提交任务')
  fireEvent.change(input, { target: { value: '生成订单明细表' } })
  const submit = screen.getByRole('button', { name: '提交任务' })
  await userEvent.click(submit)

  await waitFor(() => expect(api.submitConversationTurn).toHaveBeenCalledOnce())
  expect(api.submitConversationTurn).toHaveBeenCalledWith(
    'conversation-1',
    { text: '生成订单明细表', employee_id: 'employee-1', idempotency_key: expect.any(String) },
    'admin-local',
  )
  expect(screen.getByRole('button', { name: '提交中…' })).toBeDisabled()
  finishSubmission?.({ conversation_id: 'conversation-1', message_id: 'message-2', run_id: 'run-2', status: 'QUEUED' })
})

test('creates a new conversation from the selected active employee deployment', async () => {
  baseApiState()
  api.listConversations.mockResolvedValueOnce({ items: [], next_cursor: null })
  api.listConversationMessages.mockImplementation((conversationId: string) => Promise.resolve({
    items: conversationId === 'conversation-new' ? [] : [{
      id: 'message-1', sender_id: 'admin-local', source: 'user', text: '继续生成工作流',
    }],
  }))
  api.createSession.mockResolvedValue({ id: 'session-new', deployment_id: 'deploy-1' })
  api.createConversation.mockResolvedValue({ id: 'conversation-new', title: '新对话', is_group: false, status: 'ACTIVE' })
  api.bindConversationAgent.mockResolvedValue({ employee_id: 'employee-1', session_id: 'session-new', status: 'ACTIVE' })
  renderPage()

  await screen.findByText('还没有工作对话')
  await userEvent.click(screen.getByRole('button', { name: /新对话/ }))

  await waitFor(() => expect(api.bindConversationAgent).toHaveBeenCalledWith(
    'conversation-new',
    { employee_id: 'employee-1', employee_name: '海豚调度数仓开发', session_id: 'session-new' },
    'admin-local',
  ))
  expect(api.createSession).toHaveBeenCalledWith('deploy-1', 'admin-local')
  expect(api.createConversation).toHaveBeenCalledWith(expect.objectContaining({ title: expect.any(String) }), 'admin-local')
  expect(await screen.findByText('从这里继续与员工协作。提交任务后，查询、预览和运行状态会按顺序出现。')).toBeInTheDocument()
})

test('switching tenant changes query scope and removes the previous tenant conversations', async () => {
  baseApiState()
  api.listConversations.mockImplementation((employeeId: string, _query: unknown, fakeUser: string) => Promise.resolve({
    items: fakeUser === 'admin-local' ? [{
      conversation_id: 'tenant-a-conversation', title: '租户 A 工作',
      employee: { employee_id: employeeId, employee_name: '海豚调度数仓开发' },
      session_id: 'session-a', last_message: null, last_run: null,
      pending_approval_count: 0, updated_at: '2026-11-19T08:30:00Z',
    }] : [{
      conversation_id: 'tenant-b-conversation', title: '租户 B 工作',
      employee: { employee_id: employeeId, employee_name: '海豚调度数仓开发' },
      session_id: 'session-b', last_message: null, last_run: null,
      pending_approval_count: 0, updated_at: '2026-11-19T08:31:00Z',
    }],
    next_cursor: null,
  }))
  const view = renderPage()
  expect(await screen.findByText('租户 A 工作')).toBeInTheDocument()

  identity.fakeUser = 'admin-other'
  view.rerender(
    <QueryClientProvider client={view.queryClient}>
      <ConversationWorkbenchPage />
    </QueryClientProvider>,
  )

  expect(await screen.findByText('租户 B 工作')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText('租户 A 工作')).not.toBeInTheDocument())
  expect(api.listConversations).toHaveBeenCalledWith('employee-1', expect.any(Object), 'admin-other')
})
