import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'

import {
  bundleTeamArtifacts,
  assignTeamTask,
  addTeamMember,
  approveTeamPlan,
  cancelTeamTask,
  cancelTeamOrchestration,
  createTeam,
  createTeamConversation,
  deliverTeamArtifacts,
  downloadArtifact,
  forkTeamRevision,
  getConversationMessages,
  getDeploymentRuntimeConfig,
  getEffectiveEngineProfileDefault,
  getEffectiveModelRouteDefault,
  getTeam,
  getTeamActivity,
  listDeployments,
  listDigitalEmployees,
  listEngineProviders,
  listExecutionProfileVersions,
  listOrchestrationArtifacts,
  listOrchestrationEvents,
  listOrchestrationTasks,
  listModelRoutes,
  listTeamConversations,
  listTeamWorkerPools,
  listTeams,
  pauseTeamOrchestration,
  previewArtifact,
  publishTeam,
  removeTeamMember,
  replanTeamOrchestration,
  retryTeamTask,
  resumeTeamOrchestration,
  submitTeamTurn,
  updateTeamPolicy,
  updateTeamRoles,
} from '../../api/client'
import type {
  ConversationMessageSummary,
  OrchestrationEventSummary,
  OrchestrationTaskSummary,
  TeamArtifactSummary,
  TeamMemberSummary,
  TeamVersionSummary,
} from '../../api/client'

interface TeamWorkbenchProps {
  fakeUser: string
  onSwitchToSingle: () => void
}

type DialogKind = 'config' | 'plan' | 'task' | 'event' | 'preview' | 'delivery' | null

const terminalTaskStatuses = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED'])

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    DRAFT: '草稿', PUBLISHED: '已发布', PLANNING: '规划中', AWAITING_PLAN_APPROVAL: '等待确认',
    READY: '待执行', PROPOSED: '待执行', ASSIGNED: '已分配', CLAIMED: '已认领', RUNNING: '进行中',
    WAITING_APPROVAL: '等待审批', SUCCEEDED: '已完成', FAILED: '失败', BLOCKED: '阻塞',
    CANCELLED: '已取消', SKIPPED: '已跳过', PAUSED: '已暂停', PARTIAL_FAILED: '部分失败',
    UNKNOWN: '状态未知', CREATED: '已创建', SCANNING: '扫描中', DELIVERED: '已交付',
  }
  return labels[status] ?? status
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '—' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function messageTime(message: ConversationMessageSummary): string {
  return message.created_at ? new Date(message.created_at).toLocaleString('zh-CN') : '—'
}

function eventTitle(event: OrchestrationEventSummary): string {
  const labels: Record<string, string> = {
    plan_created: 'Router 计划已创建', router_plan_generated: 'Router Agent 已生成计划',
    plan_approved: '用户确认任务计划', orchestration_started: '编排开始运行',
    task_assigned: '任务已分配', task_run_started: '成员开始执行任务',
    task_run_finished: '成员执行任务结束', task_state_changed: '任务状态更新',
    task_retried: '任务已重新尝试', orchestration_cancelled: '编排已取消',
    plan_requested: '请求 Router 计划', router_run_submitted: 'Router Run 已入队',
    task_queued_for_worker: '任务已进入 Worker 队列', task_blocked: '任务已阻塞',
  }
  return labels[event.event_type] ?? event.event_type
}

function eventFields(event: OrchestrationEventSummary): Array<{ label: string; value: string }> {
  const payload = event.payload ?? {}
  const labels: Record<string, string> = {
    status: '状态', run_id: 'Run', member_id: '成员', worker_id: 'Worker',
    engine_name: '引擎', worker_pool: 'Worker Pool', error_code: '错误',
    task_count: '任务数', plan_revision: '计划版本', source: '来源',
  }
  return Object.entries(payload)
    .filter(([key, value]) => !['prompt_sha256', 'input_message_id'].includes(key) && value !== null && value !== undefined && value !== '')
    .slice(0, 6)
    .map(([key, value]) => ({
      label: labels[key] ?? key,
      value: typeof value === 'object' ? JSON.stringify(value) : String(value),
    }))
}

export function TeamWorkbench({ fakeUser, onSwitchToSingle }: TeamWorkbenchProps) {
  const queryClient = useQueryClient()
  const [selectedTeamKey, setSelectedTeamKey] = useState<string | null>(null)
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null)
  const [conversationSearch, setConversationSearch] = useState('')
  const [taskInput, setTaskInput] = useState('')
  const [newTeamName, setNewTeamName] = useState('')
  const [newTeamOpen, setNewTeamOpen] = useState(false)
  const [dialog, setDialog] = useState<DialogKind>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [assignmentMemberId, setAssignmentMemberId] = useState('')
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null)
  const [selectedDeploymentId, setSelectedDeploymentId] = useState('')
  const [selectedRole, setSelectedRole] = useState<'MEMBER' | 'ROUTER' | 'FINALIZER'>('MEMBER')
  const [memberConcurrency, setMemberConcurrency] = useState(1)
  const [memberCapabilities, setMemberCapabilities] = useState<string[]>([])
  const [orchestrationMode, setOrchestrationMode] = useState<'USER' | 'ROUTER' | 'HYBRID'>('HYBRID')
  const [approvalMode, setApprovalMode] = useState<'CONFIRM' | 'AUTO' | 'MANUAL'>('CONFIRM')
  const [maxConcurrency, setMaxConcurrency] = useState(3)
  const [deliveryPolicy, setDeliveryPolicy] = useState<'CONFIRM' | 'ATTACHMENTS' | 'MANUAL'>('CONFIRM')
  const [routerMemberId, setRouterMemberId] = useState('')
  const [finalizerMemberId, setFinalizerMemberId] = useState('')
  const [newTeamMode, setNewTeamMode] = useState<'USER' | 'ROUTER' | 'HYBRID'>('HYBRID')
  const [newTeamConcurrency, setNewTeamConcurrency] = useState(3)
  const [newTeamSelectedDeployments, setNewTeamSelectedDeployments] = useState<string[]>([])
  const [newTeamRouterDeployment, setNewTeamRouterDeployment] = useState('')
  const [newTeamFinalizerDeployment, setNewTeamFinalizerDeployment] = useState('')
  const [deliverySelection, setDeliverySelection] = useState<string[]>([])
  const [deliveryPending, setDeliveryPending] = useState(false)

  const teamsQuery = useQuery({ queryKey: ['team-workbench-teams', fakeUser], queryFn: () => listTeams(fakeUser), refetchInterval: 10000 })
  const teams = teamsQuery.data?.items ?? []
  const selectedTeam = useMemo(() => {
    const key = selectedTeamKey ?? (teams[0] ? `${teams[0].team_id}:${teams[0].version}` : null)
    return teams.find((team) => `${team.team_id}:${team.version}` === key) ?? null
  }, [selectedTeamKey, teams])
  const canManage = teamsQuery.data?.permissions?.can_manage ?? false
  const canOperate = teamsQuery.data?.permissions?.can_operate ?? false
  const detailQuery = useQuery({
    queryKey: ['team-workbench-detail', fakeUser, selectedTeam?.team_id, selectedTeam?.version],
    queryFn: () => getTeam(selectedTeam!.team_id, selectedTeam!.version, fakeUser),
    enabled: Boolean(selectedTeam),
  })
  const team = detailQuery.data ?? selectedTeam
  const conversationsQuery = useQuery({
    queryKey: ['team-workbench-team-conversations', fakeUser, selectedTeam?.team_id],
    queryFn: () => listTeamConversations(selectedTeam!.team_id, fakeUser),
    enabled: Boolean(selectedTeam),
    refetchInterval: 5000,
  })
  const conversationItems = conversationsQuery.data?.items ?? []
  const activeConversationId = conversationItems.some((item) => item.conversation_id === selectedConversationId)
    ? selectedConversationId
    : conversationItems[0]?.conversation_id ?? null
  const activeConversation = conversationItems.find((item) => item.conversation_id === activeConversationId) ?? null
  const conversationTeamQuery = useQuery({
    queryKey: ['team-workbench-conversation-team', fakeUser, activeConversation?.team_id, activeConversation?.team_version],
    queryFn: () => getTeam(activeConversation!.team_id, activeConversation!.team_version, fakeUser),
    enabled: Boolean(activeConversation),
  })
  const conversationTeam = conversationTeamQuery.data ?? (activeConversation?.team_id === team?.team_id && activeConversation?.team_version === team?.version ? team : null)
  const contextTeam = conversationTeam ?? team
  const poolTeamId = activeConversation?.team_id ?? selectedTeam?.team_id
  const poolTeamVersion = activeConversation?.team_version ?? selectedTeam?.version
  const poolQuery = useQuery({
    queryKey: ['team-workbench-pools', fakeUser, poolTeamId, poolTeamVersion],
    queryFn: () => listTeamWorkerPools(poolTeamId!, fakeUser, poolTeamVersion),
    enabled: Boolean(poolTeamId && poolTeamVersion),
    refetchInterval: 5000,
  })
  const activityQuery = useQuery({
    queryKey: ['team-workbench-activity', fakeUser, activeConversationId],
    queryFn: () => getTeamActivity(activeConversationId!, fakeUser),
    enabled: Boolean(activeConversationId),
    refetchInterval: 3000,
  })
  const activeOrchestration = activityQuery.data?.items[0]
  const orchestrationId = typeof activeOrchestration?.orchestration_id === 'string' ? activeOrchestration.orchestration_id : null
  const planRevision = typeof activeOrchestration?.plan_revision === 'number' ? activeOrchestration.plan_revision : 1
  const messagesQuery = useQuery({
    queryKey: ['team-workbench-messages', fakeUser, activeConversationId],
    queryFn: () => getConversationMessages(activeConversationId!, fakeUser),
    enabled: Boolean(activeConversationId),
    refetchInterval: 3000,
  })
  const tasksQuery = useQuery({
    queryKey: ['team-workbench-tasks', fakeUser, orchestrationId],
    queryFn: () => listOrchestrationTasks(orchestrationId!, fakeUser),
    enabled: Boolean(orchestrationId),
    refetchInterval: 3000,
  })
  const eventsQuery = useQuery({
    queryKey: ['team-workbench-events', fakeUser, orchestrationId],
    queryFn: () => listOrchestrationEvents(orchestrationId!, fakeUser),
    enabled: Boolean(orchestrationId),
    refetchInterval: dialog === 'event' ? 3000 : false,
  })
  const artifactsQuery = useQuery({
    queryKey: ['team-workbench-artifacts', fakeUser, orchestrationId],
    queryFn: () => listOrchestrationArtifacts(orchestrationId!, fakeUser),
    enabled: Boolean(orchestrationId),
    refetchInterval: 5000,
  })
  const selectedTask = (tasksQuery.data?.items ?? []).find((task) => task.task_id === selectedTaskId) ?? null
  const selectedArtifact = (artifactsQuery.data?.items ?? []).find((artifact) => artifact.artifact_id === selectedArtifactId) ?? null
  const previewQuery = useQuery({
    queryKey: ['team-workbench-artifact-preview', fakeUser, selectedArtifactId],
    queryFn: () => previewArtifact(selectedArtifactId!, fakeUser),
    enabled: dialog === 'preview' && Boolean(selectedArtifactId),
  })
  // 团队成员可能固定到历史已发布 Deployment；员工目录当前版本即使已 DRAFT，也必须能被读取用于显示来源和名称。
  const employeesQuery = useQuery({ queryKey: ['team-config-employees', fakeUser], queryFn: () => listDigitalEmployees({ limit: 200 }, fakeUser), enabled: Boolean(team) || newTeamOpen })
  const deploymentsQuery = useQuery({ queryKey: ['team-config-deployments', fakeUser], queryFn: () => listDeployments({ status: 'ACTIVE', limit: 200 }, fakeUser), enabled: dialog === 'config' || newTeamOpen })
  const effectiveProfileQuery = useQuery({ queryKey: ['team-config-effective-profile', fakeUser], queryFn: () => getEffectiveEngineProfileDefault(fakeUser), enabled: dialog === 'config' || newTeamOpen })
  const effectiveRouteQuery = useQuery({ queryKey: ['team-config-effective-route', fakeUser], queryFn: () => getEffectiveModelRouteDefault(fakeUser), enabled: dialog === 'config' || newTeamOpen })
  const enginesQuery = useQuery({ queryKey: ['team-config-engines', fakeUser], queryFn: () => listEngineProviders(fakeUser), enabled: Boolean(team) || newTeamOpen })
  const routesQuery = useQuery({ queryKey: ['team-config-model-routes', fakeUser], queryFn: () => listModelRoutes(fakeUser), enabled: dialog === 'config' || newTeamOpen })
  const deployments = deploymentsQuery.data?.items ?? []
  const selectedNewDeployments = deployments.filter((deployment) => newTeamSelectedDeployments.includes(deployment.id))
  const newDeploymentRuntimeQueries = useQueries({
    queries: selectedNewDeployments.map((deployment) => ({
      queryKey: ['team-config-new-deployment-runtime', fakeUser, deployment.id],
      queryFn: () => getDeploymentRuntimeConfig(deployment.id, fakeUser),
      enabled: newTeamOpen,
    })),
  })
  const newDeploymentProfileRefs = selectedNewDeployments.map((deployment, index) => {
    const revision = newDeploymentRuntimeQueries[index]?.data?.revision
    const useOverride = revision?.mode === 'OVERRIDE' && Boolean(revision.execution_profile_id)
    return {
      profile_id: useOverride ? revision?.execution_profile_id : effectiveProfileQuery.data?.profile?.profile_id,
      profile_version: useOverride ? revision?.execution_profile_version : effectiveProfileQuery.data?.profile?.version,
    }
  })
  const newDeploymentProfileQueries = useQueries({
    queries: selectedNewDeployments.map((deployment, index) => {
      const profileId = newDeploymentProfileRefs[index]?.profile_id
      return {
        queryKey: ['team-config-new-profile-versions', fakeUser, deployment.id, profileId],
        queryFn: () => listExecutionProfileVersions(profileId!, fakeUser),
        enabled: newTeamOpen && Boolean(profileId),
      }
    }),
  })
  const selectedNewProfiles = selectedNewDeployments.map((_deployment, index) => {
    const ref = newDeploymentProfileRefs[index]
    return newDeploymentProfileQueries[index]?.data?.items.find((profile) => profile.id === ref?.profile_id && profile.version === ref.profile_version) ?? null
  })
  const newTeamProfilesReady = selectedNewDeployments.length >= 2 && selectedNewProfiles.every((profile) => Boolean(profile && profile.status === 'PUBLISHED' && enginesQuery.data?.items.some((engine) => engine.engine_name === profile.engine_name)))
  const selectedDeployment = deployments.find((deployment) => deployment.id === selectedDeploymentId) ?? deployments[0] ?? null
  const runtimeConfigQuery = useQuery({
    queryKey: ['team-config-runtime', fakeUser, selectedDeployment?.id],
    queryFn: () => getDeploymentRuntimeConfig(selectedDeployment!.id, fakeUser),
    enabled: Boolean(selectedDeployment && (dialog === 'config' || newTeamOpen)),
  })
  const runtimeRevision = runtimeConfigQuery.data?.revision
  const effectiveProfile = effectiveProfileQuery.data?.profile
  const profileId = runtimeRevision?.mode === 'OVERRIDE' && runtimeRevision.execution_profile_id ? runtimeRevision.execution_profile_id : effectiveProfile?.profile_id
  const profileVersion = runtimeRevision?.mode === 'OVERRIDE' && runtimeRevision.execution_profile_version ? runtimeRevision.execution_profile_version : effectiveProfile?.version
  const selectedProfileVersionsQuery = useQuery({
    queryKey: ['team-config-selected-profile-versions', fakeUser, profileId],
    queryFn: () => listExecutionProfileVersions(profileId!, fakeUser),
    enabled: Boolean(profileId && (dialog === 'config' || newTeamOpen)),
  })
  const selectedProfile = selectedProfileVersionsQuery.data?.items.find((profile) => profile.id === profileId && profile.version === profileVersion) ?? null
  const selectedEngine = enginesQuery.data?.items.find((engine) => engine.engine_name === selectedProfile?.engine_name) ?? null
  const selectedRouteId = runtimeRevision?.mode === 'OVERRIDE' && runtimeRevision.model_route_id ? runtimeRevision.model_route_id : effectiveRouteQuery.data?.route?.route_id
  const employees = employeesQuery.data?.items ?? []
  const members = contextTeam?.members ?? []
  const configMembers = team?.members ?? []
  const allTasks = tasksQuery.data?.items ?? []
  const messageItems = (messagesQuery.data?.items ?? []).filter((message) => message.source !== 'system')
  const events = eventsQuery.data?.items ?? []
  const artifacts = artifactsQuery.data?.items ?? []
  const status = typeof activeOrchestration?.status === 'string' ? activeOrchestration.status : null
  const taskCounts = useMemo(() => {
    const counts = { total: allTasks.length, ready: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 }
    allTasks.forEach((task) => {
      if (['PROPOSED', 'READY', 'ASSIGNED'].includes(task.status)) counts.ready += 1
      else if (['CLAIMED', 'RUNNING', 'WAITING_APPROVAL'].includes(task.status)) counts.running += 1
      else if (task.status === 'SUCCEEDED') counts.succeeded += 1
      else if (['FAILED', 'BLOCKED'].includes(task.status)) counts.failed += 1
      else if (['CANCELLED', 'SKIPPED'].includes(task.status)) counts.cancelled += 1
    })
    return counts
  }, [allTasks])

  useEffect(() => {
    if (!selectedDeploymentId && deployments.length) setSelectedDeploymentId(deployments[0].id)
  }, [deployments, selectedDeploymentId])
  useEffect(() => {
    if (team) {
      setOrchestrationMode((team.orchestration_mode as 'USER' | 'ROUTER' | 'HYBRID') || 'HYBRID')
      setApprovalMode((team.approval_mode as 'CONFIRM' | 'AUTO' | 'MANUAL') || 'CONFIRM')
      setMaxConcurrency(team.max_concurrency ?? 3)
      setDeliveryPolicy(team.delivery_policy ?? 'CONFIRM')
      setRouterMemberId(team.router_member_id ?? '')
      setFinalizerMemberId(team.finalizer_member_id ?? '')
    }
  }, [team?.team_id, team?.version, team?.orchestration_mode, team?.approval_mode])
  useEffect(() => {
    setMemberCapabilities(Object.entries(selectedEngine?.capabilities ?? {}).filter(([, enabled]) => enabled).map(([name]) => name))
  }, [selectedEngine?.engine_name])
  useEffect(() => {
    setAssignmentMemberId(selectedTask?.assigned_member_id ?? members[0]?.member_id ?? '')
  }, [selectedTask?.task_id, selectedTask?.assigned_member_id, members.length])
  useEffect(() => {
    const dialogs = [...document.querySelectorAll<HTMLDialogElement>('dialog.team-prototype-modal')]
    const closeOnEscape = (event: Event) => {
      event.preventDefault()
      setDialog(null)
      setNewTeamOpen(false)
    }
    dialogs.forEach((element) => {
      element.addEventListener('cancel', closeOnEscape)
      if (!element.open) {
        if (typeof element.showModal === 'function') {
          try { element.showModal() } catch { element.setAttribute('open', '') }
        } else {
          element.setAttribute('open', '')
        }
      }
    })
    return () => dialogs.forEach((element) => {
      element.removeEventListener('cancel', closeOnEscape)
      if (element.open) {
        try { element.close() } catch { element.removeAttribute('open') }
      }
    })
  }, [dialog, newTeamOpen, selectedTaskId, selectedArtifactId])

  const invalidateTeam = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['team-workbench-teams', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['team-workbench-detail', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['team-workbench-team-conversations', fakeUser] }),
    ])
  }

  async function saveTeamDraft() {
    const name = newTeamName.trim()
    if (!name) return
    let created: { team_id: string; version: number; name: string; status: string } | null = null
    try {
      if (newTeamSelectedDeployments.length < 2 || !newTeamRouterDeployment || !newTeamFinalizerDeployment || newTeamRouterDeployment === newTeamFinalizerDeployment || !newTeamSelectedDeployments.includes(newTeamRouterDeployment) || !newTeamSelectedDeployments.includes(newTeamFinalizerDeployment)) {
        throw new Error('请至少选择两名成员，并分别指定不同的 Router Agent 和 Finalizer。')
      }
      const defaultProfile = effectiveProfileQuery.data ?? await getEffectiveEngineProfileDefault(fakeUser)
      const defaultRoute = effectiveRouteQuery.data ?? await getEffectiveModelRouteDefault(fakeUser)
      const providers = enginesQuery.data?.items ?? (await listEngineProviders(fakeUser)).items
      const chosenDeployments = deployments.filter((deployment) => newTeamSelectedDeployments.includes(deployment.id))
      const members = await Promise.all(chosenDeployments.map(async (deployment) => {
        const runtime = await getDeploymentRuntimeConfig(deployment.id, fakeUser)
        const revision = runtime.revision
        const useOverride = revision?.mode === 'OVERRIDE'
        const profileId = useOverride && revision?.execution_profile_id ? revision.execution_profile_id : defaultProfile.profile?.profile_id
        const profileVersion = useOverride && revision?.execution_profile_version ? revision.execution_profile_version : defaultProfile.profile?.version
        const profile = profileId && profileVersion
          ? (await listExecutionProfileVersions(profileId, fakeUser)).items.find((item) => item.id === profileId && item.version === profileVersion)
          : undefined
        if (!profile || profile.status !== 'PUBLISHED') throw new Error(`部署“${deployment.id}”没有可用的已发布 EngineProvider 配置。`)
        const provider = providers.find((item) => item.engine_name === profile.engine_name)
        if (!provider) throw new Error(`EngineProvider“${profile.engine_name}”当前未注册。`)
        const routeId = useOverride && revision?.model_route_id ? revision.model_route_id : defaultRoute.route?.route_id ?? null
        return {
          deployment,
          engine_name: profile.engine_name,
          model_route_id: routeId,
          capabilities: Object.entries(provider.capabilities).filter(([, supported]) => supported).map(([capability]) => capability),
          role: deployment.id === newTeamRouterDeployment ? 'ROUTER' as const : deployment.id === newTeamFinalizerDeployment ? 'FINALIZER' as const : 'MEMBER' as const,
        }
      }))
      created = await createTeam(name, fakeUser)
      await updateTeamPolicy(created.team_id, created.version, {
        orchestration_mode: newTeamMode,
        approval_mode: 'CONFIRM',
        max_concurrency: newTeamConcurrency,
        delivery_policy: 'CONFIRM',
      }, fakeUser)
      for (const member of members) {
        await addTeamMember(created.team_id, created.version, {
          employee_id: member.deployment.employee_id,
          employee_version: member.deployment.version,
          deployment_id: member.deployment.id,
          role: member.role,
          engine_name: member.engine_name,
          model_route_id: member.model_route_id,
          capabilities: member.capabilities,
          max_concurrency: 1,
        }, fakeUser)
      }
      setNewTeamName('')
      setNewTeamSelectedDeployments([])
      setNewTeamRouterDeployment('')
      setNewTeamFinalizerDeployment('')
      setNewTeamOpen(false)
      setSelectedTeamKey(`${created.team_id}:${created.version}`)
      await invalidateTeam()
      setNotice(`团队草稿“${name}”及 ${members.length} 名成员已创建，请检查配置后发布。`)
      setDialog('config')
    } catch (error) {
      if (created) {
        setNewTeamOpen(false)
        setSelectedTeamKey(`${created.team_id}:${created.version}`)
        await invalidateTeam()
        setDialog('config')
      }
      setNotice(error instanceof Error ? `创建团队失败：${error.message}` : '创建团队失败，请重试。')
    }
  }

  async function createRevision() {
    if (!team) return
    try {
      const draft = await forkTeamRevision(team.team_id, team.version, fakeUser)
      setSelectedTeamKey(`${draft.team_id}:${draft.version}`)
      await invalidateTeam()
      setNotice(`已创建团队配置草稿 v${draft.version}；已有对话仍固定使用原版本。`)
    } catch (error) {
      setNotice(error instanceof Error ? `创建配置草稿失败：${error.message}` : '创建配置草稿失败，请重试。')
    }
  }

  async function addMember() {
    if (!team || !selectedDeployment || !selectedProfile || !runtimeRevision) return
    try {
      const result = await addTeamMember(team.team_id, team.version, {
        employee_id: selectedDeployment.employee_id,
        employee_version: selectedDeployment.version,
        deployment_id: selectedDeployment.id,
        role: selectedRole,
        engine_name: selectedProfile.engine_name,
        model_route_id: selectedRouteId ?? null,
        capabilities: memberCapabilities,
        max_concurrency: memberConcurrency,
      }, fakeUser)
      queryClient.setQueryData(['team-workbench-detail', fakeUser, team.team_id, team.version], result)
      await invalidateTeam()
      setNotice(`已将“${employees.find((employee) => employee.id === selectedDeployment.employee_id)?.name ?? selectedDeployment.employee_id}”加入团队。`)
    } catch (error) {
      setNotice(error instanceof Error ? `添加成员失败：${error.message}` : '添加成员失败，请重试。')
    }
  }

  async function removeMember(member: TeamMemberSummary) {
    if (!team) return
    try {
      const result = await removeTeamMember(team.team_id, team.version, member.member_id, fakeUser)
      queryClient.setQueryData(['team-workbench-detail', fakeUser, team.team_id, team.version], result)
      await invalidateTeam()
      setNotice('团队成员已从此草稿版本移除。')
    } catch (error) {
      setNotice(error instanceof Error ? `移除成员失败：${error.message}` : '移除成员失败，请重试。')
    }
  }

  async function savePolicy() {
    if (!team || team.status !== 'DRAFT') return
    try {
      const result = await updateTeamPolicy(team.team_id, team.version, {
        orchestration_mode: orchestrationMode,
        approval_mode: approvalMode,
        max_concurrency: maxConcurrency,
        delivery_policy: deliveryPolicy,
      }, fakeUser)
      queryClient.setQueryData(['team-workbench-detail', fakeUser, team.team_id, team.version], result)
      if (routerMemberId && finalizerMemberId && routerMemberId !== finalizerMemberId) {
        const roles = await updateTeamRoles(team.team_id, team.version, { router_member_id: routerMemberId, finalizer_member_id: finalizerMemberId }, fakeUser)
        queryClient.setQueryData(['team-workbench-detail', fakeUser, team.team_id, team.version], roles)
      }
      await invalidateTeam()
      setNotice('团队编排和计划审批策略已保存到当前草稿。')
    } catch (error) {
      setNotice(error instanceof Error ? `保存团队配置失败：${error.message}` : '保存团队配置失败，请重试。')
    }
  }

  async function publishCurrentTeam() {
    if (!team || team.status !== 'DRAFT') return
    try {
      const published = await publishTeam(team.team_id, team.version, fakeUser)
      queryClient.setQueryData(['team-workbench-detail', fakeUser, team.team_id, team.version], published)
      setNotice(`团队版本 v${published.version} 已发布，可以创建新的团队对话。`)
      await invalidateTeam()
    } catch (error) {
      setNotice(error instanceof Error ? `发布团队失败：${error.message}` : '发布团队失败，请检查 Router 和 Finalizer 配置。')
    }
  }

  async function createConversation() {
    if (!team || team.status !== 'PUBLISHED') return
    try {
      const result = await createTeamConversation({ title: `${team.name} · 新对话`, team_id: team.team_id, team_version: team.version }, fakeUser)
      setSelectedConversationId(result.id)
      setNotice('团队对话已创建，成员 Session 已固定到当前发布版本。')
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-team-conversations', fakeUser, team.team_id] })
    } catch (error) {
      setNotice(error instanceof Error ? `创建团队对话失败：${error.message}` : '创建团队对话失败，请检查成员部署和引擎配置。')
    }
  }

  async function submitTeamTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!activeConversationId || !taskInput.trim() || status === 'PLANNING') return
    try {
      const result = await submitTeamTurn(activeConversationId, { text: taskInput.trim(), idempotency_key: crypto.randomUUID() }, fakeUser)
      setTaskInput('')
      setNotice(result.status === 'PLANNING'
        ? 'Router 计划已排队，正在等待 Runtime Worker 生成计划。'
        : `Router Agent 已生成 ${result.task_count} 个任务，当前计划：${statusLabel(result.status)}。`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['team-workbench-activity', fakeUser, activeConversationId] }),
        queryClient.invalidateQueries({ queryKey: ['team-workbench-messages', fakeUser, activeConversationId] }),
      ])
      if (result.status === 'AWAITING_PLAN_APPROVAL') setDialog('plan')
    } catch (error) {
      setNotice(error instanceof Error ? `团队任务提交失败：${error.message}` : '团队任务提交失败，请检查 Router 引擎配置。')
    }
  }

  async function replanCurrentOrchestration() {
    if (!orchestrationId || !canOperate || status === 'PLANNING') return
    try {
      const result = await replanTeamOrchestration(orchestrationId, fakeUser)
      setNotice(result.status === 'PLANNING'
        ? '新的计划草稿已提交，原计划中的运行中任务继续执行。'
        : `新的计划草稿已创建：${statusLabel(result.status)}。`)
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-activity', fakeUser, activeConversationId] })
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-messages', fakeUser, activeConversationId] })
    } catch (error) {
      setNotice(error instanceof Error ? `重新规划失败：${error.message}` : '重新规划失败，请检查 Router 配置后重试。')
    }
  }

  async function changeOrchestration(action: 'pause' | 'resume' | 'cancel') {
    if (!orchestrationId) return
    try {
      if (action === 'pause') await pauseTeamOrchestration(orchestrationId, fakeUser)
      else if (action === 'resume') await resumeTeamOrchestration(orchestrationId, fakeUser)
      else await cancelTeamOrchestration(orchestrationId, fakeUser)
      setNotice(action === 'pause' ? '已暂停后续任务派发。' : action === 'resume' ? '已恢复团队任务派发。' : '已取消尚未完成的团队任务。')
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-activity', fakeUser, activeConversationId] })
    } catch (error) {
      setNotice(error instanceof Error ? `操作失败：${error.message}` : '操作失败，请重试。')
    }
  }

  async function confirmPlan() {
    if (!orchestrationId) return
    try {
      await approveTeamPlan(orchestrationId, fakeUser)
      setDialog(null)
      setNotice('计划已确认，READY 任务已并行派发给对应成员。')
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-activity', fakeUser, activeConversationId] })
    } catch (error) {
      setNotice(error instanceof Error ? `确认计划失败：${error.message}` : '确认计划失败，请重试。')
    }
  }

  async function changeTask(task: OrchestrationTaskSummary, action: 'retry' | 'cancel') {
    try {
      if (action === 'retry') await retryTeamTask(task.task_id, fakeUser)
      else await cancelTeamTask(task.task_id, fakeUser)
      setNotice(action === 'retry' ? '任务已重新排队。' : '已取消该任务。')
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-activity', fakeUser, activeConversationId] })
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-tasks', fakeUser, orchestrationId] })
    } catch (error) {
      setNotice(error instanceof Error ? `任务操作失败：${error.message}` : '任务操作失败，请重试。')
    }
  }

  async function assignTask(task: OrchestrationTaskSummary) {
    if (!assignmentMemberId) return
    try {
      await assignTeamTask(task.task_id, assignmentMemberId, fakeUser)
      setNotice('任务已固定分配给所选团队成员。')
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-activity', fakeUser, activeConversationId] })
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-tasks', fakeUser, orchestrationId] })
    } catch (error) {
      setNotice(error instanceof Error ? `分配任务失败：${error.message}` : '分配任务失败，请重试。')
    }
  }

  async function openPreview(artifact: TeamArtifactSummary) {
    setSelectedArtifactId(artifact.artifact_id)
    setDialog('preview')
  }

  async function downloadSelectedArtifact(artifact: TeamArtifactSummary) {
    try {
      const blob = await downloadArtifact(artifact.artifact_id, fakeUser)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = artifact.filename
      link.click()
      URL.revokeObjectURL(url)
      setNotice(`已下载产出物“${artifact.filename}”。`)
    } catch (error) {
      setNotice(error instanceof Error ? `下载失败：${error.message}` : '下载失败，请重试。')
    }
  }

  async function copySelectedTrace() {
    const traceId = selectedTask?.latest_attempt?.trace_id ?? selectedTask?.latest_attempt?.run_id
    if (!traceId) return
    try {
      await navigator.clipboard.writeText(traceId)
      setNotice('Trace 标识已复制。')
    } catch {
      setNotice(`Trace 标识：${traceId}`)
    }
  }

  function addPreviewToDelivery() {
    if (!selectedArtifactId) return
    setDeliverySelection((current) => current.includes(selectedArtifactId) ? current : [...current, selectedArtifactId])
    setDialog('delivery')
  }

  async function deliverSelectedArtifacts() {
    if (!orchestrationId || !deliverySelection.length) return
    setDeliveryPending(true)
    try {
      const idempotencyKey = crypto.randomUUID()
      const bundle = await bundleTeamArtifacts(orchestrationId, { artifact_ids: deliverySelection, channel_name: 'web', idempotency_key: idempotencyKey }, fakeUser)
      await deliverTeamArtifacts(orchestrationId, { artifact_ids: [bundle.artifact_id], channel_name: 'web', idempotency_key: idempotencyKey }, fakeUser)
      setDeliverySelection([])
      setDialog(null)
      setNotice(`已将 ${deliverySelection.length} 个产出物打包并交付到当前 Web 工作台。`)
      await queryClient.invalidateQueries({ queryKey: ['team-workbench-artifacts', fakeUser, orchestrationId] })
    } catch (error) {
      setNotice(error instanceof Error ? `交付失败：${error.message}` : '交付失败，请重试。')
    } finally {
      setDeliveryPending(false)
    }
  }

  const filteredConversations = conversationItems.filter((item) => {
    const query = conversationSearch.toLocaleLowerCase()
    return !query || item.title.toLocaleLowerCase().includes(query) || (item.last_message_preview ?? '').toLocaleLowerCase().includes(query)
  })
  const orchestrationTasks = allTasks
  const currentMemberActivity = members.map((member) => ({
    member,
    task: orchestrationTasks.find((task) => task.assigned_member_id === member.member_id && ['ASSIGNED', 'CLAIMED', 'RUNNING', 'WAITING_APPROVAL'].includes(task.status)) ?? null,
  }))
  const taskStatusText = status ? statusLabel(status) : '等待任务'
  const noticeTitle = activeConversationId ? (status ? `${contextTeam?.name ?? '团队'} · ${taskStatusText}` : '团队对话已就绪') : (team?.status === 'PUBLISHED' ? '尚无团队对话' : '团队草稿待配置')
  const poolItems = poolQuery.data?.items ?? []
  const availableDeliveryArtifacts = artifacts.filter((artifact) => artifact.status === 'READY')
  const selectedEmployeeForDeployment = selectedDeployment ? employees.find((employee) => employee.id === selectedDeployment.employee_id) : null
  const profileNeedsConfiguration = Boolean(selectedDeployment && !selectedProfile)

  return (
    <section className="team-prototype-page" aria-label="团队协作工作台">
      <header className="team-prototype-heading">
        <div><span className="team-prototype-kicker">团队执行 / 群聊工作台</span><h1>Agent 团队群聊</h1><p>路由 Agent 拆分任务，成员在独立引擎队列中并行执行，产出物集中交付。</p></div>
        <div className="team-prototype-heading-actions">
          <label>当前团队<select aria-label="当前团队" value={team ? `${team.team_id}:${team.version}` : ''} onChange={(event) => { setSelectedTeamKey(event.target.value); setSelectedConversationId(null) }} disabled={!teams.length}>
            {!teams.length && <option value="">暂无团队</option>}
            {teams.map((item) => <option key={`${item.team_id}:${item.version}`} value={`${item.team_id}:${item.version}`}>{item.name} · v{item.version} · {statusLabel(item.status)}</option>)}
          </select></label>
          {canManage && <button type="button" onClick={() => setNewTeamOpen(true)}>新建团队</button>}
          {team && <button type="button" onClick={() => setDialog('config')}>{team.status === 'DRAFT' && canManage ? '团队配置 · 编辑草稿' : '团队配置 · 只读'}</button>}
          <button type="button" onClick={onSwitchToSingle}>数字员工对话</button>
          {team?.status === 'DRAFT' && canManage && <button type="button" className="team-prototype-primary" disabled={!team.router_member_id || !team.finalizer_member_id} onClick={() => void publishCurrentTeam()}>发布团队版本</button>}
        </div>
      </header>

      <div className="team-prototype-notice"><div><strong>{noticeTitle}</strong><span>{activeConversationId ? (activeOrchestration ? `当前编排 ${orchestrationId} · 计划 v${activeOrchestration.plan_revision} · ${taskCounts.total} 个任务` : '提交任务后由 Router Agent 生成计划，确认后开始执行。') : (team?.status === 'PUBLISHED' ? '创建团队对话后，成员会使用固定的数字员工部署和引擎配置。' : '先配置 Router Agent、Finalizer 和成员引擎，再发布团队版本。')}</span></div><div className="team-prototype-chips"><span>{contextTeam?.orchestration_mode ?? 'HYBRID'} 编排</span>{poolItems.map((pool) => <span key={pool.engine_name} className="team-prototype-pool-chip">{pool.engine_name} · 队列 {pool.queue_depth} · {pool.healthy ? '可用' : '不可用'}</span>)}</div></div>

      {teamsQuery.isLoading && <div className="team-prototype-state">正在读取团队配置…</div>}
      {teamsQuery.isError && <div className="team-prototype-state error"><span>团队配置读取失败。</span><button type="button" onClick={() => void teamsQuery.refetch()}>重试</button></div>}
      {detailQuery.isError && <div className="team-prototype-state error">当前团队版本读取失败，请重试。</div>}
      {poolQuery.isError && <div className="team-prototype-state error"><span>Worker Pool 状态读取失败。</span><button type="button" onClick={() => void poolQuery.refetch()}>重试</button></div>}
      {conversationsQuery.isLoading && team?.status === 'PUBLISHED' && <div className="team-prototype-state">正在读取团队对话…</div>}
      {conversationsQuery.isError && <div className="team-prototype-state error"><span>团队对话读取失败，既有任务和产物仍保存在服务端。</span><button type="button" onClick={() => void conversationsQuery.refetch()}>重试</button></div>}

      <div className="team-prototype-grid">
        <nav className="team-prototype-panel workbench-panel team-prototype-conversations workbench-conversation-panel" aria-label="团队对话列表">
          <div className="team-prototype-panel-heading workbench-panel-heading"><div><span className="workbench-section-kicker">团队对话</span><h2>最近工作</h2></div><button className="workbench-new-button" type="button" onClick={() => void createConversation()} disabled={!canOperate || team?.status !== 'PUBLISHED'}><span aria-hidden="true">＋</span> 新对话</button></div>
          <label className="workbench-search-label" htmlFor="team-workbench-conversation-search">查找对话</label><input id="team-workbench-conversation-search" className="workbench-search" type="search" placeholder="搜索标题或消息" value={conversationSearch} onChange={(event) => setConversationSearch(event.target.value)} />
          <div className="team-prototype-conversation-list workbench-conversation-list">
            {filteredConversations.map((conversation) => <button key={conversation.conversation_id} type="button" className={`workbench-conversation ${activeConversationId === conversation.conversation_id ? 'selected' : ''}`} onClick={() => setSelectedConversationId(conversation.conversation_id)}>
              <span className="workbench-conversation-topline"><strong>{conversation.title}</strong><time>{formatTime(conversation.updated_at)}</time></span><span className="workbench-conversation-preview">{conversation.last_message_preview || `团队 Conversation · ${statusLabel(conversation.status)}`}</span><span className="workbench-conversation-bottomline"><span className={`workbench-status workbench-status-${conversation.status.toLowerCase()}`}><span className="workbench-status-dot" />{statusLabel(conversation.status)}</span><em>TEAM · v{conversation.team_version}</em></span>
            </button>)}
            {!conversationsQuery.isLoading && filteredConversations.length === 0 && <div className="team-prototype-empty">{conversationItems.length ? '没有匹配的团队对话。' : team?.status === 'PUBLISHED' ? '还没有团队对话，点击“新对话”创建。' : '发布团队版本后即可新建团队对话。'}</div>}
          </div>
        </nav>

        <main className="team-prototype-panel workbench-panel team-prototype-timeline workbench-timeline-panel" aria-label="团队群聊时间线">
          <header className="team-prototype-timeline-heading workbench-timeline-heading"><div><span className="workbench-section-kicker">{orchestrationId ? `当前编排 · ${orchestrationId}` : activeConversationId ? '当前团队 Conversation' : '尚未选择团队对话'}</span><h2>{activeConversationId ? activeConversation?.title : '创建一个团队对话开始协作'}</h2><div className="team-prototype-chips"><span>TEAM · v{activeConversation?.team_version ?? team?.version ?? '—'}</span><span>{taskCounts.total} 个任务</span>{activeOrchestration && <span>计划 v{planRevision}</span>}{contextTeam && <span>{contextTeam.approval_mode} · {contextTeam.orchestration_mode}</span>}</div></div><b>● {status ? statusLabel(status) : '就绪'}</b></header>
          {messagesQuery.isLoading && activeConversationId && <div className="team-prototype-loading">正在读取对话消息…</div>}
          {messagesQuery.isError && <div className="team-prototype-state error">对话消息读取失败，请重试。</div>}
          {activityQuery.isError && <div className="team-prototype-state error"><span>任务与成员活动读取失败。</span><button type="button" onClick={() => void activityQuery.refetch()}>重试</button></div>}
          {tasksQuery.isError && <div className="team-prototype-state error"><span>任务队列读取失败。</span><button type="button" onClick={() => void tasksQuery.refetch()}>重试</button></div>}
          {artifactsQuery.isError && <div className="team-prototype-state error"><span>产物列表读取失败。</span><button type="button" onClick={() => void artifactsQuery.refetch()}>重试</button></div>}
          <div className="team-prototype-events workbench-timeline">
            {messageItems.map((message) => {
              const routerMessage = message.source === 'agent' && members.some((member) => member.role === 'ROUTER' && (member.member_id === message.team_member_id || member.employee_id === message.sender_id))
              const senderName = message.source === 'user' ? '你' : employees.find((employee) => employee.id === message.sender_id)?.name ?? members.find((member) => member.member_id === message.team_member_id)?.employee_id ?? message.sender_name ?? '团队成员'
              return <article key={message.id} className={`event ${message.source === 'user' ? 'user' : routerMessage ? 'router' : 'task'}`}><span className="marker" aria-hidden="true" /><div><div className="event-meta"><span className="event-label">{senderName}</span><time>{messageTime(message)}</time></div><p>{message.text}</p>{routerMessage && orchestrationId && <div className="event-actions"><button type="button" onClick={() => setDialog('plan')}>查看任务计划</button>{canOperate && <button type="button" disabled={status === 'PLANNING'} onClick={() => void replanCurrentOrchestration()}>重新规划</button>}</div>}</div></article>
            })}
            {orchestrationTasks.map((task) => <article key={task.task_id} className="event task"><span className="marker" aria-hidden="true" /><div><div className="event-meta"><span className="event-label">{statusLabel(task.status)} · {members.find((member) => member.member_id === task.assigned_member_id)?.employee_id ? employees.find((employee) => employee.id === members.find((member) => member.member_id === task.assigned_member_id)?.employee_id)?.name : '等待分配'}</span><time>{formatTime(task.started_at)}</time></div><p>{task.title}{task.error_code ? ` · ${task.error_code}` : ''}</p><div className="task-inline"><strong>{task.client_key} · {statusLabel(task.status)}</strong><span>{task.engine_name ?? '等待引擎'} · {task.latest_attempt?.worker_pool ?? '尚未领取'} · Attempt {task.latest_attempt?.attempt_no ?? task.attempt}</span></div></div></article>)}
            {activeConversationId && messageItems.length === 0 && orchestrationTasks.length === 0 && !messagesQuery.isLoading && <div className="team-prototype-empty">这是团队对话的开始，提交一条任务后 Router Agent 会生成执行计划。</div>}
            {events.length > 0 && <details className="workbench-internal-details"><summary>执行详情（{events.length} 条服务端事件）</summary><p className="workbench-internal-intro">按服务端 sequence 展示任务、Worker、Run 和状态变化；隐藏原始 Prompt、哈希和完整内部载荷。</p><div className="workbench-internal-list">{events.slice(-10).map((event) => { const fields = eventFields(event); return <article className="workbench-internal-event" key={event.event_id}><div className="workbench-internal-meta"><strong>{eventTitle(event)}</strong><span>seq {event.sequence} · {formatTime(event.created_at)}</span></div>{fields.length ? <div className="workbench-internal-fields">{fields.map((field) => <span key={field.label}><b>{field.label}</b><code>{field.value}</code></span>)}</div> : <span className="workbench-internal-empty">无附加摘要字段</span>}</article> })}</div></details>}
          </div>
          <form className="team-prototype-composer workbench-composer" onSubmit={submitTeamTask}>
            <label htmlFor="team-workbench-message-input">提交任务</label><textarea id="team-workbench-message-input" aria-label="提交新的团队任务" value={taskInput} onChange={(event) => setTaskInput(event.target.value)} placeholder="描述要交给团队完成的工作…" rows={3} disabled={!activeConversationId || !canOperate || status === 'CANCELLED' || status === 'PLANNING'} />
            <div className="workbench-composer-footer"><span>{status === 'PLANNING' ? 'Router 计划正在 Runtime Worker 队列中生成，完成后会显示任务计划。' : activeConversationId ? '提交后由 Router Agent 生成任务计划；CONFIRM 模式下需先确认计划。' : '请先创建或选择一个团队对话，再提交新的团队任务。'}</span>{activeConversationId ? <button className="team-prototype-submit-button" type="submit" disabled={!canOperate || !taskInput.trim() || status === 'PLANNING'}>提交团队任务</button> : <button className="team-prototype-submit-button" type="button" disabled={!canOperate || team?.status !== 'PUBLISHED'} onClick={() => void createConversation()}>新建对话</button>}</div>
          </form>
        </main>

        <aside className="team-prototype-panel team-prototype-side" aria-label="团队任务与产出物">
          <header className="team-prototype-side-heading"><div><span>团队执行</span><h2>任务与产出物</h2></div><em>{canOperate ? '可操作' : '只读'}</em></header>
          <div className="team-prototype-stats" aria-label="任务状态统计"><b>{taskCounts.total}<small>全部任务</small></b><b>{taskCounts.ready}<small>待执行</small></b><b>{taskCounts.running}<small>进行中</small></b><b>{taskCounts.succeeded}<small>已完成</small></b><b>{taskCounts.failed}<small>失败</small></b><b>{taskCounts.cancelled}<small>已取消</small></b></div>
          <section className="team-section"><div className="section-title"><strong>团队成员和能力</strong><button className="link-button" type="button" onClick={() => setDialog('config')}>管理团队</button></div><div className="member-list">
            {members.map((member) => {
              const employee = employees.find((item) => item.id === member.employee_id)
              const memberTask = currentMemberActivity.find((activity) => activity.member.member_id === member.member_id)?.task
              const busy = Boolean(memberTask && ['CLAIMED', 'RUNNING', 'WAITING_APPROVAL'].includes(memberTask.status))
              const executionMode = enginesQuery.data?.items.find((engine) => engine.engine_name === member.engine_name)?.execution_mode
              const avatarClass = member.engine_name.toLowerCase().includes('codex') ? 'codex' : 'deepseek'
              return <article className="member-row" key={member.member_id}><span className={`member-avatar ${avatarClass}`}>{(employee?.name ?? member.employee_id).slice(0, 1)}</span><span className="member-name"><strong>{employee?.name ?? member.employee_id}</strong><span>{member.role} · 负载 {busy ? 1 : 0}/{member.max_concurrency}</span><span className={`engine-label ${avatarClass}`}>{member.engine_name}</span><span className="member-runtime-sr">{member.role} · {member.engine_name}</span></span><span className={`health ${busy ? 'busy' : ''}`}><i></i>{busy ? '执行中' : '空闲'}</span></article>
            })}
            {!members.length && <div className="team-prototype-empty">当前草稿尚未添加成员。</div>}
          </div></section>
          <section className="team-section activity-section"><div className="section-title"><strong>成员当前活动</strong><span>实时快照 · {new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span></div><div className="activity-list">
            {currentMemberActivity.map(({ member, task }) => {
              const employeeName = employees.find((employee) => employee.id === member.employee_id)?.name ?? member.employee_id
              return <article className={task ? 'activity-row active' : 'activity-row'} key={member.member_id}><div className="activity-top"><strong>{employeeName}{task ? ` · ${task.client_key} ${task.title}` : ''}</strong><span className={`pool-status ${task && ['RUNNING', 'CLAIMED'].includes(task.status) ? 'busy' : ''}`}><i></i>{task ? statusLabel(task.status) : '空闲'}</span></div><div className="activity-phase">{task ? `${statusLabel(task.status)} · ${task.title}` : '当前没有认领任务'}</div><div className="activity-meta"><code>{task?.latest_attempt?.worker_pool ?? `${member.engine_name}-pool`}</code><span>·</span><span>{task ? '已分配' : '等待派发'}</span></div><div className="activity-actions"><button type="button" disabled={!task} onClick={() => { setSelectedTaskId(task?.task_id ?? null); setDialog('task') }}>查看任务</button><button type="button" disabled={!orchestrationId} onClick={() => { setSelectedTaskId(task?.task_id ?? null); setDialog('event') }}>查看最近事件</button></div></article>
            })}
            {!members.length && <div className="team-prototype-empty">团队配置发布后显示成员活动。</div>}
          </div></section>
          <section className="team-section"><div className="section-title"><strong>任务队列</strong><button className="link-button" type="button" onClick={() => setDialog('plan')} disabled={!orchestrationId}>查看计划</button></div><div className="task-list">
            {tasksQuery.isLoading && <div className="team-prototype-loading">正在读取任务队列…</div>}
            {orchestrationTasks.map((task) => <button className={selectedTaskId === task.task_id ? 'task-row selected' : 'task-row'} key={task.task_id} type="button" onClick={() => { setSelectedTaskId(task.task_id); setDialog('task') }}><span className="task-row-top"><strong>{task.client_key} · {task.title}</strong><span className={`task-status ${task.status.toLowerCase()}`}><i></i>{statusLabel(task.status)}</span></span><span className="task-row-meta"><code>{members.find((member) => member.member_id === task.assigned_member_id)?.employee_id ?? '未分配'}</code><span>·</span><span>{task.engine_name ?? '等待引擎'}</span><span>·</span><span>Attempt {task.latest_attempt?.attempt_no ?? task.attempt}</span></span></button>)}
            {!orchestrationTasks.length && !tasksQuery.isLoading && <div className="team-prototype-empty">提交团队任务后，这里显示 Router 计划和真实执行状态。</div>}
          </div></section>
          <section className="team-section"><div className="section-title"><strong>产出物</strong><span>{artifacts.length} 个文件</span></div><div className="artifact-list">
            {artifactsQuery.isLoading && <div className="team-prototype-loading">正在读取产出物…</div>}
            {artifacts.map((artifact) => <div className="artifact-row" key={artifact.artifact_id}><div className="artifact-top"><span className="artifact-icon">文件</span><span className="artifact-body"><strong>{artifact.filename}</strong><span className="artifact-meta">{artifact.source_engine ?? '团队产出'} · {(artifact.size_bytes / 1024).toFixed(1)} KB</span></span><span className={`artifact-state ${artifact.status === 'FAILED' ? 'failed' : artifact.status === 'READY' ? '' : 'pending'}`}>{statusLabel(artifact.status)}</span></div><div className="artifact-actions"><button type="button" onClick={() => void openPreview(artifact)}>预览</button><button type="button" onClick={() => void downloadSelectedArtifact(artifact)}>下载</button><button type="button" disabled={!canManage || artifact.status !== 'READY'} onClick={() => { setDeliverySelection([artifact.artifact_id]); setDialog('delivery') }}>交付</button></div></div>)}
            {!artifacts.length && !artifactsQuery.isLoading && <div className="team-prototype-empty">任务产生的报告、数据和文件会保存在这里。</div>}
          </div></section>
          <footer><button type="button" disabled={!canOperate || !orchestrationId || !['RUNNING', 'READY', 'PAUSED'].includes(status ?? '')} onClick={() => void changeOrchestration(status === 'PAUSED' ? 'resume' : 'pause')}>{status === 'PAUSED' ? '继续团队' : '暂停团队'}</button><button type="button" disabled={!canManage || !availableDeliveryArtifacts.length} onClick={() => { setDeliverySelection(availableDeliveryArtifacts.map((artifact) => artifact.artifact_id)); setDialog('delivery') }}>交付产出物</button></footer>
          {poolItems.map((pool) => <small key={pool.engine_name} className="team-prototype-pool">{pool.engine_name} · 队列 {pool.queue_depth} · 运行中 {pool.active_workers} · Worker {pool.worker_count ?? pool.active_workers}/{pool.max_concurrency} · {pool.healthy ? '可用' : '不可用'}</small>)}
        </aside>
      </div>

      {notice && <div className="team-prototype-feedback" role="status">{notice}</div>}

      {newTeamOpen && <dialog className="team-prototype-modal team-prototype-config-modal" aria-label="新建 Agent 团队">
        <div className="dialog-head"><div><h2>新建 Agent 团队</h2><p>先保存团队草稿，再发布一个固定版本供 Conversation 绑定。</p></div><button className="dialog-close" type="button" onClick={() => setNewTeamOpen(false)} aria-label="关闭新建团队">×</button></div>
        <div className="dialog-body">
          <div className="dialog-context"><span>创建范围：当前租户</span><span>状态：草稿</span><span>凭据：只引用已有配置</span></div>
          <div className="config-grid">
            <div className="config-field full"><label htmlFor="new-team-name">团队名称</label><input id="new-team-name" value={newTeamName} onChange={(event) => setNewTeamName(event.target.value)} maxLength={255} placeholder="例如：数据分析与交付团队" /></div>
            <div className="config-field"><label htmlFor="new-team-mode">编排模式</label><select id="new-team-mode" value={newTeamMode} onChange={(event) => setNewTeamMode(event.target.value as 'USER' | 'ROUTER' | 'HYBRID')}><option value="HYBRID">HYBRID · 路由提案 + 用户确认</option><option value="ROUTER">ROUTER · 路由分配</option><option value="USER">USER · 手动分配</option></select></div>
            <div className="config-field"><label htmlFor="new-team-concurrency">最大并发任务</label><input id="new-team-concurrency" type="number" min={1} max={100} value={newTeamConcurrency} onChange={(event) => setNewTeamConcurrency(Math.max(1, Number(event.target.value)))} /></div>
            <div className="config-field"><label htmlFor="new-router-member">路由 Agent</label><select id="new-router-member" aria-label="新团队路由 Agent" value={newTeamRouterDeployment} onChange={(event) => setNewTeamRouterDeployment(event.target.value)}><option value="">选择 Router Agent</option>{deployments.filter((deployment) => newTeamSelectedDeployments.includes(deployment.id)).map((deployment) => <option key={deployment.id} value={deployment.id}>{employees.find((employee) => employee.id === deployment.employee_id)?.name ?? deployment.employee_id}</option>)}</select></div>
            <div className="config-field"><label htmlFor="new-finalizer-member">汇总 Agent</label><select id="new-finalizer-member" aria-label="新团队汇总 Agent" value={newTeamFinalizerDeployment} onChange={(event) => setNewTeamFinalizerDeployment(event.target.value)}><option value="">选择 Finalizer</option>{deployments.filter((deployment) => newTeamSelectedDeployments.includes(deployment.id)).map((deployment) => <option key={deployment.id} value={deployment.id}>{employees.find((employee) => employee.id === deployment.employee_id)?.name ?? deployment.employee_id}</option>)}</select></div>
          </div>
          <section className="config-card"><div className="config-card-heading"><strong>团队成员和能力</strong><span>已选择 {newTeamSelectedDeployments.length} 名成员</span></div><div className="team-prototype-config-members">{deployments.map((deployment) => {
            const employee = employees.find((item) => item.id === deployment.employee_id)
            const selected = newTeamSelectedDeployments.includes(deployment.id)
            const selectionIndex = selectedNewDeployments.findIndex((item) => item.id === deployment.id)
            const runtime = selectionIndex >= 0 ? newDeploymentRuntimeQueries[selectionIndex]?.data : undefined
            const revision = runtime?.revision
            const profileId = revision?.mode === 'OVERRIDE' && revision.execution_profile_id ? revision.execution_profile_id : effectiveProfileQuery.data?.profile?.profile_id
            const profileVersion = revision?.mode === 'OVERRIDE' && revision.execution_profile_version ? revision.execution_profile_version : effectiveProfileQuery.data?.profile?.version
            const profile = newDeploymentProfileQueries[selectionIndex]?.data?.items.find((item) => item.id === newDeploymentProfileRefs[selectionIndex]?.profile_id && item.version === newDeploymentProfileRefs[selectionIndex]?.profile_version)
            const provider = enginesQuery.data?.items.find((item) => item.engine_name === profile?.engine_name)
            const routeId = revision?.mode === 'OVERRIDE' && revision.model_route_id ? revision.model_route_id : effectiveRouteQuery.data?.route?.route_id
            const routeName = routesQuery.data?.items.find((item) => item.route_id === routeId)?.alias
            const capabilityNames = Object.entries(provider?.capabilities ?? {}).filter(([, supported]) => supported).map(([name]) => name)
            const runtimeQuery = selectionIndex >= 0 ? newDeploymentRuntimeQueries[selectionIndex] : undefined
            const profileQuery = selectionIndex >= 0 ? newDeploymentProfileQueries[selectionIndex] : undefined
            const profileRef = selectionIndex >= 0 ? newDeploymentProfileRefs[selectionIndex] : undefined
            const configPending = selected && Boolean(runtimeQuery?.isLoading || (!profileRef?.profile_id && !effectiveProfileQuery.isFetched) || (profileRef?.profile_id && profileQuery?.isLoading))
            const configError = selected && Boolean(runtimeQuery?.isError || profileQuery?.isError)
            const avatarClass = profile?.engine_name?.toLowerCase().includes('codex') ? 'codex' : 'deepseek'
            const avatarText = (employee?.name ?? deployment.employee_id).slice(0, 1)
            return <label className="config-member-row team-prototype-deployment-option" key={deployment.id}><span className={`config-member-avatar ${avatarClass}`}>{avatarText}</span><span className="config-member-info"><strong>{employee?.name ?? deployment.employee_id}</strong><span>员工版本 v{deployment.version} · Deployment {deployment.id} · {statusLabel(deployment.status)}</span>{selected && profile ? <><span>{profile.engine_name} · {routeName ?? '未绑定 ModelRoute'} · {capabilityNames.join(' · ') || '无已登记能力'}</span><span className="capability-list"><span className="capability">{profile.engine_name}</span>{(capabilityNames.length ? capabilityNames : ['未登记能力']).map((capability) => <span className="capability" key={capability}>{capability}</span>)}</span></> : <span>{!selected ? '选中后读取固定 EngineProvider 和 ModelRoute' : configPending ? '正在读取固定运行配置…' : configError ? '读取固定运行配置失败，请重试。' : '未找到该 Deployment 固定版本的已发布 EngineProvider 配置。'}</span>}</span><input type="checkbox" aria-label={`选择${employee?.name ?? deployment.employee_id}`} checked={selected} onChange={(event) => {
              const next = event.target.checked ? [...newTeamSelectedDeployments, deployment.id] : newTeamSelectedDeployments.filter((id) => id !== deployment.id)
              setNewTeamSelectedDeployments(next)
              if (!next.includes(newTeamRouterDeployment)) setNewTeamRouterDeployment('')
              if (!next.includes(newTeamFinalizerDeployment)) setNewTeamFinalizerDeployment('')
            }} /></label>
          })}{!deployments.length && <div className="team-prototype-empty">当前租户没有 ACTIVE 数字员工部署，请先发布员工并创建部署。</div>}</div></section>
          <div className="config-note">发布前会校验成员状态、EngineProvider 能力、ModelRoute 协议、并发额度和租户权限。</div>
          <div className="dialog-actions"><button className="close" type="button" onClick={() => setNewTeamOpen(false)}>取消</button><button className="save" type="button" disabled={!newTeamName.trim() || !newTeamProfilesReady || !newTeamRouterDeployment || !newTeamFinalizerDeployment || newTeamRouterDeployment === newTeamFinalizerDeployment} onClick={() => void saveTeamDraft()}>保存团队草稿</button></div>
        </div>
      </dialog>}

      {dialog === 'config' && team && <dialog className="team-prototype-modal team-prototype-config-modal" aria-label="团队配置">
        <div className="dialog-head"><div><h2>团队配置 · {team.name}</h2><p>配置决定任务如何拆分、如何分配，以及成员分别进入哪个 Engine Worker Pool。</p></div><button className="dialog-close" type="button" onClick={() => setDialog(null)} aria-label="关闭团队配置">×</button></div>
        <div className="dialog-body">
          <div className="dialog-context"><span>Team Version v{team.version}</span><span>当前 Conversation 已固定</span><span>{canManage ? '管理员可编辑草稿' : '普通成员只读'}</span></div>
          {team.status === 'PUBLISHED' && canManage && <div className="team-prototype-state"><span>已发布版本不能原地修改。</span><button type="button" onClick={() => void createRevision()}>创建下一版草稿</button></div>}
          <div className="config-grid">
            <div className="config-field"><label htmlFor="orchestration-mode">编排模式</label><select id="orchestration-mode" value={orchestrationMode} disabled={!canManage || team.status !== 'DRAFT'} onChange={(event) => setOrchestrationMode(event.target.value as 'USER' | 'ROUTER' | 'HYBRID')}><option value="HYBRID">HYBRID · 路由提案 + 用户确认</option><option value="ROUTER">ROUTER · 自动执行</option><option value="USER">USER · 手动分配</option></select></div>
            <div className="config-field"><label htmlFor="approval-mode">计划审批</label><select id="approval-mode" value={approvalMode} disabled={!canManage || team.status !== 'DRAFT'} onChange={(event) => setApprovalMode(event.target.value as 'CONFIRM' | 'AUTO' | 'MANUAL')}><option value="CONFIRM">CONFIRM · 用户确认后执行</option><option value="AUTO">AUTO · 验证后自动执行</option><option value="MANUAL">MANUAL · 手动调度</option></select></div>
            <div className="config-field"><label htmlFor="router-member">路由 Agent</label><select id="router-member" aria-label="路由 Agent" value={routerMemberId} disabled={!canManage || team.status !== 'DRAFT'} onChange={(event) => setRouterMemberId(event.target.value)}><option value="">选择 Router Agent</option>{configMembers.map((member) => <option key={member.member_id} value={member.member_id}>{employees.find((employee) => employee.id === member.employee_id)?.name ?? member.employee_id} · {member.engine_name}</option>)}</select></div>
            <div className="config-field"><label htmlFor="finalizer-member">汇总 Agent</label><select id="finalizer-member" aria-label="汇总 Agent" value={finalizerMemberId} disabled={!canManage || team.status !== 'DRAFT'} onChange={(event) => setFinalizerMemberId(event.target.value)}><option value="">选择 Finalizer</option>{configMembers.map((member) => <option key={member.member_id} value={member.member_id}>{employees.find((employee) => employee.id === member.employee_id)?.name ?? member.employee_id} · {member.engine_name}</option>)}</select></div>
            <div className="config-field"><label htmlFor="max-concurrency">最大并发任务</label><input id="max-concurrency" aria-label="最大并发任务" type="number" min={1} max={100} value={maxConcurrency} disabled={!canManage || team.status !== 'DRAFT'} onChange={(event) => setMaxConcurrency(Math.max(1, Number(event.target.value)))} /></div>
            <div className="config-field"><label htmlFor="delivery-policy">产出物交付</label><select id="delivery-policy" aria-label="产出物交付" value={deliveryPolicy} disabled={!canManage || team.status !== 'DRAFT'} onChange={(event) => setDeliveryPolicy(event.target.value as 'CONFIRM' | 'ATTACHMENTS' | 'MANUAL')}><option value="CONFIRM">CONFIRM · 用户确认后交付</option><option value="ATTACHMENTS">ATTACHMENTS · 自动附带</option><option value="MANUAL">MANUAL · 手动选择</option></select></div>
          </div>
          <section className="config-card"><div className="config-card-heading"><strong>团队成员和能力</strong><span>固定到 Team Version v{team.version}</span></div><div className="team-prototype-config-members">{configMembers.map((member) => {
            const employee = employees.find((item) => item.id === member.employee_id)
            const avatarClass = member.engine_name.toLowerCase().includes('codex') ? 'codex' : 'deepseek'
            return <article className="config-member-row" key={member.member_id}><span className={`config-member-avatar ${avatarClass}`}>{(employee?.name ?? member.employee_id).slice(0, 1)}</span><span className="config-member-info"><strong>{employee?.name ?? member.employee_id} · {member.engine_name}</strong><span>{member.role} · ModelRoute {member.model_route_id ?? '使用部署默认'}</span><span className="capability-list">{(member.capabilities.length ? member.capabilities : ['无已登记能力']).map((capability) => <span className="capability" key={capability}>{capability}</span>)}</span><span>数字员工 {employee ? statusLabel(employee.status) : '目录未返回'} · Deployment {member.deployment_id} · 员工版本 v{member.employee_version} · 并发 {member.max_concurrency}</span></span><span className="team-prototype-member-actions"><span className="pool-status"><i></i>{member.status === 'ACTIVE' ? '可用' : statusLabel(member.status)}</span>{canManage && team.status === 'DRAFT' && <button type="button" onClick={() => void removeMember(member)}>移除</button>}</span></article>
          })}{!configMembers.length && <div className="team-prototype-empty">当前团队草稿还没有成员。</div>}</div></section>
          {canManage && team.status === 'DRAFT' && <section className="config-card"><div className="config-card-heading"><strong>添加数字员工部署</strong><span>员工版本和运行配置从已发布 Deployment 读取</span></div>
            {deploymentsQuery.isLoading || selectedProfileVersionsQuery.isLoading || employeesQuery.isLoading ? <div className="team-prototype-loading">正在读取租户员工、部署和引擎配置…</div> : (deploymentsQuery.isError || selectedProfileVersionsQuery.isError || employeesQuery.isError || enginesQuery.isError) ? <div className="team-prototype-state error"><span>员工部署或引擎配置读取失败。</span><button type="button" onClick={() => { void deploymentsQuery.refetch(); void selectedProfileVersionsQuery.refetch(); void employeesQuery.refetch(); void enginesQuery.refetch() }}>重试</button></div> : <div className="config-grid">
              <div className="config-field"><label htmlFor="employee-deployment">员工部署</label><select id="employee-deployment" aria-label="员工部署" value={selectedDeployment?.id ?? ''} onChange={(event) => setSelectedDeploymentId(event.target.value)}><option value="">选择 ACTIVE 部署</option>{deployments.map((deployment) => <option key={deployment.id} value={deployment.id}>{employees.find((employee) => employee.id === deployment.employee_id)?.name ?? deployment.employee_id} · v{deployment.version} · {deployment.id}</option>)}</select></div>
              <div className="config-field"><label htmlFor="team-role">团队角色</label><select id="team-role" aria-label="团队角色" value={selectedRole} onChange={(event) => setSelectedRole(event.target.value as 'MEMBER' | 'ROUTER' | 'FINALIZER')}><option value="MEMBER">MEMBER · 执行成员</option><option value="ROUTER">ROUTER · 任务拆分</option><option value="FINALIZER">FINALIZER · 结果汇总</option></select></div>
              <div className="config-field"><label htmlFor="member-concurrency">最大并发</label><input id="member-concurrency" aria-label="最大并发" type="number" min={1} max={100} value={memberConcurrency} onChange={(event) => setMemberConcurrency(Math.max(1, Number(event.target.value)))} /></div>
              <div className="config-field"><label htmlFor="member-engine">执行引擎</label><select id="member-engine" aria-label="执行引擎" value={selectedProfile?.id ?? ''} disabled><option value="">{selectedProfile ? `${selectedProfile.engine_name} · Profile v${selectedProfile.version}` : '部署尚未配置可用引擎'}</option></select></div>
              <div className="config-field"><label htmlFor="member-route">ModelRoute</label><select id="member-route" aria-label="ModelRoute" value={selectedRouteId ?? ''} disabled><option value="">{routesQuery.data?.items.find((route) => route.route_id === selectedRouteId)?.alias ?? '无 ModelRoute'}</option></select></div>
              <fieldset className="team-prototype-capability-field"><legend>能力快照</legend>{Object.entries(selectedEngine?.capabilities ?? {}).filter(([, enabled]) => enabled).map(([capability]) => <label key={capability}><input type="checkbox" checked={memberCapabilities.includes(capability)} onChange={(event) => setMemberCapabilities((current) => event.target.checked ? [...current, capability] : current.filter((item) => item !== capability))} />{capability}</label>)}</fieldset>
            </div>}
            {profileNeedsConfiguration && selectedDeployment && <div className="team-prototype-state error">所选部署没有可用的已发布 EngineProvider 配置，请先在该数字员工的运行配置中选择并发布执行配置。</div>}
            <div className="dialog-actions"><button className="secondary" type="button" disabled={!selectedProfile || !selectedDeployment} onClick={() => void addMember()}>添加成员</button><button className="save" type="button" disabled={!team.members.length} onClick={() => void savePolicy()}>保存团队策略</button></div>
          </section>}
          <section className="config-card"><div className="config-card-heading"><strong>Worker Pool 健康</strong><span>最近心跳 · 30 秒内视为在线</span></div><div className="pool-grid">{poolItems.map((pool) => <article className="pool-card" key={pool.engine_name}><strong>{pool.engine_name}</strong><span>队列 {pool.queue_depth} · 运行中 {pool.active_workers} · Worker {pool.worker_count ?? pool.active_workers}/{pool.max_concurrency}</span><span className="pool-status"><i></i>{pool.healthy ? '可用' : '不可用'}</span></article>)}{!poolItems.length && <div className="team-prototype-empty">当前团队尚未引用 Engine Worker Pool。</div>}</div></section>
          <div className="config-note">成员的 EngineProvider、ModelRoute 和能力快照在 Conversation 创建时固定。修改团队配置不会改变当前 Conversation 的运行环境。</div>
          <div className="dialog-actions"><button className="close" type="button" onClick={() => setDialog(null)}>关闭</button>{canManage && team.status === 'DRAFT' && <button className="save" type="button" disabled={!team.router_member_id || !team.finalizer_member_id} onClick={() => void publishCurrentTeam()}>发布团队版本</button>}</div>
        </div>
      </dialog>}

      {dialog === 'plan' && <dialog className="team-prototype-modal" aria-label="任务计划">
        <div className="dialog-head"><div><h2>任务计划 · 计划 v{planRevision}</h2><p>Router Agent 已生成并通过后端校验的任务 DAG；确认后 Scheduler 才会执行 READY 节点。</p></div></div><div className="team-prototype-dialog-context"><span>团队：{team?.name ?? '—'}</span><span>最大并发：{team?.max_concurrency ?? 3}</span><span>路由：{members.find((member) => member.member_id === team?.router_member_id)?.engine_name ?? '—'}</span><span>交付：{team?.delivery_policy ?? 'CONFIRM'}</span></div>
        <div className="team-prototype-plan-list">{orchestrationTasks.map((task, index) => <article key={task.task_id}><span>{index + 1}</span><div><strong>{task.title}</strong><small>{task.depends_on?.length ? `依赖 ${task.depends_on.join('、')}` : '无依赖'} · {statusLabel(task.status)} · {task.required_capabilities?.join('、') || '无额外能力'}</small></div><em>{task.engine_name ?? '自动分配'}</em></article>)}{!orchestrationTasks.length && <div className="team-prototype-empty">当前没有可显示的任务计划。</div>}</div>
        {status === 'AWAITING_PLAN_APPROVAL' && <div className="team-prototype-dialog-actions"><button type="button" onClick={() => setDialog(null)}>稍后确认</button><button type="button" disabled={!canOperate} onClick={() => void confirmPlan()}>确认计划并继续</button></div>}
        {orchestrationId && canManage && !['SUCCEEDED', 'FAILED', 'PARTIAL_FAILED', 'CANCELLED'].includes(status ?? '') && <div className="team-prototype-dialog-actions"><button type="button" onClick={() => void changeOrchestration('cancel')}>取消编排</button></div>}
        {status !== 'AWAITING_PLAN_APPROVAL' && <footer><button type="button" onClick={() => setDialog(null)}>关闭</button></footer>}
      </dialog>}

      {dialog === 'task' && <dialog className="team-prototype-modal" aria-label={`任务详情${selectedTask ? ` · ${selectedTask.title}` : ''}`}>
        {selectedTask ? <><h2>任务详情 · {selectedTask.title}</h2><div className="team-prototype-detail-grid"><span>任务状态<strong>{statusLabel(selectedTask.status)}</strong></span><span>负责人<strong>{members.find((member) => member.member_id === selectedTask.assigned_member_id)?.employee_id ?? '未分配'}</strong></span><span>执行引擎<strong>{selectedTask.engine_name ?? '等待分配'}</strong></span><span>Worker Pool<strong>{selectedTask.latest_attempt?.worker_pool ?? '尚未领取'}</strong></span><span>依赖关系<strong>{selectedTask.depends_on?.length ? selectedTask.depends_on.join('、') : '无'}</strong></span><span>Attempt / Trace<strong>{selectedTask.latest_attempt ? `${selectedTask.latest_attempt.attempt_no} · ${selectedTask.latest_attempt.trace_id ?? selectedTask.latest_attempt.run_id ?? '等待 Run'}` : '尚未创建'}</strong></span></div>
        <div className="team-prototype-details-box"><strong>{selectedTask.error_code ? `错误：${selectedTask.error_code}` : `当前阶段：${statusLabel(selectedTask.status)}`}</strong><p>要求能力：{selectedTask.required_capabilities?.join(' · ') || '无'}；分配来源：{selectedTask.assignment_source ?? '未分配'}。</p></div>{canOperate && ['READY', 'ASSIGNED'].includes(selectedTask.status) && <div className="team-prototype-task-assignment"><label>分配成员<select aria-label="分配成员" value={assignmentMemberId} onChange={(event) => setAssignmentMemberId(event.target.value)}>{members.filter((member) => member.status === 'ACTIVE').map((member) => <option key={member.member_id} value={member.member_id}>{employees.find((employee) => employee.id === member.employee_id)?.name ?? member.employee_id} · {member.engine_name}</option>)}</select></label><button type="button" disabled={!assignmentMemberId} onClick={() => void assignTask(selectedTask)}>分配成员</button></div>}<div className="team-prototype-dialog-actions">{canManage && selectedTask.status === 'FAILED' && <button type="button" onClick={() => void changeTask(selectedTask, 'retry')}>重试任务</button>}{canManage && !terminalTaskStatuses.has(selectedTask.status) && <button type="button" onClick={() => void changeTask(selectedTask, 'cancel')}>取消任务</button>}</div></> : <div className="team-prototype-state">正在读取当前任务详情…</div>}<footer><button type="button" onClick={() => setDialog(null)}>关闭</button></footer>
      </dialog>}

      {dialog === 'event' && <dialog className="team-prototype-modal" aria-label="最近事件">
        <h2>最近事件{selectedTask ? ` · ${selectedTask.title}` : ''}</h2><p>事件按服务端 sequence 顺序读取。</p>
        <div className="team-prototype-dialog-context"><span>Task {selectedTask?.client_key ?? orchestrationId ?? '—'} · {selectedTask?.engine_name ?? 'EngineProvider —'}</span><span>Trace {selectedTask?.latest_attempt?.trace_id ?? selectedTask?.latest_attempt?.run_id ?? '—'}</span><span>租户范围：当前租户</span></div>{eventsQuery.isLoading && <div className="team-prototype-loading">正在读取最近事件…</div>}{eventsQuery.isError && <div className="team-prototype-state error"><span>最近事件读取失败。</span><button type="button" onClick={() => void eventsQuery.refetch()}>重试</button></div>}<div className="team-prototype-event-log">{events.filter((event) => !selectedTaskId || event.task_id === selectedTaskId || event.task_id === null).slice(-30).map((event) => <article key={event.event_id}><time>{formatTime(event.created_at)}</time><div><strong>{eventTitle(event)}</strong><span>{JSON.stringify(event.payload)}</span></div><code>seq {event.sequence}</code></article>)}{events.length === 0 && !eventsQuery.isLoading && <div className="team-prototype-empty">当前没有最近事件。</div>}</div><footer><button type="button" onClick={() => setDialog(null)}>关闭</button><button type="button" disabled={!selectedTask?.latest_attempt?.trace_id && !selectedTask?.latest_attempt?.run_id} onClick={() => void copySelectedTrace()}>复制 Trace 标识</button></footer>
      </dialog>}

      {dialog === 'preview' && selectedArtifact && <dialog className="team-prototype-modal" aria-label={`产出物预览 · ${selectedArtifact.filename}`}>
        <h2>产出物预览 · {previewQuery.data?.filename ?? selectedArtifact.filename}</h2><p>来源任务 {selectedArtifact.task_id ?? '—'} · EngineProvider {selectedArtifact.source_engine ?? '—'} · SHA-256 {selectedArtifact.content_sha256}</p>
        {previewQuery.isLoading && <div className="team-prototype-loading">正在读取受租户保护的产物正文…</div>}{previewQuery.isError && <div className="team-prototype-state error">产物预览失败，请重试或下载文件。</div>}
        {previewQuery.data && <pre className="team-prototype-artifact-content">{previewQuery.data.content}</pre>}<footer><button type="button" onClick={() => setDialog(null)}>关闭</button><button type="button" onClick={() => void downloadSelectedArtifact(selectedArtifact)}>下载</button><button type="button" onClick={addPreviewToDelivery}>加入交付包</button></footer>
      </dialog>}

      {dialog === 'delivery' && <dialog className="team-prototype-modal" aria-label="交付产出物">
        <div className="dialog-head"><div><h2>交付产出物</h2><p>确认后将选中的 READY 产物打包并交付到当前租户的 Web 工作台。</p></div></div><div className="team-prototype-dialog-context"><span>交付范围：当前租户</span><span>渠道：Web 工作台</span><span>需要确认：是</span></div>
        <div className="team-prototype-delivery-list">{artifacts.map((artifact) => <label key={artifact.artifact_id}><input type="checkbox" disabled={artifact.status !== 'READY'} checked={deliverySelection.includes(artifact.artifact_id)} onChange={(event) => setDeliverySelection((current) => event.target.checked ? [...current, artifact.artifact_id] : current.filter((id) => id !== artifact.artifact_id))} /><span><strong>{artifact.filename}</strong><small>{statusLabel(artifact.status)} · {(artifact.size_bytes / 1024).toFixed(1)} KB</small></span><em>{artifact.status === 'READY' ? '将交付' : '暂缓'}</em></label>)}{!artifacts.length && <div className="team-prototype-empty">当前没有可交付产物。</div>}</div>
        <footer><button type="button" onClick={() => setDialog(null)}>取消</button><button type="button" disabled={!deliverySelection.length || !canManage || deliveryPending} onClick={() => void deliverSelectedArtifacts()}>{deliveryPending ? '正在打包并交付…' : `确认交付 ${deliverySelection.length} 个文件`}</button></footer>
      </dialog>}
    </section>
  )
}
