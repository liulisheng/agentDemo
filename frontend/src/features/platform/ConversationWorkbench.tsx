import { useMemo, useState, type FormEvent } from 'react'

export type WorkbenchRunStatus =
  | 'QUEUED'
  | 'STARTING'
  | 'RUNNING'
  | 'AWAITING_APPROVAL'
  | 'CANCELLING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'UNKNOWN'

export interface WorkbenchConversation {
  id: string
  title: string
  preview: string
  updatedAt: string
  runStatus?: WorkbenchRunStatus
  pendingApprovalCount?: number
}

export interface WorkbenchTimelineEvent {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'run'
  text: string
  label?: string
  toolName?: string
  status?: string
  createdAt?: string
  internal?: boolean
  detail?: string
}

export interface WorkbenchSnapshot {
  employeeName: string
  employeeVersion: number
  sessionId: string
  skills: string[]
  allowedTools: string[]
  engineName?: string
  executionProfileVersion?: number
  modelRouteAlias?: string
  modelName?: string
  runtimeConfigVersion?: number | null
  runtimeProfileSource?: string | null
  runtimeModelRouteSource?: string | null
  runtimeOptions?: {
    reasoning_mode?: 'OFF' | 'AUTO' | 'DEEP'
    reasoning_effort?: 'AUTO' | 'LOW' | 'MEDIUM' | 'HIGH'
    reasoning_budget_tokens?: number | null
    response_verbosity?: 'CONCISE' | 'STANDARD' | 'DETAILED'
    max_output_tokens?: number | null
    max_tool_steps?: number | null
    timeout_seconds?: number | null
  }
}

export interface WorkbenchApprovalPreview {
  toolName: string
  preview: string
  previewSha256: string
  expiresAt: string
  state?: string
}

export interface ConversationWorkbenchProps {
  employeeName?: string
  employeeOptions?: Array<{ id: string; name: string }>
  selectedEmployeeId?: string
  deploymentStatus?: 'ACTIVE' | 'DISABLED' | 'PENDING_CONFIGURATION'
  runtimeConfigVersion?: number | null
  runtimeConfigNotice?: string | null
  conversations?: WorkbenchConversation[]
  selectedConversationId?: string | null
  timeline?: WorkbenchTimelineEvent[]
  snapshot?: WorkbenchSnapshot | null
  approval?: WorkbenchApprovalPreview | null
  loading?: boolean
  error?: string | null
  hasMore?: boolean
  submitting?: boolean
  approvalBusy?: boolean
  onEmployeeChange?: (employeeId: string) => void
  onOpenRuntimeConfig?: () => void
  onSelectConversation?: (conversationId: string) => void
  onLoadMore?: () => void
  onNewConversation?: () => void
  onSubmit?: (text: string) => void | Promise<unknown>
  onApprove?: (previewSha256: string) => void
  onReject?: () => void
}

const statusLabels: Record<WorkbenchRunStatus, string> = {
  QUEUED: '排队中',
  STARTING: '准备运行',
  RUNNING: '执行中',
  AWAITING_APPROVAL: '等待你确认',
  CANCELLING: '正在取消',
  SUCCEEDED: '已完成',
  FAILED: '执行失败',
  CANCELLED: '已取消',
  UNKNOWN: '结果待对账',
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function strategyLabel(snapshot: WorkbenchSnapshot): string {
  const mode = {
    OFF: '关闭思考',
    AUTO: '自动思考',
    DEEP: '深度思考',
  }[snapshot.runtimeOptions?.reasoning_mode ?? 'AUTO']
  const effort = snapshot.runtimeOptions?.reasoning_effort
  const verbosity = {
    CONCISE: '简洁答复',
    STANDARD: '标准答复',
    DETAILED: '详细答复',
  }[snapshot.runtimeOptions?.response_verbosity ?? 'STANDARD']
  return `${mode}${effort && effort !== 'AUTO' ? ` · 强度${effort === 'HIGH' ? '高' : effort === 'MEDIUM' ? '中' : '低'}` : ''} · ${verbosity}`
}

function StatusPill({ status }: { status?: WorkbenchRunStatus }) {
  if (!status) return null
  return (
    <span className={`workbench-status workbench-status-${status.toLowerCase()}`}>
      <span aria-hidden="true" className="workbench-status-dot" />
      {statusLabels[status]}
    </span>
  )
}

export function ConversationWorkbench({
  employeeName = '选择一名员工',
  employeeOptions = [],
  selectedEmployeeId,
  deploymentStatus,
  runtimeConfigVersion,
  runtimeConfigNotice = null,
  conversations = [],
  selectedConversationId = null,
  timeline = [],
  snapshot = null,
  approval = null,
  loading = false,
  error = null,
  hasMore = false,
  submitting = false,
  approvalBusy = false,
  onEmployeeChange,
  onOpenRuntimeConfig,
  onSelectConversation,
  onLoadMore,
  onNewConversation,
  onSubmit,
  onApprove,
  onReject,
}: ConversationWorkbenchProps) {
  const [searchText, setSearchText] = useState('')
  const [draft, setDraft] = useState('')
  const selectedConversation = conversations.find(
    (conversation) => conversation.id === selectedConversationId,
  ) ?? null
  const visibleConversations = useMemo(() => {
    const query = searchText.trim().toLocaleLowerCase()
    if (!query) return conversations
    return conversations.filter((conversation) =>
      `${conversation.title} ${conversation.preview}`.toLocaleLowerCase().includes(query),
    )
  }, [conversations, searchText])
  const visibleTimeline = timeline.filter((event) => !event.internal)
  const internalTimeline = timeline.filter((event) => event.internal)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || !selectedConversation || !onSubmit || submitting) return
    try {
      const result = onSubmit(text)
      if (result && typeof (result as PromiseLike<unknown>).then === 'function') {
        // 网络提交失败时保留草稿，避免用户丢失尚未持久化的任务描述。
        void Promise.resolve(result).then(() => setDraft(''), () => undefined)
      } else {
        setDraft('')
      }
    } catch {
      // 同步失败也保留草稿；错误提示由调用方展示。
    }
  }

  return (
    <section className="employee-workbench" aria-label="员工运行工作台">
      <header className="workbench-heading">
        <div>
          <span className="workbench-kicker">员工执行 / 工作台</span>
          <h1>对话工作区</h1>
          <p>任务、工具活动与审批预览在这里连续保存。</p>
        </div>
        <div className="workbench-heading-employee">
          <span>当前员工</span>
          {employeeOptions.length > 0 && onEmployeeChange ? (
            <select
              aria-label="当前员工"
              value={selectedEmployeeId ?? ''}
              onChange={(event) => onEmployeeChange(event.target.value)}
            >
              {employeeOptions.map((employee) => (
                <option key={employee.id} value={employee.id}>{employee.name}</option>
              ))}
            </select>
          ) : (
            <strong>{employeeName}</strong>
          )}
          <div className="workbench-heading-runtime-actions">
            {deploymentStatus === 'PENDING_CONFIGURATION' ? (
              <span className="workbench-config-pending">运行环境待设置</span>
            ) : runtimeConfigVersion ? (
              <span className="workbench-config-revision">配置 v{runtimeConfigVersion}</span>
            ) : null}
            <button type="button" className="workbench-runtime-config-button" onClick={onOpenRuntimeConfig} disabled={!onOpenRuntimeConfig}>
              运行配置
            </button>
          </div>
        </div>
      </header>

      {error && <div className="workbench-page-error" role="alert">{error}</div>}
      {runtimeConfigNotice && <div className="workbench-runtime-config-notice" role="status">{runtimeConfigNotice}</div>}

      <div className="workbench-grid">
        <nav className="workbench-panel workbench-conversation-panel" aria-label="对话列表">
          <div className="workbench-panel-heading">
            <div>
              <span className="workbench-section-kicker">对话</span>
              <h2>最近工作</h2>
            </div>
            <button
              className="workbench-new-button"
              type="button"
              onClick={onNewConversation}
              disabled={!onNewConversation}
            >
              <span aria-hidden="true">＋</span> 新对话
            </button>
          </div>
          <label className="workbench-search-label" htmlFor="workbench-conversation-search">查找对话</label>
          <input
            id="workbench-conversation-search"
            className="workbench-search"
            type="search"
            placeholder="搜索标题或消息"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />

          {loading ? (
            <div className="workbench-loading" role="status" aria-label="正在加载对话">
              <span className="workbench-loading-mark" aria-hidden="true" />
              <span>正在读取对话…</span>
            </div>
          ) : visibleConversations.length === 0 ? (
            <div className="workbench-empty-list">
              <span className="workbench-empty-mark" aria-hidden="true">—</span>
              <strong>{searchText ? '没有匹配的对话' : '还没有工作对话'}</strong>
              <span>{searchText ? '试试更短的关键词。' : '选择一名员工并提交任务，工作过程会保存在这里。'}</span>
            </div>
          ) : (
            <div className="workbench-conversation-list">
              {visibleConversations.map((conversation) => {
                const selected = conversation.id === selectedConversationId
                return (
                  <button
                    key={conversation.id}
                    className={selected ? 'workbench-conversation selected' : 'workbench-conversation'}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onSelectConversation?.(conversation.id)}
                  >
                    <span className="workbench-conversation-topline">
                      <strong>{conversation.title}</strong>
                      <time dateTime={conversation.updatedAt}>{formatTime(conversation.updatedAt)}</time>
                    </span>
                    <span className="workbench-conversation-preview">{conversation.preview || '暂无消息'}</span>
                    <span className="workbench-conversation-bottomline">
                      <StatusPill status={conversation.runStatus} />
                      {(conversation.pendingApprovalCount ?? 0) > 0 && (
                        <span className="workbench-pending-count">
                          待审批 {conversation.pendingApprovalCount}
                        </span>
                      )}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
          {hasMore && (
            <button
              type="button"
              className="workbench-load-more"
              onClick={onLoadMore}
              disabled={loading || !onLoadMore}
            >
              加载更多对话
            </button>
          )}
        </nav>

        <main className="workbench-panel workbench-timeline-panel" aria-label="对话与运行时间线">
          <div className="workbench-timeline-heading">
            <div>
              <span className="workbench-section-kicker">当前任务</span>
              <h2>{selectedConversation?.title ?? '选择一个对话'}</h2>
            </div>
            <StatusPill status={selectedConversation?.runStatus} />
          </div>
          {selectedConversation ? (
            <>
              <div className="workbench-timeline" aria-live="polite">
                {timeline.length === 0 ? (
                  <div className="workbench-timeline-empty">
                    <span className="workbench-timeline-rule" aria-hidden="true" />
                    <p>从这里继续与员工协作。提交任务后，查询、预览和运行状态会按顺序出现。</p>
                  </div>
                ) : (
                  <>
                  {visibleTimeline.map((event) => (
                    <article
                      key={event.id}
                      className={`workbench-event workbench-event-${event.role}`}
                      data-event-status={event.status}
                    >
                      <span className="workbench-event-marker" aria-hidden="true" />
                      <div className="workbench-event-content">
                        <div className="workbench-event-meta">
                          <span className="workbench-event-label">
                            {event.label ?? (event.role === 'user' ? '你' : event.role === 'assistant' ? employeeName : event.role === 'tool' ? `工具 · ${event.toolName ?? '运行步骤'}` : 'Run 状态')}
                          </span>
                          {event.createdAt && (
                            <time dateTime={event.createdAt}>{formatTime(event.createdAt)}</time>
                          )}
                        </div>
                        <p>{event.text}</p>
                        {event.status && <span className="workbench-event-status">{event.status}</span>}
                      </div>
                    </article>
                  ))}
                  {internalTimeline.length > 0 && (
                    <details className="workbench-internal-details">
                      <summary>执行详情（{internalTimeline.length} 条内部事件）</summary>
                      <p className="workbench-internal-intro">
                        以下是会话、上下文和 Harness 引擎的内部步骤；重复状态不代表重复执行任务。
                      </p>
                      <div className="workbench-internal-list">
                        {internalTimeline.map((event) => (
                          <article
                            key={event.id}
                            className="workbench-internal-event"
                            data-event-status={event.status}
                          >
                            <div className="workbench-internal-meta">
                              <strong>{event.label ?? '内部执行步骤'}</strong>
                              {event.status && <span>{event.status}</span>}
                            </div>
                            <p>{event.text}</p>
                            {event.detail && <code>{event.detail}</code>}
                          </article>
                        ))}
                      </div>
                    </details>
                  )}
                  </>
                )}
                {selectedConversation.runStatus === 'UNKNOWN' && (
                  <div className="workbench-unknown-note" role="status">
                    <strong>结果待对账</strong>
                    <span>远端结果尚未确认。请先只读检查 DolphinScheduler，再由管理员完成对账。</span>
                  </div>
                )}
              </div>
              <form className="workbench-composer" onSubmit={submit}>
                <label htmlFor="workbench-message-input">提交任务</label>
                <textarea
                  id="workbench-message-input"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="描述你要完成的数仓工作…"
                  rows={3}
                  disabled={submitting}
                />
                <div className="workbench-composer-footer">
                  <span>写操作会先生成预览，确认后才会执行。</span>
                  <button type="submit" disabled={!onSubmit || submitting || !draft.trim()}>
                    {submitting ? '提交中…' : '提交任务'}
                  </button>
                </div>
              </form>
            </>
          ) : (
            <div className="workbench-select-prompt">
              <span aria-hidden="true">◌</span>
              <strong>从左侧选择一段对话</strong>
              <p>员工消息和运行时间线会在这里恢复。</p>
            </div>
          )}
        </main>

        <aside className="workbench-panel workbench-details-panel" aria-label="本次 Run 快照">
          <div className="workbench-panel-heading workbench-details-heading">
            <div>
              <span className="workbench-section-kicker">执行上下文</span>
              <h2>本次 Run 快照</h2>
            </div>
            <span className="workbench-lock-mark" aria-label="只读快照">只读</span>
          </div>
          {snapshot ? (
            <div className="workbench-snapshot">
              <div className="workbench-snapshot-employee">
                <span>数字员工</span>
                <strong>{snapshot.employeeName}</strong>
                <code>版本 {snapshot.employeeVersion}</code>
              </div>
              <div className="workbench-snapshot-row">
                <span>Session</span>
                <code>{snapshot.sessionId}</code>
              </div>
              {(snapshot.engineName || snapshot.modelName || snapshot.runtimeConfigVersion) && (
                <div className="workbench-snapshot-row workbench-snapshot-runtime">
                  <span>运行配置来源</span>
                  <strong>引擎 · {snapshot.engineName ?? '未记录'}{snapshot.executionProfileVersion ? ` · Profile v${snapshot.executionProfileVersion}` : ''}</strong>
                  <small>{snapshot.runtimeProfileSource ?? '来源未知'}{snapshot.runtimeConfigVersion ? ` · Deployment v${snapshot.runtimeConfigVersion}` : ''}</small>
                  <strong>模型 · {snapshot.modelRouteAlias ?? snapshot.modelName ?? '未记录'}</strong>
                  <small>{snapshot.runtimeModelRouteSource ?? '来源未知'}</small>
                </div>
              )}
              <div className="workbench-snapshot-row workbench-snapshot-strategy">
                <span>回答策略</span>
                <strong>{strategyLabel(snapshot)}</strong>
                <small>推理预算：{snapshot.runtimeOptions?.reasoning_budget_tokens ? `${snapshot.runtimeOptions.reasoning_budget_tokens.toLocaleString()} token` : '自动'} · 最大工具步骤：{snapshot.runtimeOptions?.max_tool_steps ?? 20}</small>
              </div>
              <div className="workbench-snapshot-section">
                <span>Skill 指令</span>
                {snapshot.skills.length ? (
                  <ul>{snapshot.skills.map((skill) => <li key={skill}>{skill}</li>)}</ul>
                ) : <p>本次未绑定 Skill。</p>}
              </div>
              <div className="workbench-snapshot-section">
                <span>可用工具</span>
                {snapshot.allowedTools.length ? (
                  <ul>{snapshot.allowedTools.map((tool) => <li key={tool}><code>{tool}</code></li>)}</ul>
                ) : <p>本次未开放工具。</p>}
              </div>
            </div>
          ) : (
            <div className="workbench-details-empty">选择对话后查看固定员工版本、Skill 和工具权限。</div>
          )}

          {approval && selectedConversation?.runStatus === 'AWAITING_APPROVAL' && (
            <section className="workbench-approval" aria-labelledby="workbench-approval-title">
              <span className="workbench-approval-kicker">
                {approval.state && approval.state.toUpperCase() !== 'PENDING'
                  ? '已确认，等待 Worker 执行'
                  : '需要你的确认'}
              </span>
              <h3 id="workbench-approval-title">{approval.toolName}</h3>
              <p className="workbench-approval-preview">{approval.preview}</p>
              <div className="workbench-approval-digest">
                <span>预览摘要</span>
                <code>{approval.previewSha256.slice(0, 16)}…</code>
              </div>
              <time dateTime={approval.expiresAt}>有效至 {formatTime(approval.expiresAt)}</time>
              <div className="workbench-approval-actions">
                <button
                  type="button"
                  className="workbench-approve-button"
                  onClick={() => onApprove?.(approval.previewSha256)}
                  disabled={!onApprove || approvalBusy || Boolean(approval.state && approval.state.toUpperCase() !== 'PENDING')}
                >
                  确认此预览
                </button>
                <button
                  type="button"
                  className="workbench-reject-button"
                  onClick={onReject}
                  disabled={!onReject || approvalBusy || Boolean(approval.state && approval.state.toUpperCase() !== 'PENDING')}
                >
                  拒绝此预览
                </button>
              </div>
            </section>
          )}
        </aside>
      </div>
    </section>
  )
}
