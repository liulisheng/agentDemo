import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'

import { TeamWorkbench } from './TeamWorkbench'
import type { TeamMemberSummary, TeamVersionSummary } from '../../api/client'

const { api } = vi.hoisted(() => ({
  api: {
    listTeams: vi.fn(), getTeam: vi.fn(), listTeamWorkerPools: vi.fn(), createTeam: vi.fn(),
    addTeamMember: vi.fn(), removeTeamMember: vi.fn(), updateTeamPolicy: vi.fn(), updateTeamRoles: vi.fn(), forkTeamRevision: vi.fn(), publishTeam: vi.fn(),
    createTeamConversation: vi.fn(), listTeamConversations: vi.fn(), createTeamOrchestration: vi.fn(), submitTeamTurn: vi.fn(), getTeamActivity: vi.fn(),
    getConversationMessages: vi.fn(), listOrchestrationTasks: vi.fn(), listOrchestrationEvents: vi.fn(), listOrchestrationArtifacts: vi.fn(),
    previewArtifact: vi.fn(), downloadArtifact: vi.fn(), bundleTeamArtifacts: vi.fn(), approveTeamPlan: vi.fn(), replanTeamOrchestration: vi.fn(), pauseTeamOrchestration: vi.fn(),
    resumeTeamOrchestration: vi.fn(), cancelTeamOrchestration: vi.fn(), deliverTeamArtifacts: vi.fn(),
    listDigitalEmployees: vi.fn(), listDeployments: vi.fn(), getDeploymentRuntimeConfig: vi.fn(),
    listExecutionProfiles: vi.fn(), listExecutionProfileVersions: vi.fn(), getEffectiveEngineProfileDefault: vi.fn(), getEffectiveModelRouteDefault: vi.fn(),
    listEngineProviders: vi.fn(), listModelRoutes: vi.fn(),
    assignTeamTask: vi.fn(), retryTeamTask: vi.fn(), cancelTeamTask: vi.fn(),
  },
}))

vi.mock('../../api/client', () => api)

const draftTeam = {
  team_id: 'team-1', version: 1, name: '分析团队', status: 'DRAFT',
  orchestration_mode: 'HYBRID', approval_mode: 'CONFIRM', router_member_id: null, finalizer_member_id: null, members: [],
}

function renderWorkbench() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><TeamWorkbench fakeUser="admin-local" onSwitchToSingle={() => undefined} /></QueryClientProvider>)
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [draftTeam] })
  api.getTeam.mockResolvedValue(draftTeam)
  api.listTeamWorkerPools.mockResolvedValue({ team_id: 'team-1', items: [{ engine_name: 'fake', healthy: true, queue_depth: 0, active_workers: 0, max_concurrency: 4 }] })
  api.listTeamConversations.mockResolvedValue({ items: [{ conversation_id: 'conversation-1', title: '真实团队对话', conversation_mode: 'TEAM', team_id: 'team-1', team_version: 1, status: 'ACTIVE', updated_at: '2026-11-25T02:00:00Z' }] })
  api.getTeamActivity.mockResolvedValue({ conversation_id: 'conversation-1', items: [{
    orchestration_id: 'orchestration-1', status: 'RUNNING', plan_revision: 1,
    tasks: [{ task_id: 'task-1', client_key: 'analyze', title: '来自 API 的真实任务', status: 'RUNNING', assigned_member_id: 'router-member', assignment_source: 'ROUTER', attempt: 1, engine_name: 'fake', worker_pool: 'worker-test', required_capabilities: ['reasoning'], depends_on: [], latest_attempt: { attempt_no: 1, status: 'RUNNING', worker_id: 'worker-test', fencing_token: 1, run_id: 'run-1', error_code: null } }],
    recent_events: [{ event_type: 'task_claimed', sequence: 1, created_at: '2026-11-25T02:00:00Z' }],
  }] })
  api.getConversationMessages.mockResolvedValue({ items: [{ id: 'message-1', conversation_id: 'conversation-1', sender_id: 'admin-local', source: 'user', text: '来自真实消息记录的任务需求', routing_depth: 0, created_at: '2026-11-25T02:00:00Z' }] })
  api.listOrchestrationTasks.mockResolvedValue({ items: [{ task_id: 'task-1', client_key: 'analyze', title: '来自 API 的真实任务', status: 'RUNNING', assigned_member_id: 'router-member', assignment_source: 'ROUTER', attempt: 1, error_code: null, required_capabilities: ['reasoning'], engine_name: 'fake', worker_pool: 'worker-test', output_ref: null, started_at: '2026-11-25T02:00:00Z', finished_at: null, depends_on: [], latest_attempt: { attempt_no: 1, status: 'RUNNING', worker_id: 'worker-test', fencing_token: 1, run_id: 'run-1', error_code: null } }], next_cursor: null })
  api.listOrchestrationEvents.mockResolvedValue({ items: [{ event_id: 'event-1', orchestration_id: 'orchestration-1', task_id: 'task-1', attempt_id: 'attempt-1', event_type: 'task_claimed', actor_type: 'WORKER', sequence: 1, created_at: '2026-11-25T02:00:00Z', payload: { worker_id: 'worker-test' } }] })
  api.listOrchestrationArtifacts.mockResolvedValue({ items: [] })
  api.previewArtifact.mockResolvedValue({ artifact_id: 'artifact-1', filename: 'analysis.md', content: '分析结果正文' })
  api.bundleTeamArtifacts.mockResolvedValue({ artifact_id: 'bundle-1', conversation_id: 'conversation-1', orchestration_id: 'orchestration-1', task_id: null, run_id: null, attempt_no: null, filename: 'delivery.zip', mime_type: 'application/zip', size_bytes: 120, content_sha256: 'f'.repeat(64), source_engine: null, visibility: 'USER', status: 'READY', created_at: '2026-11-25T02:00:00Z' })
  api.deliverTeamArtifacts.mockResolvedValue({ items: [{ delivery_id: 'delivery-1', artifact_id: 'bundle-1', status: 'DELIVERED' }] })
  api.createTeam.mockResolvedValue({ team_id: 'team-2', version: 1, name: '新团队', status: 'DRAFT' })
  api.addTeamMember.mockResolvedValue(draftTeam)
  api.updateTeamPolicy.mockResolvedValue(draftTeam)
  api.updateTeamRoles.mockResolvedValue(draftTeam)
  api.publishTeam.mockResolvedValue({ ...draftTeam, status: 'PUBLISHED' })
  api.assignTeamTask.mockResolvedValue({ task_id: 'task-1', status: 'ASSIGNED', assigned_member_id: 'router-member', assignment_source: 'USER' })
  api.createTeamConversation.mockResolvedValue({ id: 'conversation-new', title: '新对话', conversation_mode: 'TEAM', team_id: 'team-1', team_version: 1 })
  api.submitTeamTurn.mockResolvedValue({ orchestration_id: 'orchestration-1', conversation_id: 'conversation-1', status: 'AWAITING_PLAN_APPROVAL', plan_revision: 1, router_run_id: 'router-run-1', summary: '计划已生成', task_count: 1, created: true })
  api.replanTeamOrchestration.mockResolvedValue({ orchestration_id: 'orchestration-2', conversation_id: 'conversation-1', status: 'PLANNING', plan_revision: 1, router_run_id: 'router-run-2', summary: '新的 Router 计划已提交。', task_count: 0, created: true })
  api.listDigitalEmployees.mockResolvedValue({ items: [
    { id: 'employee-1', tenant_id: 'tenant-a', name: '订单分析员工', role_name: '分析', description: '', status: 'ACTIVE', published_to_gallery: false, resource_counts: { knowledge_base: 0, skill: 0, sop: 0, tool: 0 } },
    { id: 'employee-2', tenant_id: 'tenant-a', name: '数据汇总员工', role_name: '汇总', description: '', status: 'ACTIVE', published_to_gallery: false, resource_counts: { knowledge_base: 0, skill: 0, sop: 0, tool: 0 } },
  ] })
  api.listDeployments.mockResolvedValue({ items: [
    { id: 'deployment-1', tenant_id: 'tenant-a', employee_id: 'employee-1', version: 1, status: 'ACTIVE', runtime_config_version: 1, version_summary: { version: 1, status: 'PUBLISHED', skill_count: 0, tool_count: 0 } },
    { id: 'deployment-2', tenant_id: 'tenant-a', employee_id: 'employee-2', version: 1, status: 'ACTIVE', runtime_config_version: 1, version_summary: { version: 1, status: 'PUBLISHED', skill_count: 0, tool_count: 0 } },
  ] })
  api.getDeploymentRuntimeConfig.mockResolvedValue({ deployment_id: 'deployment-1', deployment_status: 'ACTIVE', runtime_config_version: 1, revision: { config_version: 1, mode: 'OVERRIDE', execution_profile_id: 'profile-1', execution_profile_version: 1, model_route_id: 'route-1', workspace_profile_id: null, workspace_profile_version: null, sandbox_profile_id: null, sandbox_profile_version: null, approval_policy: {}, runtime_options: {}, created_by: 'admin-local' } })
  api.listExecutionProfiles.mockResolvedValue({ items: [{ id: 'profile-1', tenant_id: 'tenant-a', version: 1, engine_name: 'fake', engine_available: true, model_alias: 'fake-model', status: 'PUBLISHED' }] })
  api.listExecutionProfileVersions.mockResolvedValue({ items: [{ id: 'profile-1', tenant_id: 'tenant-a', version: 1, engine_name: 'fake', engine_available: true, model_alias: 'fake-model', status: 'PUBLISHED' }] })
  api.getEffectiveEngineProfileDefault.mockResolvedValue({ binding: { binding_id: 'binding-1', scope_type: 'tenant', scope_id: 'tenant-a', profile_id: 'profile-1', profile_version: 1, status: 'ACTIVE' }, profile: { profile_id: 'profile-1', version: 1, engine_name: 'fake', tool_policy: [], context_policy: 'default', workspace_profile_id: null, workspace_profile_version: null, sandbox_profile_id: null, sandbox_profile_version: null, approval_policy: 'none', status: 'PUBLISHED' }, source: 'tenant' })
  api.getEffectiveModelRouteDefault.mockResolvedValue({ binding: null, route: null, source: null })
  api.listEngineProviders.mockResolvedValue({ items: [{ engine_name: 'fake', display_name: 'Fake Runtime', supported_model_protocols: [], capabilities: { reasoning: true }, execution_mode: 'SIMULATION' }] })
  api.listModelRoutes.mockResolvedValue({ items: [{ route_id: 'route-1', alias: '测试模型', status: 'ACTIVE', profile: { model_name: 'fake-model', provider: 'fake', endpoint: 'local', secret_ref: null } }], next_cursor: null })
})

test('workbench renders messages and task activity from APIs, not prototype fixtures', async () => {
  renderWorkbench()

  expect((await screen.findAllByText('来自 API 的真实任务')).length).toBeGreaterThan(0)
  expect(await screen.findByText('来自真实消息记录的任务需求')).toBeInTheDocument()
  expect(screen.queryByText('季度销售下降分析')).not.toBeInTheDocument()
  expect(screen.getAllByText('1').length).toBeGreaterThan(0)
})

test('admin can configure a team member and publish the draft', async () => {
  const user = userEvent.setup()
  let mutableTeam: TeamVersionSummary = draftTeam
  api.listTeams.mockImplementation(async () => ({ permissions: { can_manage: true, can_operate: true }, items: [mutableTeam] }))
  api.getTeam.mockImplementation(async () => mutableTeam)
  api.addTeamMember.mockImplementation(async (_teamId: string, _version: number, input: { employee_id: string; employee_version: number; deployment_id: string; role: 'MEMBER' | 'ROUTER' | 'FINALIZER'; engine_name: string; capabilities: string[] }) => {
    const member: TeamMemberSummary = { member_id: `${input.role.toLowerCase()}-member`, ...input, model_route_id: 'route-1', max_concurrency: 1, status: 'ACTIVE' }
    mutableTeam = { ...mutableTeam, members: [...mutableTeam.members, member], router_member_id: input.role === 'ROUTER' ? member.member_id : mutableTeam.router_member_id, finalizer_member_id: input.role === 'FINALIZER' ? member.member_id : mutableTeam.finalizer_member_id }
    return mutableTeam
  })
  renderWorkbench()

  await user.click(await screen.findByRole('button', { name: /团队配置/ }))
  const dialog = await screen.findByRole('dialog', { name: /团队配置/ })
  expect(within(dialog).getByText('团队成员和能力')).toBeInTheDocument()
  await user.selectOptions(within(dialog).getByLabelText('员工部署'), 'deployment-1')
  await user.selectOptions(within(dialog).getByLabelText('团队角色'), 'ROUTER')
  await user.click(within(dialog).getByRole('button', { name: '添加成员' }))
  expect(api.addTeamMember).toHaveBeenCalledWith('team-1', 1, expect.objectContaining({ employee_id: 'employee-1', deployment_id: 'deployment-1', role: 'ROUTER', engine_name: 'fake' }), 'admin-local')
  await user.selectOptions(within(dialog).getByLabelText('团队角色'), 'FINALIZER')
  await user.click(within(dialog).getByRole('button', { name: '添加成员' }))
  await user.click(within(dialog).getByRole('button', { name: '关闭' }))
  await user.click(await screen.findByRole('button', { name: '发布团队版本' }))
  expect(api.publishTeam).toHaveBeenCalledWith('team-1', 1, 'admin-local')
})

test('new team action saves a draft and opens its actual configuration', async () => {
  const user = userEvent.setup()
  const createdTeam = { ...draftTeam, team_id: 'team-2', name: '新团队' }
  let teams = [draftTeam]
  api.listTeams.mockImplementation(async () => ({ permissions: { can_manage: true, can_operate: true }, items: teams }))
  api.createTeam.mockImplementation(async () => {
    teams = [...teams, createdTeam]
    return { team_id: createdTeam.team_id, version: 1, name: createdTeam.name, status: 'DRAFT' }
  })
  api.getTeam.mockImplementation(async (teamId: string) => teamId === 'team-2' ? createdTeam : draftTeam)
  renderWorkbench()

  await user.click(await screen.findByRole('button', { name: '新建团队' }))
  expect((await screen.findAllByText('团队成员和能力')).length).toBeGreaterThanOrEqual(1)
  await user.type(screen.getByLabelText('团队名称'), '新团队')
  await user.click(await screen.findByLabelText('选择订单分析员工'))
  await user.click(screen.getByLabelText('选择数据汇总员工'))
  await user.selectOptions(screen.getByLabelText('新团队路由 Agent'), 'deployment-1')
  await user.selectOptions(screen.getByLabelText('新团队汇总 Agent'), 'deployment-2')
  await user.click(screen.getByRole('button', { name: '保存团队草稿' }))

  expect(api.createTeam).toHaveBeenCalledWith('新团队', 'admin-local')
  expect(await screen.findByRole('dialog', { name: '团队配置' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: /新团队/ })).toBeInTheDocument()
})

test('new team uses the exact published Execution Profile version pinned by each Deployment', async () => {
  const user = userEvent.setup()
  const createdTeam = { ...draftTeam, team_id: 'team-2', name: '历史配置团队' }
  let teams = [draftTeam]
  api.listTeams.mockImplementation(async () => ({ permissions: { can_manage: true, can_operate: true }, items: teams }))
  api.createTeam.mockImplementation(async () => {
    teams = [...teams, createdTeam]
    return { team_id: createdTeam.team_id, version: 1, name: createdTeam.name, status: 'DRAFT' }
  })
  api.getTeam.mockImplementation(async (teamId: string) => teamId === 'team-2' ? createdTeam : draftTeam)
  api.listExecutionProfiles.mockResolvedValue({ items: [{ id: 'profile-1', tenant_id: 'tenant-a', version: 2, engine_name: 'fake', model_alias: 'latest draft', status: 'DRAFT' }] })
  api.listExecutionProfileVersions.mockResolvedValue({ items: [{ id: 'profile-1', tenant_id: 'tenant-a', version: 1, engine_name: 'fake', model_alias: 'published pinned version', status: 'PUBLISHED' }] })

  renderWorkbench()
  await user.click(await screen.findByRole('button', { name: '新建团队' }))
  await user.type(screen.getByLabelText('团队名称'), '历史配置团队')
  await user.click(await screen.findByLabelText('选择订单分析员工'))
  await user.click(screen.getByLabelText('选择数据汇总员工'))
  await user.selectOptions(screen.getByLabelText('新团队路由 Agent'), 'deployment-1')
  await user.selectOptions(screen.getByLabelText('新团队汇总 Agent'), 'deployment-2')

  expect(await screen.findAllByText(/fake · 测试模型 · reasoning/)).toHaveLength(2)
  await user.click(screen.getByRole('button', { name: '保存团队草稿' }))

  expect(api.listExecutionProfileVersions).toHaveBeenCalledWith('profile-1', 'admin-local')
  expect(api.addTeamMember).toHaveBeenCalledTimes(2)
  expect(api.addTeamMember).toHaveBeenNthCalledWith(1, 'team-2', 1, expect.objectContaining({ engine_name: 'fake' }), 'admin-local')
})

test('shows Router planning as queued and blocks another team turn until the Worker returns a plan', async () => {
  const user = userEvent.setup()
  const publishedTeam = { ...draftTeam, status: 'PUBLISHED', members: [{ member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER' as const, engine_name: 'fake', model_route_id: 'route-1', capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] }
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [publishedTeam] })
  api.getTeam.mockResolvedValue(publishedTeam)
  let workerPlanQueued = false
  api.getTeamActivity.mockImplementation(async () => ({ conversation_id: 'conversation-1', items: workerPlanQueued
    ? [{ orchestration_id: 'orchestration-planning', status: 'PLANNING', plan_revision: 1, tasks: [], recent_events: [] }]
    : [{ orchestration_id: 'orchestration-ready', status: 'READY', plan_revision: 1, tasks: [], recent_events: [] }] }))
  api.submitTeamTurn.mockImplementation(async () => {
    workerPlanQueued = true
    return { orchestration_id: 'orchestration-planning', conversation_id: 'conversation-1', status: 'PLANNING', plan_revision: 1, router_run_id: 'router-run-1', summary: 'Router 计划已提交，等待 Runtime Worker 执行。', task_count: 0, created: true }
  })
  renderWorkbench()

  expect(await screen.findByRole('option', { name: /分析团队/ })).toBeInTheDocument()
  expect(await screen.findByRole('button', { name: /真实团队对话/ })).toBeInTheDocument()
  const composer = await screen.findByLabelText('提交新的团队任务')
  await waitFor(() => expect(composer).toBeEnabled())
  await user.type(composer, '拆分一个测试任务')
  expect(composer).toHaveValue('拆分一个测试任务')
  expect(screen.getByRole('button', { name: '提交团队任务' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '提交团队任务' }))

  expect(api.submitTeamTurn).toHaveBeenCalledWith('conversation-1', expect.objectContaining({ text: '拆分一个测试任务', idempotency_key: expect.any(String) }), 'admin-local')
  expect(await screen.findByText(/Router 计划已排队/)).toBeInTheDocument()
  expect(screen.getByLabelText('提交新的团队任务')).toBeDisabled()
})

test('member activity opens task and recent event dialogs using the selected task', async () => {
  const user = userEvent.setup()
  const memberTeam = { ...draftTeam, status: 'PUBLISHED', members: [{ member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER' as const, engine_name: 'fake', model_route_id: 'route-1', capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] }
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [memberTeam] })
  api.getTeam.mockResolvedValue(memberTeam)
  renderWorkbench()

  await user.click(await screen.findByRole('button', { name: /analyze · 来自 API 的真实任务/ }))
  const taskDialog = await screen.findByRole('dialog', { name: /任务详情/ })
  expect(within(taskDialog).getByRole('heading', { name: /来自 API 的真实任务/ })).toBeInTheDocument()
  await user.click(within(taskDialog).getByRole('button', { name: '关闭' }))
  await user.click((await screen.findAllByRole('button', { name: '查看最近事件' }))[0])
  const eventDialog = await screen.findByRole('dialog', { name: /最近事件/ })
  expect(within(eventDialog).getByText('task_claimed')).toBeInTheDocument()
  expect(eventDialog).toHaveTextContent('worker-test')
})

test('shows an assigned task in member activity while its Run waits in the Worker queue', async () => {
  const user = userEvent.setup()
  const memberTeam = { ...draftTeam, status: 'PUBLISHED', members: [{ member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER' as const, engine_name: 'fake', model_route_id: 'route-1', capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] }
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [memberTeam] })
  api.getTeam.mockResolvedValue(memberTeam)
  api.getTeamActivity.mockResolvedValue({ conversation_id: 'conversation-1', items: [{
    orchestration_id: 'orchestration-1', status: 'RUNNING', plan_revision: 1,
    tasks: [{ task_id: 'task-1', client_key: 'analyze', title: '排队中的 Worker 任务', status: 'ASSIGNED', assigned_member_id: 'router-member', assignment_source: 'ROUTER', attempt: 1, engine_name: 'fake', worker_pool: 'fake-pool', required_capabilities: ['reasoning'], depends_on: [], latest_attempt: { attempt_no: 1, status: 'QUEUED', worker_id: null, fencing_token: 0, run_id: 'run-1', error_code: null } }],
    recent_events: [],
  }] })
  api.listOrchestrationTasks.mockResolvedValue({ items: [{ task_id: 'task-1', client_key: 'analyze', title: '排队中的 Worker 任务', status: 'ASSIGNED', assigned_member_id: 'router-member', assignment_source: 'ROUTER', attempt: 1, error_code: null, required_capabilities: ['reasoning'], engine_name: 'fake', worker_pool: 'fake-pool', output_ref: null, started_at: null, finished_at: null, depends_on: [], latest_attempt: { attempt_no: 1, status: 'QUEUED', worker_id: null, fencing_token: 0, run_id: 'run-1', error_code: null } }], next_cursor: null })
  renderWorkbench()

  expect(await screen.findByText('已分配 · 排队中的 Worker 任务')).toBeInTheDocument()
  await user.click((await screen.findAllByRole('button', { name: '查看任务' }))[0])
  expect(await screen.findByRole('dialog', { name: /任务详情/ })).toHaveTextContent('排队中的 Worker 任务')
})

test('replan action submits a new Router request without replacing the current plan', async () => {
  const user = userEvent.setup()
  const memberTeam = { ...draftTeam, status: 'PUBLISHED', members: [
    { member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER' as const, engine_name: 'fake', model_route_id: 'route-1', capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' },
    { member_id: 'member-2', employee_id: 'employee-2', employee_version: 1, deployment_id: 'deployment-2', role: 'MEMBER' as const, engine_name: 'fake', model_route_id: 'route-1', capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' },
  ] }
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [memberTeam] })
  api.getTeam.mockResolvedValue(memberTeam)
  api.getConversationMessages.mockResolvedValue({ items: [
    { id: 'message-user', conversation_id: 'conversation-1', sender_id: 'admin-local', source: 'user', text: '测试重新规划', routing_depth: 0, created_at: '2026-11-25T02:00:00Z' },
    { id: 'message-agent', conversation_id: 'conversation-1', sender_id: 'employee-1', source: 'agent', team_member_id: 'router-member', text: '已生成原计划', routing_depth: 0, created_at: '2026-11-25T02:00:01Z' },
    { id: 'message-member', conversation_id: 'conversation-1', sender_id: 'employee-2', source: 'agent', team_member_id: 'member-2', text: '成员任务进度', routing_depth: 0, created_at: '2026-11-25T02:00:02Z' },
  ] })
  renderWorkbench()

  expect(await screen.findAllByRole('button', { name: '重新规划' })).toHaveLength(1)
  await user.click(await screen.findByRole('button', { name: '重新规划' }))

  expect(api.replanTeamOrchestration).toHaveBeenCalledWith('orchestration-1', 'admin-local')
  expect(await screen.findByText(/新的计划草稿已提交/)).toBeInTheDocument()
  expect((await screen.findAllByText('来自 API 的真实任务')).length).toBeGreaterThan(0)
})

test('operator can assign a ready task to a team member from task details', async () => {
  const user = userEvent.setup()
  const memberTeam = { ...draftTeam, status: 'PUBLISHED', members: [{ member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER' as const, engine_name: 'fake', model_route_id: 'route-1', capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] }
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [memberTeam] })
  api.getTeam.mockResolvedValue(memberTeam)
  api.listOrchestrationTasks.mockResolvedValue({ items: [{ task_id: 'task-1', client_key: 'analyze', title: '待分配任务', status: 'READY', assigned_member_id: null, assignment_source: null, attempt: 0, error_code: null, required_capabilities: ['reasoning'], engine_name: null, worker_pool: null, output_ref: null, depends_on: [], latest_attempt: null }], next_cursor: null })
  renderWorkbench()

  await user.click(await screen.findByRole('button', { name: /analyze · 待分配任务/ }))
  const dialog = await screen.findByRole('dialog', { name: /任务详情/ })
  await user.selectOptions(within(dialog).getByLabelText('分配成员'), 'router-member')
  await user.click(within(dialog).getByRole('button', { name: '分配成员' }))

  expect(api.assignTeamTask).toHaveBeenCalledWith('task-1', 'router-member', 'admin-local')
})

test('artifact preview can be packaged and delivered to the Web workbench', async () => {
  const user = userEvent.setup()
  api.listOrchestrationArtifacts.mockResolvedValue({ items: [{ artifact_id: 'artifact-1', conversation_id: 'conversation-1', orchestration_id: 'orchestration-1', task_id: 'task-1', run_id: 'run-1', attempt_no: 1, filename: 'analysis.md', mime_type: 'text/markdown', size_bytes: 30, content_sha256: 'a'.repeat(64), source_engine: 'fake', visibility: 'TEAM', status: 'READY', created_at: '2026-11-25T02:00:00Z' }] })
  renderWorkbench()

  await user.click(await screen.findByRole('button', { name: '预览' }))
  expect(await screen.findByText('分析结果正文')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '加入交付包' }))
  await user.click(screen.getByRole('button', { name: '确认交付 1 个文件' }))

  expect(api.bundleTeamArtifacts).toHaveBeenCalledWith('orchestration-1', expect.objectContaining({ artifact_ids: ['artifact-1'], channel_name: 'web' }), 'admin-local')
  expect(api.deliverTeamArtifacts).toHaveBeenCalledWith('orchestration-1', expect.objectContaining({ artifact_ids: ['bundle-1'], channel_name: 'web' }), 'admin-local')
})

test('viewer can inspect team configuration but cannot create, publish or edit members', async () => {
  const user = userEvent.setup()
  api.listTeams.mockResolvedValue({ permissions: { can_manage: false, can_operate: false }, items: [{ ...draftTeam, members: [{ member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER', engine_name: 'fake', model_route_id: null, capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] }] })
  api.getTeam.mockResolvedValue({ ...draftTeam, members: [{ member_id: 'router-member', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER', engine_name: 'fake', model_route_id: null, capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] })
  renderWorkbench()

  expect(screen.queryByRole('button', { name: '新建团队' })).not.toBeInTheDocument()
  await user.click(await screen.findByRole('button', { name: '团队配置 · 只读' }))
  const dialog = await screen.findByRole('dialog', { name: '团队配置' })
  expect(within(dialog).queryByRole('button', { name: '添加成员' })).not.toBeInTheDocument()
  expect(within(dialog).queryByRole('button', { name: '发布团队版本' })).not.toBeInTheDocument()
})

test('conversation activity stays on its bound team version while a newer draft is selected', async () => {
  const oldVersion = { ...draftTeam, version: 1, status: 'PUBLISHED', members: [{ member_id: 'old-router', employee_id: 'employee-1', employee_version: 1, deployment_id: 'deployment-1', role: 'ROUTER' as const, engine_name: 'fake', model_route_id: null, capabilities: ['reasoning'], max_concurrency: 1, status: 'ACTIVE' }] }
  const currentDraft = { ...draftTeam, version: 2, status: 'DRAFT', name: '分析团队 v2' }
  api.listTeams.mockResolvedValue({ permissions: { can_manage: true, can_operate: true }, items: [currentDraft, oldVersion] })
  api.getTeam.mockImplementation(async (_teamId: string, version: number) => version === 1 ? oldVersion : currentDraft)
  api.listTeamConversations.mockResolvedValue({ items: [{ conversation_id: 'conversation-1', title: '旧版固定对话', conversation_mode: 'TEAM', team_id: 'team-1', team_version: 1, status: 'ACTIVE', updated_at: '2026-11-25T02:00:00Z' }] })
  renderWorkbench()

  expect(await screen.findByText('ROUTER · fake')).toBeInTheDocument()
  expect(within(screen.getByRole('main', { name: '团队群聊时间线' })).getByText('TEAM · v1')).toBeInTheDocument()
  expect(screen.getByRole('option', { name: /分析团队 v2/ })).toBeInTheDocument()
})

test('team list empty and error states offer the matching next action', async () => {
  const user = userEvent.setup()
  api.listTeams.mockRejectedValueOnce(new Error('网络不可用'))
  renderWorkbench()

  expect(await screen.findByText('团队配置读取失败。')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '重试' }))
  expect(await screen.findByRole('option', { name: /分析团队/ })).toBeInTheDocument()
  expect(api.listTeams).toHaveBeenCalledTimes(2)
})
