import { afterEach, expect, test, vi } from 'vitest'

import {
  approveToolApproval,
  bindConversationAgent,
  createConversation,
  createSession,
  getRun,
  getToolApproval,
  listConversationMessages,
  listConversations,
  rejectToolApproval,
  submitConversationTurn,
} from './client'

afterEach(() => vi.unstubAllGlobals())

function stubJsonResponse(value: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('conversation APIs keep the employee cursor and fake identity in the authorized request', async () => {
  const fetchMock = stubJsonResponse({ items: [], next_cursor: null })

  await listConversations('employee-1', { limit: 20, cursor: 'cursor-1' }, 'admin-other')

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/conversations?employee_id=employee-1&limit=20&cursor=cursor-1',
    expect.objectContaining({ headers: expect.any(Headers) }),
  )
  const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Headers
  expect(headers.get('X-Fake-User')).toBe('admin-other')
})

test('turn and setup APIs preserve the identifiers used to bind an employee session', async () => {
  const fetchMock = stubJsonResponse({ id: 'session-1' }, 201)

  await createSession('deployment-1', 'admin-local')
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/sessions', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ deployment_id: 'deployment-1' }),
  }))

  stubJsonResponse({ id: 'conversation-1' }, 201)
  await createConversation({ title: '新对话', is_group: false }, 'admin-local')
  expect(fetch).toHaveBeenCalledWith('/api/v1/conversations', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ title: '新对话', is_group: false }),
  }))

  const bindFetch = stubJsonResponse({ status: 'ACTIVE' }, 201)
  await bindConversationAgent('conversation-1', {
    employee_id: 'employee-1', employee_name: '数仓员工', session_id: 'session-1',
  }, 'admin-local')
  expect(bindFetch).toHaveBeenCalledWith('/api/v1/conversations/conversation-1/bindings', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ employee_id: 'employee-1', employee_name: '数仓员工', session_id: 'session-1' }),
  }))

  const turnFetch = stubJsonResponse({ status: 'QUEUED' }, 202)
  await submitConversationTurn('conversation-1', {
    text: '生成工作流', employee_id: 'employee-1', idempotency_key: 'turn-key',
  }, 'admin-local')
  expect(turnFetch).toHaveBeenCalledWith('/api/v1/conversations/conversation-1/turns', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ text: '生成工作流', employee_id: 'employee-1', idempotency_key: 'turn-key' }),
  }))
})

test('message, Run and Approval read/action APIs use encoded identifiers and exact preview digest', async () => {
  const fetchMock = stubJsonResponse({ items: [] })
  await listConversationMessages('conversation / 1', 'admin-local')
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/conversations/conversation%20%2F%201/messages', expect.any(Object))

  const runFetch = stubJsonResponse({ id: 'run-1', status: 'QUEUED' })
  await getRun('run/1', 'admin-local')
  expect(runFetch).toHaveBeenCalledWith('/api/v1/runs/run%2F1', expect.any(Object))

  const approvalFetch = stubJsonResponse({ approval_id: 'approval-1' })
  await getToolApproval('approval-1', 'admin-local')
  expect(approvalFetch).toHaveBeenCalledWith('/api/v1/tool-approvals/approval-1', expect.any(Object))

  const approveFetch = stubJsonResponse({ state: 'APPROVED' })
  await approveToolApproval('approval-1', {
    preview_sha256: 'a'.repeat(64), idempotency_key: 'approval-key',
  }, 'admin-local')
  expect(approveFetch).toHaveBeenCalledWith('/api/v1/tool-approvals/approval-1/approve', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ preview_sha256: 'a'.repeat(64), idempotency_key: 'approval-key' }),
  }))

  const rejectFetch = stubJsonResponse({ state: 'REJECTED' })
  await rejectToolApproval('approval-1', 'admin-local')
  expect(rejectFetch).toHaveBeenCalledWith('/api/v1/tool-approvals/approval-1/reject', expect.objectContaining({
    method: 'POST', body: JSON.stringify({}),
  }))
})
