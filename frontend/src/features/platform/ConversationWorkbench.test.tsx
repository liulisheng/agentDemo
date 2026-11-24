import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import type { ComponentType } from 'react'

import { ChatWorkspace } from './PlatformPages'

type Status = 'QUEUED' | 'RUNNING' | 'AWAITING_APPROVAL' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'UNKNOWN'

type WorkbenchProps = {
  conversations?: Array<{
    id: string
    title: string
    preview: string
    updatedAt: string
    runStatus?: Status
    pendingApprovalCount?: number
  }>
  selectedConversationId?: string | null
  timeline?: Array<{
    id: string
    role: 'user' | 'assistant' | 'tool' | 'run'
    text: string
    label?: string
    toolName?: string
    status?: string
    createdAt?: string
    internal?: boolean
    detail?: string
  }>
  snapshot?: {
    employeeName: string
    employeeVersion: number
    sessionId: string
    skills: string[]
    allowedTools: string[]
    runtimeOptions?: {
      reasoning_mode?: 'OFF' | 'AUTO' | 'DEEP'
      reasoning_effort?: 'AUTO' | 'LOW' | 'MEDIUM' | 'HIGH'
      reasoning_budget_tokens?: number | null
      response_verbosity?: 'CONCISE' | 'STANDARD' | 'DETAILED'
      max_tool_steps?: number | null
    }
  }
  approval?: { toolName: string; preview: string; previewSha256: string; expiresAt: string; state?: string }
  loading?: boolean
  error?: string | null
  hasMore?: boolean
  submitting?: boolean
  onSelectConversation?: (id: string) => void
  onLoadMore?: () => void
  onNewConversation?: () => void
  onSubmit?: (text: string) => void
  onApprove?: (previewSha256: string) => void
  onReject?: () => void
}

const Workbench = ChatWorkspace as unknown as ComponentType<WorkbenchProps>

const conversation = {
  id: 'conv-1',
  title: 'ODS 到 DWD 工作流',
  preview: '查询项目与数据源，再生成离线预览。',
  updatedAt: '2026-11-19T08:30:00Z',
  runStatus: 'AWAITING_APPROVAL' as const,
  pendingApprovalCount: 1,
}

const snapshot = {
  employeeName: '海豚调度数仓开发',
  employeeVersion: 3,
  sessionId: 'session-1',
  skills: ['数仓建模', 'DolphinScheduler 工作流'],
  allowedTools: ['projects_list', 'workflow_preview'],
}

test('workbench selects conversations and requests the next stable page', async () => {
  const user = userEvent.setup()
  const onSelectConversation = vi.fn()
  const onLoadMore = vi.fn()

  render(
    <Workbench
      conversations={[conversation]}
      selectedConversationId="conv-1"
      timeline={[
        { id: 'msg-1', role: 'user', text: '生成 ODS 到 DWD 工作流', createdAt: '2026-11-19T08:12:00Z' },
        { id: 'tool-1', role: 'tool', toolName: 'projects_list', text: '读取到 3 个项目。', status: '已完成' },
        { id: 'run-1', role: 'run', text: 'Run #3 开始执行。', status: 'RUNNING' },
      ]}
      snapshot={snapshot}
      hasMore
      onSelectConversation={onSelectConversation}
      onLoadMore={onLoadMore}
    />,
  )

  expect(screen.getByRole('navigation', { name: '对话列表' })).toBeInTheDocument()
  expect(screen.getByRole('main', { name: '对话与运行时间线' })).toBeInTheDocument()
  expect(screen.getByRole('complementary', { name: '本次 Run 快照' })).toBeInTheDocument()
  expect(screen.getAllByText('ODS 到 DWD 工作流').length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('版本 3')).toBeInTheDocument()
  expect(screen.getByText('工具 · projects_list')).toBeInTheDocument()
  expect(screen.getByText('Run 状态')).toBeInTheDocument()
  expect(document.querySelector('time[datetime="2026-11-19T08:12:00Z"]')).toBeInTheDocument()
  expect(screen.getByText(/2026\/11\/19 16:12:00/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /ODS 到 DWD 工作流/ }))
  await user.click(screen.getByRole('button', { name: '加载更多对话' }))
  expect(onSelectConversation).toHaveBeenCalledWith('conv-1')
  expect(onLoadMore).toHaveBeenCalledOnce()
})

test('workbench presents purposeful empty, loading, and failure states', () => {
  const { rerender } = render(<Workbench conversations={[]} />)
  expect(screen.getByText('还没有工作对话')).toBeInTheDocument()
  expect(screen.getByText('选择一名员工并提交任务，工作过程会保存在这里。')).toBeInTheDocument()

  rerender(<Workbench conversations={[]} loading />)
  expect(screen.getByRole('status', { name: '正在加载对话' })).toBeInTheDocument()

  rerender(<Workbench conversations={[]} error="列表暂时不可用" />)
  expect(screen.getByRole('alert')).toHaveTextContent('列表暂时不可用')
})

test('collapses internal runtime events and explains them in Chinese on demand', async () => {
  const user = userEvent.setup()
  render(
    <Workbench
      conversations={[{ ...conversation, runStatus: 'SUCCEEDED', pendingApprovalCount: 0 }]}
      selectedConversationId="conv-1"
      timeline={[
        { id: 'message-1', role: 'user', text: '帮我列出有哪些工作流' },
        {
          id: 'tool-1', role: 'tool', label: '工具调用', toolName: 'projects_list',
          text: '已读取 DolphinScheduler 项目列表。', status: '成功',
        },
        {
          id: 'internal-1', role: 'run', text: '会话已创建或恢复。',
          label: '会话准备', status: '已启动', internal: true,
        },
        {
          id: 'internal-2', role: 'run', text: 'Harness 正在整理模型请求上下文。',
          label: '模型请求准备', detail: 'request/context', status: '已完成', internal: true,
        },
        { id: 'answer-1', role: 'assistant', text: '项目 AgentDemo Acceptance 有 1 个工作流。' },
      ]}
    />,
  )

  expect(screen.getByText('帮我列出有哪些工作流')).toBeInTheDocument()
  expect(screen.getByText('已读取 DolphinScheduler 项目列表。')).toBeInTheDocument()
  expect(screen.getByText('项目 AgentDemo Acceptance 有 1 个工作流。')).toBeInTheDocument()
  const details = screen.getByText('执行详情（2 条内部事件）')
  expect(screen.getByText('会话已创建或恢复。')).not.toBeVisible()

  await user.click(details)

  expect(screen.getByText('会话已创建或恢复。')).toBeInTheDocument()
  expect(screen.getByText('Harness 正在整理模型请求上下文。')).toBeInTheDocument()
  expect(screen.getByText('request/context')).toBeInTheDocument()
})

test('approval preview exposes explicit actions and UNKNOWN never offers retry', async () => {
  const user = userEvent.setup()
  const onApprove = vi.fn()
  const onReject = vi.fn()

  const { rerender } = render(
    <Workbench
      conversations={[conversation]}
      selectedConversationId="conv-1"
      snapshot={snapshot}
      approval={{
        toolName: 'workflow_create',
        preview: '将创建 2 节点 OFFLINE 工作流。',
        previewSha256: 'a'.repeat(64),
        expiresAt: '2026-11-19T09:00:00Z',
      }}
      onApprove={onApprove}
      onReject={onReject}
    />,
  )

  expect(screen.getAllByText('等待你确认')).toHaveLength(2)
  expect(screen.getByText('将创建 2 节点 OFFLINE 工作流。')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '确认此预览' }))
  await user.click(screen.getByRole('button', { name: '拒绝此预览' }))
  expect(onApprove).toHaveBeenCalledWith('a'.repeat(64))
  expect(onReject).toHaveBeenCalledOnce()

  rerender(
    <Workbench
      conversations={[{ ...conversation, runStatus: 'UNKNOWN', pendingApprovalCount: 0 }]}
      selectedConversationId="conv-1"
      snapshot={snapshot}
    />,
  )
  expect(screen.getAllByText('结果待对账').length).toBeGreaterThanOrEqual(2)
  expect(screen.queryByRole('button', { name: /重试/ })).not.toBeInTheDocument()
})

test('shows the answer strategy in the Run snapshot and thinking phase in the timeline', () => {
  render(
    <Workbench
      conversations={[{ ...conversation, runStatus: 'RUNNING', pendingApprovalCount: 0 }]}
      selectedConversationId="conv-1"
      snapshot={{
        ...snapshot,
        runtimeOptions: {
          reasoning_mode: 'DEEP',
          reasoning_effort: 'HIGH',
          response_verbosity: 'STANDARD',
          reasoning_budget_tokens: 8192,
          max_tool_steps: 20,
        },
      }}
      timeline={[{
        id: 'thinking-1',
        role: 'run',
        label: '思考中',
        text: '正在整理任务约束。原始思考链不会展示。',
        status: '进行中',
      }]}
    />,
  )

  expect(screen.getByText('深度思考 · 强度高 · 标准答复')).toBeInTheDocument()
  expect(screen.getByText('推理预算：8,192 token · 最大工具步骤：20')).toBeInTheDocument()
  expect(screen.getByText('思考中')).toBeInTheDocument()
  expect(screen.getByText('正在整理任务约束。原始思考链不会展示。')).toBeInTheDocument()
})

test('narrow viewport keeps all three workbench regions available', () => {
  const previousWidth = window.innerWidth
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  window.dispatchEvent(new Event('resize'))
  try {
    render(
      <Workbench
        conversations={[conversation]}
        selectedConversationId="conv-1"
        timeline={[]}
        snapshot={snapshot}
      />,
    )
    expect(screen.getByRole('navigation', { name: '对话列表' })).toBeInTheDocument()
    expect(screen.getByRole('main', { name: '对话与运行时间线' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: '本次 Run 快照' })).toBeInTheDocument()
  } finally {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: previousWidth })
    window.dispatchEvent(new Event('resize'))
  }
})

test('keeps a failed submission draft so the user can correct or retry it', async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn().mockRejectedValue(new Error('暂时无法提交'))
  render(
    <Workbench
      conversations={[conversation]}
      selectedConversationId="conv-1"
      onSubmit={onSubmit}
    />,
  )

  const input = screen.getByLabelText('提交任务')
  await user.type(input, '生成订单明细表')
  await user.click(screen.getByRole('button', { name: '提交任务' }))

  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('生成订单明细表'))
  expect(input).toHaveValue('生成订单明细表')
})

test('shows an approved preview as waiting for execution and disables further decisions', () => {
  render(
    <Workbench
      conversations={[conversation]}
      selectedConversationId="conv-1"
      approval={{
        toolName: 'workflow_create',
        preview: '将创建 2 节点 OFFLINE 工作流。',
        previewSha256: 'a'.repeat(64),
        expiresAt: '2026-11-19T09:00:00Z',
        state: 'APPROVED',
      }}
      onApprove={() => undefined}
      onReject={() => undefined}
    />,
  )

  expect(screen.getByText('已确认，等待 Worker 执行')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认此预览' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '拒绝此预览' })).toBeDisabled()
})
