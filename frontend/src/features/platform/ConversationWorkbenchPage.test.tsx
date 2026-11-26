import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
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
    getDeploymentRuntimeConfig: vi.fn(),
    saveDeploymentRuntimeConfig: vi.fn(),
    listExecutionProfiles: vi.fn(),
    listExecutionProfileVersions: vi.fn(),
    listSelectableModelRoutes: vi.fn(),
    listEngineProviders: vi.fn(),
    getEffectiveEngineProfileDefault: vi.fn(),
    getEffectiveModelRouteDefault: vi.fn(),
    getEffectiveRuntimeStrategyDefault: vi.fn(),
    getRuntimeStrategyDefault: vi.fn(),
    setRuntimeStrategyDefault: vi.fn(),
    listTeams: vi.fn(),
    getTeam: vi.fn(),
    listTeamWorkerPools: vi.fn(),
  },
  identity: { fakeUser: 'admin-local' },
}))

vi.mock('../../api/client', () => api)
vi.mock('../management/TenantContext', () => ({
  useTenantContext: () => ({ fakeUser: identity.fakeUser, currentIdentity: { roleName: '管理员', label: '租户 A · 管理员' } }),
}))

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  identity.fakeUser = 'admin-local'
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><ConversationWorkbenchPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...view, queryClient }
}

function baseApiState() {
  api.listDeployments.mockResolvedValue({
    items: [{ id: 'deploy-1', employee_id: 'employee-1', version: 3, status: 'ACTIVE', runtime_config_version: 1 }],
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
      execution_profile_version: 2,
      engine_name: 'deepseek_harness',
      model_route_id: 'route-1',
      runtime_config_version: 4,
      runtime_profile_source: 'DEPLOYMENT_OVERRIDE',
      runtime_model_route_source: 'TENANT_DEFAULT',
      model_route_snapshot: { route_id: 'route-1', route_version: 3, route_alias: '数仓模型', provider_kind: 'deepseek', model_id: 'deepseek-chat', protocol: 'openai-chat-completions' },
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
  api.getDeploymentRuntimeConfig.mockResolvedValue({ deployment_id: 'deploy-1', deployment_status: 'ACTIVE', runtime_config_version: 1, revision: {
    config_version: 1, mode: 'INHERIT', execution_profile_id: null, execution_profile_version: null,
    model_route_id: null, workspace_profile_id: null, workspace_profile_version: null,
    sandbox_profile_id: null, sandbox_profile_version: null, approval_policy: {}, runtime_options: {}, created_by: 'admin-local',
  } })
  api.saveDeploymentRuntimeConfig.mockImplementation((_deploymentId: string, input: Record<string, unknown>) => Promise.resolve({
    deployment_id: 'deploy-1', deployment_status: 'ACTIVE', runtime_config_version: 2,
    revision: { ...input, config_version: 2 },
  }))
  api.listExecutionProfiles.mockResolvedValue({ items: [{ id: 'profile-1', tenant_id: 'tenant-a', version: 1, engine_name: 'deepseek_harness', engine_available: true, model_alias: null, status: 'PUBLISHED' }] })
  api.listExecutionProfileVersions.mockResolvedValue({ items: [{ id: 'profile-1', tenant_id: 'tenant-a', version: 1, engine_name: 'deepseek_harness', engine_available: true, model_alias: null, status: 'PUBLISHED' }] })
  api.listSelectableModelRoutes.mockResolvedValue({ items: [{ route_id: 'route-1', tenant_id: 'tenant-a', alias: '数仓模型', status: 'ACTIVE', route_version: 1, profile: { model_name: 'deepseek-chat', protocol: 'openai-chat-completions' } }] })
  api.listEngineProviders.mockResolvedValue({ items: [{ engine_name: 'deepseek_harness', display_name: 'DeepSeek Harness', supported_model_protocols: ['openai-chat-completions'], capabilities: { reasoning: true, reasoning_effort: true, reasoning_budget: true, max_output_tokens: true }, execution_mode: 'SIMULATION' }] })
  api.getEffectiveEngineProfileDefault.mockResolvedValue({ binding: null, profile: null, source: null })
  api.getEffectiveModelRouteDefault.mockResolvedValue({ binding: null, route: null, source: null })
  api.getEffectiveRuntimeStrategyDefault.mockResolvedValue({ binding: null, runtime_options: { reasoning_mode: 'AUTO', reasoning_effort: 'AUTO', response_verbosity: 'STANDARD' }, source: 'SYSTEM_DEFAULT' })
  api.listTeams.mockResolvedValue({ items: [] })
  api.getTeam.mockResolvedValue({ items: [] })
  api.listTeamWorkerPools.mockResolvedValue({ items: [] })
}

test('loads tenant scoped Conversation, Run, Trace and exact Approval preview', async () => {
  baseApiState()
  const user = userEvent.setup()
  api.approveToolApproval.mockResolvedValue({ approval_id: 'approval-1', state: 'APPROVED' })

  renderPage()

  expect(await screen.findByText('ODS 到 DWD 工作流')).toBeInTheDocument()
  expect(await screen.findByText('请检查工作流预览。')).toBeInTheDocument()
  expect(await screen.findByText('引擎 · deepseek_harness · Profile v2')).toBeInTheDocument()
  expect(screen.getByText('DEPLOYMENT_OVERRIDE · Deployment v4')).toBeInTheDocument()
  expect(screen.getByText(/模型 · 数仓模型/)).toBeInTheDocument()
  expect(screen.getByText('TENANT_DEFAULT')).toBeInTheDocument()
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

test('keeps the first assistant answer visible after a second turn completes', async () => {
  baseApiState()
  let secondTurn = false
  api.listConversationMessages.mockImplementation(() => Promise.resolve({
    items: secondTurn
      ? [
          { id: 'message-1', sender_id: 'admin-local', source: 'user', text: '列出当前项目' },
          { id: 'message-2', sender_id: 'admin-local', source: 'user', text: '继续生成工作流预览' },
        ]
      : [{ id: 'message-1', sender_id: 'admin-local', source: 'user', text: '列出当前项目' }],
  }))
  api.getRun.mockImplementation((runId: string) => Promise.resolve({
    id: runId,
    session_id: 'session-1',
    status: secondTurn ? 'SUCCEEDED' : 'SUCCEEDED',
    result: runId === 'run-1' ? '第一轮回答：已找到 AgentDemo Acceptance 项目。' : '第二轮回答：已生成离线预览。',
    snapshot: {
      employee_id: 'employee-1', employee_version: 3, skill_ids: [], tool_ids: [],
      execution_profile_id: 'profile-1', execution_profile_version: 1,
      engine_name: 'deepseek_harness', model_route_id: 'route-1', runtime_config_version: 1,
      runtime_profile_source: 'DEPLOYMENT_DEFAULT', runtime_model_route_source: 'TENANT_DEFAULT',
      model_route_snapshot: null,
    },
  }))
  api.getRunTrace.mockImplementation((runId: string) => Promise.resolve({
    run: { id: runId, status: 'SUCCEEDED' }, spans: [],
    usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, source: 'test', estimated: true },
  }))
  api.submitConversationTurn.mockImplementation(async () => {
    secondTurn = true
    api.listConversations.mockResolvedValue({
      items: [{
        conversation_id: 'conversation-1',
        title: 'ODS 到 DWD 工作流',
        employee: { employee_id: 'employee-1', employee_name: '海豚调度数仓开发' },
        session_id: 'session-1',
        last_message: { message_id: 'message-2', preview: '继续生成工作流预览', source: 'USER' },
        last_run: { run_id: 'run-2', status: 'SUCCEEDED' },
        pending_approval_count: 0,
        updated_at: '2026-11-19T08:31:00Z',
      }],
      next_cursor: null,
    })
    return { conversation_id: 'conversation-1', message_id: 'message-2', run_id: 'run-2', status: 'QUEUED' }
  })

  renderPage()

  expect(await screen.findByText('第一轮回答：已找到 AgentDemo Acceptance 项目。')).toBeInTheDocument()
  const input = screen.getByLabelText('提交任务')
  fireEvent.change(input, { target: { value: '继续生成工作流预览' } })
  await userEvent.click(screen.getByRole('button', { name: '提交任务' }))

  expect(await screen.findByText('第二轮回答：已生成离线预览。')).toBeInTheDocument()
  expect(screen.getByText('第一轮回答：已找到 AgentDemo Acceptance 项目。')).toBeInTheDocument()
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

test('exposes pending Deployment configuration and saves a model-only override', async () => {
  const user = userEvent.setup()
  baseApiState()
  api.listDeployments.mockResolvedValue({
    items: [{ id: 'deploy-pending', employee_id: 'employee-1', version: 3, status: 'PENDING_CONFIGURATION', runtime_config_version: null }],
  })
  api.listConversations.mockResolvedValue({ items: [], next_cursor: null })
  api.getDeploymentRuntimeConfig.mockResolvedValue({
    deployment_id: 'deploy-pending', deployment_status: 'PENDING_CONFIGURATION', runtime_config_version: null, revision: null,
  })
  api.saveDeploymentRuntimeConfig.mockResolvedValue({
    deployment_id: 'deploy-pending', deployment_status: 'ACTIVE', runtime_config_version: 1, revision: { mode: 'OVERRIDE', model_route_id: 'route-1' },
  })
  renderPage()

  expect(await screen.findByText('运行环境待设置')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '运行配置' }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '设置员工覆盖' }))
  await user.selectOptions(screen.getByRole('combobox', { name: '模型（ModelRoute）' }), 'route-1')
  expect(screen.getByRole('button', { name: '设置员工覆盖' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getAllByText('数仓模型 · deepseek-chat').length).toBeGreaterThan(0)
  expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '保存配置' }))

  await waitFor(() => expect(api.saveDeploymentRuntimeConfig).toHaveBeenCalledWith(
    'deploy-pending',
    {
      expected_config_version: null,
      mode: 'OVERRIDE',
      execution_profile_id: null,
      execution_profile_version: null,
      model_route_id: 'route-1',
    },
    'admin-local',
  ))
})

test('saves the strategy revision and starts a new conversation from the panel', async () => {
  const user = userEvent.setup()
  baseApiState()
  api.listConversations.mockResolvedValue({ items: [], next_cursor: null })
  api.getDeploymentRuntimeConfig.mockResolvedValue({
    deployment_id: 'deploy-1', deployment_status: 'ACTIVE', runtime_config_version: 1,
    revision: { config_version: 1, mode: 'INHERIT', runtime_options: {} },
  })
  api.saveDeploymentRuntimeConfig.mockResolvedValue({
    deployment_id: 'deploy-1', deployment_status: 'ACTIVE', runtime_config_version: 2,
    revision: { mode: 'OVERRIDE', runtime_options: { reasoning_mode: 'DEEP' } },
  })
  api.createSession.mockResolvedValue({ id: 'session-new', deployment_id: 'deploy-1' })
  api.createConversation.mockResolvedValue({ id: 'conversation-new', title: '新对话', is_group: false, status: 'ACTIVE' })
  api.bindConversationAgent.mockResolvedValue({ employee_id: 'employee-1', session_id: 'session-new', status: 'ACTIVE' })

  renderPage()
  await user.click(await screen.findByRole('button', { name: '运行配置' }))
  await user.click(await screen.findByRole('button', { name: '设置员工覆盖' }))
  await user.selectOptions(screen.getByRole('combobox', { name: '思考模式' }), 'DEEP')
  await user.click(screen.getByRole('button', { name: '保存并新建对话' }))

  await waitFor(() => expect(api.saveDeploymentRuntimeConfig).toHaveBeenCalled())
  await waitFor(() => expect(api.bindConversationAgent).toHaveBeenCalledWith(
    'conversation-new',
    { employee_id: 'employee-1', employee_name: '海豚调度数仓开发', session_id: 'session-new' },
    'admin-local',
  ))
  expect(api.createSession).toHaveBeenCalledWith('deploy-1', 'admin-local')
})

test('clears old field overrides when switching a Deployment revision to inherit', async () => {
  const user = userEvent.setup()
  baseApiState()
  api.getDeploymentRuntimeConfig.mockResolvedValue({
    deployment_id: 'deploy-1', deployment_status: 'ACTIVE', runtime_config_version: 2, revision: {
      config_version: 2, mode: 'OVERRIDE', execution_profile_id: 'profile-1', execution_profile_version: 1,
      model_route_id: 'route-1', workspace_profile_id: null, workspace_profile_version: null,
      sandbox_profile_id: null, sandbox_profile_version: null, approval_policy: {}, runtime_options: {}, created_by: 'admin-local',
    },
  })
  renderPage()

  await screen.findByText('ODS 到 DWD 工作流')
  await user.click(screen.getByRole('button', { name: '运行配置' }))
  await user.click(await screen.findByRole('button', { name: '全部继承默认' }))
  expect(screen.getByText(/引擎来源：/)).toBeInTheDocument()
  expect(screen.getByText(/模型来源：/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '保存配置' }))

  await waitFor(() => expect(api.saveDeploymentRuntimeConfig).toHaveBeenCalledWith(
    'deploy-1',
    {
      expected_config_version: 2,
      mode: 'INHERIT',
      execution_profile_id: null,
      execution_profile_version: null,
      model_route_id: null,
    },
    'admin-local',
  ))
})

test('warns before saving away a legacy approval policy migration field', async () => {
  const user = userEvent.setup()
  baseApiState()
  api.getDeploymentRuntimeConfig.mockResolvedValue({
    deployment_id: 'deploy-1', deployment_status: 'ACTIVE', runtime_config_version: 2, revision: {
      config_version: 2, mode: 'OVERRIDE', execution_profile_id: 'profile-1', execution_profile_version: 1,
      model_route_id: 'route-1', workspace_profile_id: null, workspace_profile_version: null,
      sandbox_profile_id: null, sandbox_profile_version: null,
      approval_policy: { name: 'required-for-write' }, runtime_options: {}, created_by: 'admin-local',
    },
  })
  renderPage()

  await screen.findByText('ODS 到 DWD 工作流')
  await user.click(screen.getByRole('button', { name: '运行配置' }))
  expect(await screen.findByText('当前修订包含未接入 Worker 的环境扩展字段')).toBeInTheDocument()
  expect(screen.getByText(/保存本表单会在下一修订中清除这些字段/)).toBeInTheDocument()
})

test('tells an existing conversation that it keeps its pinned runtime snapshot', async () => {
  const user = userEvent.setup()
  baseApiState()
  renderPage()

  await screen.findByText('ODS 到 DWD 工作流')
  await user.click(screen.getByRole('button', { name: '运行配置' }))
  await user.click(await screen.findByRole('button', { name: '保存配置' }))

  expect(await screen.findByText('运行配置已更新；当前对话仍使用旧 Session 快照。点击“新对话”后使用新配置。')).toBeInTheDocument()
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
      <MemoryRouter><ConversationWorkbenchPage /></MemoryRouter>
    </QueryClientProvider>,
  )

  expect(await screen.findByText('租户 B 工作')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText('租户 A 工作')).not.toBeInTheDocument())
  expect(api.listConversations).toHaveBeenCalledWith('employee-1', expect.any(Object), 'admin-other')
})

test('switches between single employee and team workbench modes', async () => {
  baseApiState()
  api.listTeams.mockResolvedValue({ items: [] })
  renderPage()

  expect(await screen.findByRole('tab', { name: '数字员工对话' })).toHaveAttribute('aria-selected', 'true')
  await userEvent.click(screen.getByRole('tab', { name: '团队协作' }))
  expect(await screen.findByText('Agent 团队群聊')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '数字员工对话' })).toBeInTheDocument()
})
