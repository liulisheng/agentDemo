import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  approveToolApproval,
  bindConversationAgent,
  createConversation,
  createSession,
  getDigitalEmployee,
  getRun,
  getRunTrace,
  getToolApproval,
  listConversationMessages,
  listConversations,
  listDeployments,
  rejectToolApproval,
  submitConversationTurn,
} from '../../api/client'
import type {
  ConversationApiSummary,
  ConversationMessage,
  RunDetails,
  RunTrace,
  ToolApprovalPreview,
} from '../../api/client'
import { useTenantContext } from '../management/TenantContext'
import { DeploymentRuntimeConfigurationDialog } from './DeploymentRuntimeConfigurationDialog'
import {
  ConversationWorkbench,
  type WorkbenchConversation,
  type WorkbenchRunStatus,
  type WorkbenchSnapshot,
  type WorkbenchTimelineEvent,
} from './ConversationWorkbench'

interface EmployeeOption {
  id: string
  name: string
  deploymentId: string
  deploymentStatus: 'ACTIVE' | 'DISABLED' | 'PENDING_CONFIGURATION'
  runtimeConfigVersion: number | null
}

interface WorkbenchConversationContext extends WorkbenchConversation {
  employeeId: string
  employeeName: string
  sessionId: string
  runId: string | null
}

interface PendingTurn {
  conversationId: string
  employeeId: string
  text: string
  idempotencyKey: string
  requestKey: string
}

interface CachedRunResult {
  conversationId: string
  text: string
}

type ApprovalAction =
  | { kind: 'approve'; approvalId: string; previewSha256: string }
  | { kind: 'reject'; approvalId: string }

const terminalRunStates = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN'])
const terminalApprovalStates = new Set(['REJECTED', 'EXPIRED', 'SUCCEEDED', 'FAILED', 'UNKNOWN'])

function normalizedStatus(value: string | undefined): WorkbenchRunStatus | undefined {
  const status = value?.toUpperCase() as WorkbenchRunStatus | undefined
  return status
}

function errorMessage(error: unknown): string | null {
  return error instanceof Error ? error.message : null
}

function randomIdempotencyKey(): string {
  const bytes = new Uint8Array(16)
  window.crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function loadEmployeeOptions(fakeUser: string): Promise<EmployeeOption[]> {
  const deployments = []
  let cursor: string | null = null
  do {
    const page = await listDeployments({ limit: 200, cursor }, fakeUser)
    deployments.push(...page.items.filter((item) => item.status === 'ACTIVE' || item.status === 'PENDING_CONFIGURATION'))
    cursor = page.next_cursor ?? null
  } while (cursor)

  const selectedDeployments = new Map<string, (typeof deployments)[number]>()
  for (const deployment of deployments) {
    const current = selectedDeployments.get(deployment.employee_id)
    if (
      !current
      || deployment.version > current.version
      || (deployment.version === current.version && deployment.status === 'ACTIVE')
    ) {
      selectedDeployments.set(deployment.employee_id, deployment)
    }
  }

  return Promise.all([...selectedDeployments.values()].map(async (deployment) => {
    const employee = await getDigitalEmployee(deployment.employee_id, fakeUser)
    return {
      id: deployment.employee_id,
      name: employee.name,
      deploymentId: deployment.id,
      deploymentStatus: deployment.status,
      runtimeConfigVersion: deployment.runtime_config_version,
    }
  }))
}

function mapConversation(value: ConversationApiSummary): WorkbenchConversationContext {
  return {
    id: value.conversation_id,
    title: value.title,
    preview: value.last_message?.preview ?? '',
    updatedAt: value.updated_at,
    runStatus: normalizedStatus(value.last_run?.status),
    pendingApprovalCount: value.pending_approval_count,
    employeeId: value.employee.employee_id,
    employeeName: value.employee.employee_name,
    sessionId: value.session_id,
    runId: value.last_run?.run_id ?? null,
  }
}

function mapMessage(value: ConversationMessage): WorkbenchTimelineEvent {
  const isUser = value.source.toLowerCase() === 'user'
  return {
    id: value.id,
    role: isUser ? 'user' : 'assistant',
    text: value.text,
    createdAt: value.created_at ?? undefined,
  }
}

function attributeText(attributes: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = attributes[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}

function mapTrace(trace: RunTrace | undefined): WorkbenchTimelineEvent[] {
  if (!trace) return []
  const toolNameByCallId = new Map<string, string>()
  for (const span of trace.spans) {
    if (span.attributes.event_type !== 'tool/call') continue
    const callId = attributeText(span.attributes, 'call_id')
    const toolName = attributeText(span.attributes, 'tool_name', 'remote_tool_name', 'name')
    if (callId && toolName) toolNameByCallId.set(callId, toolName)
  }

  return trace.spans.map((span) => {
    const eventType = attributeText(span.attributes, 'event_type')
    const toolCallId = attributeText(span.attributes, 'tool_call_id')
    const toolName = attributeText(span.attributes, 'tool_name', 'remote_tool_name', 'name')
      ?? (toolCallId ? toolNameByCallId.get(toolCallId) : undefined)
    const status = traceStatusLabel(span.status)
    const createdAt = attributeText(span.attributes, 'created_at')
    const phaseExplanation: Record<string, { label: string; text: string; status: string }> = {
      reasoning_started: { label: '思考中', text: '员工正在整理任务约束和执行计划。原始思考链不会展示。', status: '进行中' },
      'reasoning/start': { label: '思考中', text: '员工正在整理任务约束和执行计划。原始思考链不会展示。', status: '进行中' },
      reasoning_completed: { label: '思考完成', text: '推理阶段已完成，准备生成答复或调用工具。', status: '已完成' },
      'reasoning/end': { label: '思考完成', text: '推理阶段已完成，准备生成答复或调用工具。', status: '已完成' },
    }
    const phase = eventType ? phaseExplanation[eventType] : undefined
    if (phase) {
      return {
        id: `span-${span.id}`,
        role: 'run',
        label: phase.label,
        status: phase.status,
        text: phase.text,
        detail: eventType,
        createdAt,
      }
    }
    if (span.span_type === 'tool') {
      const toolLabel = readableToolName(toolName)
      if (eventType === 'tool/result') {
        const failed = span.attributes.is_error === true
        return {
          id: `span-${span.id}`,
          role: 'tool',
          label: '工具结果',
          toolName,
          status: failed ? '失败' : '成功',
          text: failed
            ? `${toolLabel}返回错误；请查看 Trace 和错误信息。`
            : `${toolLabel}返回结果。`,
          detail: toolName,
          createdAt,
        }
      }
      if (eventType === 'tool/call') {
        const awaitingApproval = span.status === 'awaiting_approval'
        return {
          id: `span-${span.id}`,
          role: 'tool',
          label: awaitingApproval ? '审批预览' : '工具调用',
          toolName,
          status: awaitingApproval ? '等待你确认' : status,
          text: awaitingApproval
            ? `${toolLabel}的预览已生成，等待 Run 发起人确认。`
            : `${toolLabel}。`,
          detail: toolName,
          createdAt,
        }
      }
      return {
        id: `span-${span.id}`,
        role: 'tool',
        label: '工具事件',
        toolName,
        status,
        text: eventType === 'tool_denied' ? '工具调用被运行策略拒绝。' : `${toolLabel}。`,
        detail: eventType,
        createdAt,
      }
    }

    const eventExplanation: Record<string, string> = {
      'agent/inbox/spliced': 'Harness 整理当前会话中与本轮有关的消息。',
      'turn/start': '开始处理这一轮任务。',
      'step/start': '开始一个模型或内部执行步骤。',
      'system/message': '员工指令和系统规则已装入本轮上下文。',
      'user/message': '当前用户请求已传给 Harness。',
      'request/header': '正在准备模型请求。',
      'request/context': '正在整理发送给模型的上下文。',
      'session/title': '更新 Harness 内部会话标题。',
      'assistant/message': '模型生成了一条阶段性消息。',
      'step/end': '一个内部执行步骤结束。',
      'turn/end': '本轮模型处理结束。',
    }
    const spanExplanation: Record<string, { label: string; text: string }> = {
      session: { label: '会话准备', text: '创建或恢复员工会话。' },
      run: { label: '任务生命周期', text: '记录本轮 Run 的启动与结束状态。' },
      skill: { label: 'Skill 加载', text: '选择本次 Run 固定版本的 Skill。' },
      context: { label: '上下文准备', text: '整理员工指令、Skill 和工具权限。' },
      model: { label: '模型处理', text: '模型正在分析任务或生成答复。' },
      result: { label: '结果收尾', text: '保存本轮 Run 的最终结果。' },
      runtime: { label: 'Harness 内部事件', text: 'Harness 执行过程中的内部状态。' },
    }
    const explanation = spanExplanation[span.span_type] ?? {
      label: '执行详情',
      text: '运行时内部状态。',
    }
    const rawEventText = eventType ? eventExplanation[eventType] : undefined
    return {
      id: `span-${span.id}`,
      role: 'run',
      label: explanation.label,
      status,
      text: rawEventText ?? explanation.text,
      detail: eventType,
      internal: true,
      createdAt,
    }
  })
}

function traceStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    started: '已启动',
    running: '进行中',
    selected: '已选择',
    assembled: '已准备',
    succeeded: '已完成',
    failed: '失败',
    awaiting_approval: '等待审批',
    rejected: '已拒绝',
    unknown: '结果待对账',
  }
  return labels[status.toLowerCase()] ?? status
}

function readableToolName(toolName: string | undefined): string {
  const names: Record<string, string> = {
    dolphinscheduler_projects_list: '读取 DolphinScheduler 项目列表',
    dolphinscheduler_datasources_list: '读取 DolphinScheduler 数据源列表',
    dolphinscheduler_workflows_list: '读取 DolphinScheduler 工作流列表',
    dolphinscheduler_workflow_create_multi_preview: '预览创建多节点工作流',
    dolphinscheduler_workflow_create_preview: '预览创建工作流',
    dolphinscheduler_project_create_preview: '预览创建项目',
    dolphinscheduler_datasource_create_preview: '预览创建数据源',
  }
  return (toolName && names[toolName]) || 'DolphinScheduler 工具'
}

function snapshotFromRun(
  run: RunDetails | undefined,
  conversation: WorkbenchConversationContext | null,
): WorkbenchSnapshot | null {
  if (!run || !conversation) return null
  return {
    employeeName: conversation.employeeName,
    employeeVersion: run.snapshot.employee_version,
    sessionId: conversation.sessionId,
    skills: run.snapshot.skill_ids,
    allowedTools: run.snapshot.tool_ids,
    engineName: run.snapshot.engine_name,
    executionProfileVersion: run.snapshot.execution_profile_version,
    modelRouteAlias: run.snapshot.model_route_snapshot?.route_alias,
    modelName: run.snapshot.model_route_snapshot?.model_id ?? run.snapshot.model_alias ?? undefined,
    runtimeConfigVersion: run.snapshot.runtime_config_version,
    runtimeProfileSource: run.snapshot.runtime_profile_source,
    runtimeModelRouteSource: run.snapshot.runtime_model_route_source,
    runtimeOptions: run.snapshot.runtime_options,
  }
}

function getApprovalId(trace: RunTrace | undefined): string | null {
  for (const span of trace?.spans ?? []) {
    const approvalId = span.attributes.approval_ref
    if (typeof approvalId === 'string' && approvalId) return approvalId
  }
  return null
}

/** 员工工作台数据容器：按当前租户读取对话，并通过平台 API 提交和审批。 */
export function ConversationWorkbenchPage() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(null)
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null)
  const [runIdOverrides, setRunIdOverrides] = useState<Record<string, string>>({})
  const [cachedRunResults, setCachedRunResults] = useState<Record<string, CachedRunResult>>({})
  const [optimisticConversation, setOptimisticConversation] = useState<WorkbenchConversationContext | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [runtimeConfigOpen, setRuntimeConfigOpen] = useState(false)
  const [runtimeConfigNotice, setRuntimeConfigNotice] = useState<string | null>(null)
  const previousFakeUser = useRef(fakeUser)
  const turnKeys = useRef(new Map<string, string>())
  const approvalKeys = useRef(new Map<string, string>())

  const employeesQuery = useQuery({
    queryKey: ['workbench-employees', fakeUser],
    queryFn: () => loadEmployeeOptions(fakeUser),
  })
  const employeeOptions = employeesQuery.data ?? []
  const activeEmployeeId = selectedEmployeeId ?? employeeOptions[0]?.id ?? null
  const activeEmployee = employeeOptions.find((employee) => employee.id === activeEmployeeId) ?? null

  const conversationsQuery = useInfiniteQuery({
    queryKey: ['workbench-conversations', fakeUser, activeEmployeeId],
    queryFn: ({ pageParam }) => listConversations(
      activeEmployeeId ?? '',
      { limit: 20, cursor: pageParam as string | null },
      fakeUser,
    ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: Boolean(activeEmployeeId),
  })

  const serverConversations = useMemo(
    () => conversationsQuery.data?.pages.flatMap((page) => page.items.map(mapConversation)) ?? [],
    [conversationsQuery.data],
  )
  const conversationContexts = useMemo(() => {
    const rows = [...serverConversations]
    if (
      optimisticConversation
      && optimisticConversation.employeeId === activeEmployeeId
      && !rows.some((row) => row.id === optimisticConversation.id)
    ) {
      rows.unshift(optimisticConversation)
    }
    return rows
  }, [activeEmployeeId, optimisticConversation, serverConversations])

  const selectedConversation = conversationContexts.find((item) => item.id === selectedConversationId) ?? null
  const runId = selectedConversation
    ? runIdOverrides[selectedConversation.id] ?? selectedConversation.runId
    : null

  const messagesQuery = useQuery({
    queryKey: ['workbench-messages', fakeUser, selectedConversation?.id],
    queryFn: () => listConversationMessages(selectedConversation?.id ?? '', fakeUser),
    enabled: Boolean(selectedConversation),
  })
  const runQuery = useQuery({
    queryKey: ['workbench-run', fakeUser, runId],
    queryFn: () => getRun(runId ?? '', fakeUser),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status.toUpperCase()
      return status && terminalRunStates.has(status) ? false : 2000
    },
  })
  const traceQuery = useQuery({
    queryKey: ['workbench-run-trace', fakeUser, runId],
    queryFn: () => getRunTrace(runId ?? '', fakeUser),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.run.status.toUpperCase()
      return status && terminalRunStates.has(status) ? false : 3000
    },
  })
  const approvalId = getApprovalId(traceQuery.data)
  const approvalQuery = useQuery({
    queryKey: ['workbench-approval', fakeUser, approvalId],
    queryFn: () => getToolApproval(approvalId ?? '', fakeUser),
    enabled: Boolean(approvalId) && runQuery.data?.status.toUpperCase() === 'AWAITING_APPROVAL',
    refetchInterval: (query) => {
      const state = query.state.data?.state.toUpperCase()
      return state && terminalApprovalStates.has(state) ? false : 3000
    },
  })

  useEffect(() => {
    if (previousFakeUser.current === fakeUser) return
    previousFakeUser.current = fakeUser
    setSelectedEmployeeId(null)
    setSelectedConversationId(null)
    setRunIdOverrides({})
    setCachedRunResults({})
    setOptimisticConversation(null)
    setActionError(null)
    setRuntimeConfigNotice(null)
  }, [fakeUser])

  useEffect(() => {
    if (optimisticConversation && serverConversations.some((row) => row.id === optimisticConversation.id)) {
      setOptimisticConversation(null)
    }
  }, [optimisticConversation, serverConversations])

  useEffect(() => {
    const result = runQuery.data
    if (!selectedConversation || !runId || result?.id !== runId || !result.result) return
    setCachedRunResults((current) => {
      const previous = current[result.id]
      if (previous?.conversationId === selectedConversation.id && previous.text === result.result) {
        return current
      }
      return {
        ...current,
        [result.id]: { conversationId: selectedConversation.id, text: result.result },
      }
    })
  }, [runId, runQuery.data, selectedConversation?.id])

  useEffect(() => {
    if (!selectedConversationId || !conversationContexts.some((row) => row.id === selectedConversationId)) {
      setSelectedConversationId(conversationContexts[0]?.id ?? null)
    }
  }, [conversationContexts, selectedConversationId])

  const createConversationMutation = useMutation({
    mutationFn: async () => {
      if (!activeEmployee || activeEmployee.deploymentStatus !== 'ACTIVE') throw new Error('请先为当前员工保存运行配置')
      const session = await createSession(activeEmployee.deploymentId, fakeUser)
      const conversation = await createConversation({
        title: `新对话 ${new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date())}`,
        is_group: false,
      }, fakeUser)
      await bindConversationAgent(conversation.id, {
        employee_id: activeEmployee.id,
        employee_name: activeEmployee.name,
        session_id: session.id,
      }, fakeUser)
      return {
        id: conversation.id,
        title: conversation.title,
        preview: '',
        updatedAt: new Date().toISOString(),
        pendingApprovalCount: 0,
        employeeId: activeEmployee.id,
        employeeName: activeEmployee.name,
        sessionId: session.id,
        runId: null,
      } satisfies WorkbenchConversationContext
    },
    onSuccess: async (conversation) => {
      setActionError(null)
      setRuntimeConfigNotice(null)
      setOptimisticConversation(conversation)
      setSelectedConversationId(conversation.id)
      await queryClient.invalidateQueries({ queryKey: ['workbench-conversations', fakeUser, activeEmployeeId] })
    },
    onError: (error) => setActionError(errorMessage(error) ?? '新建对话失败'),
  })

  const submitTurnMutation = useMutation({
    mutationFn: (turn: PendingTurn) => submitConversationTurn(
      turn.conversationId,
      { text: turn.text, employee_id: turn.employeeId, idempotency_key: turn.idempotencyKey },
      fakeUser,
    ),
    onSuccess: async (submission, turn) => {
      turnKeys.current.delete(turn.requestKey)
      setActionError(null)
      setRunIdOverrides((current) => ({ ...current, [turn.conversationId]: submission.run_id }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['workbench-conversations', fakeUser, activeEmployeeId] }),
        queryClient.invalidateQueries({ queryKey: ['workbench-messages', fakeUser, turn.conversationId] }),
      ])
    },
    onError: (error) => setActionError(errorMessage(error) ?? '任务提交失败；草稿仍保留在输入框中'),
  })

  const approvalMutation = useMutation({
    mutationFn: (action: ApprovalAction) => {
      if (action.kind === 'approve') {
        const key = approvalKeys.current.get(action.approvalId) ?? randomIdempotencyKey()
        approvalKeys.current.set(action.approvalId, key)
        return approveToolApproval(action.approvalId, {
          preview_sha256: action.previewSha256,
          idempotency_key: key,
        }, fakeUser)
      }
      return rejectToolApproval(action.approvalId, fakeUser)
    },
    onSuccess: async (_result, action) => {
      setActionError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['workbench-approval', fakeUser, action.approvalId] }),
        queryClient.invalidateQueries({ queryKey: ['workbench-run', fakeUser, runId] }),
        queryClient.invalidateQueries({ queryKey: ['workbench-run-trace', fakeUser, runId] }),
        queryClient.invalidateQueries({ queryKey: ['workbench-conversations', fakeUser, activeEmployeeId] }),
      ])
    },
    onError: (error) => setActionError(errorMessage(error) ?? '审批提交失败'),
  })

  const runStatus = normalizedStatus(runQuery.data?.status ?? selectedConversation?.runStatus)
  const workbenchConversations: WorkbenchConversation[] = conversationContexts.map((item) => ({
    id: item.id,
    title: item.title,
    preview: item.preview,
    updatedAt: item.updatedAt,
    runStatus: item.id === selectedConversationId ? runStatus : item.runStatus,
    pendingApprovalCount: item.pendingApprovalCount,
  }))
  const messages: WorkbenchTimelineEvent[] = (messagesQuery.data?.items ?? []).map(mapMessage)
  const traceEvents = mapTrace(traceQuery.data)
  const persistedAssistantTexts = new Set(
    messages.filter((event) => event.role === 'assistant').map((event) => event.text),
  )
  const runResultEventsById = new Map<string, WorkbenchTimelineEvent>()
  for (const [cachedRunId, cachedResult] of Object.entries(cachedRunResults)) {
    if (
      cachedResult.conversationId === selectedConversation?.id
      && cachedResult.text
      && !persistedAssistantTexts.has(cachedResult.text)
    ) {
      runResultEventsById.set(cachedRunId, {
        id: `run-result-${cachedRunId}`,
        role: 'assistant',
        text: cachedResult.text,
      })
    }
  }
  if (
    runQuery.data?.result
    && runQuery.data.id === runId
    && selectedConversation
    && !persistedAssistantTexts.has(runQuery.data.result)
  ) {
    runResultEventsById.set(runQuery.data.id, {
      id: `run-result-${runQuery.data.id}`,
      role: 'assistant',
      text: runQuery.data.result,
    })
  }
  const runResultEvents = [...runResultEventsById.values()]
  const timeline = [...messages, ...traceEvents, ...runResultEvents]
  const snapshot = snapshotFromRun(runQuery.data, selectedConversation)
  const approval = approvalQuery.data
  const authorizedApproval = approval?.requested_by === fakeUser ? approval : undefined
  const canApprove = Boolean(
    authorizedApproval
    && authorizedApproval.state.toUpperCase() === 'PENDING'
    && approvalId,
  )
  const spanForApproval = traceQuery.data?.spans.find((span) => span.attributes.approval_ref === approvalId)
  const approvalToolName = spanForApproval
    ? attributeText(spanForApproval.attributes, 'tool_name', 'remote_tool_name', 'name')
    : undefined

  async function submitTurn(text: string) {
    if (!selectedConversation || !activeEmployeeId) return
    const requestKey = `${selectedConversation.id}\u0000${text}`
    const idempotencyKey = turnKeys.current.get(requestKey) ?? randomIdempotencyKey()
    turnKeys.current.set(requestKey, idempotencyKey)
    return submitTurnMutation.mutateAsync({
      conversationId: selectedConversation.id,
      employeeId: activeEmployeeId,
      text,
      idempotencyKey,
      requestKey,
    })
  }

  const queryError = [employeesQuery.error, conversationsQuery.error, messagesQuery.error, runQuery.error, traceQuery.error]
    .map(errorMessage)
    .find(Boolean)
  const error = actionError ?? queryError ?? (
    approvalQuery.isError || (approval && approval.requested_by !== fakeUser)
      ? '审批预览不可用或当前身份无权查看。'
      : null
  )

  return (
    <>
    <ConversationWorkbench
      employeeName={activeEmployee?.name ?? '没有可运行的员工'}
      employeeOptions={employeeOptions.map(({ id, name }) => ({ id, name }))}
      selectedEmployeeId={activeEmployeeId ?? undefined}
      deploymentStatus={activeEmployee?.deploymentStatus}
      runtimeConfigVersion={activeEmployee?.runtimeConfigVersion}
      runtimeConfigNotice={runtimeConfigNotice}
      conversations={workbenchConversations}
      selectedConversationId={selectedConversationId}
      timeline={timeline}
      snapshot={snapshot}
      approval={authorizedApproval && runStatus === 'AWAITING_APPROVAL' ? {
        toolName: approvalToolName ?? authorizedApproval.tool_id,
        preview: authorizedApproval.preview,
        previewSha256: authorizedApproval.preview_sha256,
        expiresAt: authorizedApproval.expires_at,
        state: authorizedApproval.state,
      } : null}
      loading={employeesQuery.isLoading || conversationsQuery.isLoading}
      error={error}
      hasMore={Boolean(conversationsQuery.hasNextPage)}
      submitting={submitTurnMutation.isPending}
      approvalBusy={approvalMutation.isPending}
      onEmployeeChange={(employeeId) => {
        setSelectedEmployeeId(employeeId)
        setSelectedConversationId(null)
        setOptimisticConversation(null)
        setRuntimeConfigNotice(null)
      }}
      onSelectConversation={(conversationId) => {
        setSelectedConversationId(conversationId)
        setRuntimeConfigNotice(null)
      }}
      onLoadMore={() => { void conversationsQuery.fetchNextPage() }}
      onNewConversation={activeEmployee?.deploymentStatus === 'ACTIVE' ? () => createConversationMutation.mutate() : undefined}
      onOpenRuntimeConfig={activeEmployee ? () => setRuntimeConfigOpen(true) : undefined}
      onSubmit={submitTurn}
      onApprove={canApprove && approvalId ? (previewSha256) => {
        if (authorizedApproval?.preview_sha256 !== previewSha256) {
          setActionError('预览摘要已变化，请重新读取后再确认。')
          return
        }
        approvalMutation.mutate({ kind: 'approve', approvalId, previewSha256 })
      } : undefined}
      onReject={canApprove && approvalId ? () => approvalMutation.mutate({ kind: 'reject', approvalId }) : undefined}
    />
    <DeploymentRuntimeConfigurationDialog
      open={runtimeConfigOpen}
      deploymentId={activeEmployee?.deploymentId}
      employeeName={activeEmployee?.name}
      canEdit={currentIdentity.roleName === '管理员'}
      onClose={() => setRuntimeConfigOpen(false)}
      onSaved={() => {
        setRuntimeConfigOpen(false)
        if (selectedConversation) {
          setRuntimeConfigNotice('运行配置已更新；当前对话仍使用旧 Session 快照。点击“新对话”后使用新配置。')
        }
        void queryClient.invalidateQueries({ queryKey: ['workbench-employees', fakeUser] })
      }}
      onSavedAndCreate={() => {
        setRuntimeConfigOpen(false)
        createConversationMutation.mutate()
      }}
    />
    </>
  )
}
