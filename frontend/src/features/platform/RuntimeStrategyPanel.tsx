import { Alert, Tag, Typography } from 'antd'
import { useMemo } from 'react'

import type { EngineProviderSummary, RuntimeOptions } from '../../api/client'

const { Text } = Typography

export type RuntimeStrategyMode = 'INHERIT' | 'OVERRIDE'

export interface RuntimeStrategyPanelProps {
  mode?: RuntimeStrategyMode
  canEdit: boolean
  draft: RuntimeOptions
  effective: RuntimeOptions
  source?: string | null
  engineLabel?: string
  routeLabel?: string
  configVersion?: number | null
  capabilities?: EngineProviderSummary['capabilities']
  reasoningUnsupported?: boolean
  incompatible?: boolean
  onModeChange?: (mode: RuntimeStrategyMode) => void
  onChange: (value: RuntimeOptions) => void
}

const modeLabels: Record<string, string> = {
  INHERIT: '继承默认',
  AUTO: '自动思考',
  DEEP: '深度思考',
  OFF: '关闭思考',
}
const effortLabels: Record<string, string> = {
  INHERIT: '继承默认',
  AUTO: '自动',
  LOW: '低',
  MEDIUM: '中',
  HIGH: '高',
}
const verbosityLabels: Record<string, string> = {
  INHERIT: '继承默认',
  CONCISE: '简洁',
  STANDARD: '标准',
  DETAILED: '详细',
}

function option(value: string | number | null | undefined, labels: Record<string, string>, fallback = '自动') {
  if (value === null || value === undefined || value === '') return fallback
  return labels[String(value)] ?? String(value)
}

function updateValue(current: RuntimeOptions, key: keyof RuntimeOptions, value: string | number | null) {
  const next = { ...current }
  if (value === null || value === '' || value === 'INHERIT') delete next[key]
  else next[key] = value as never
  return next
}

export function RuntimeStrategyPanel({
  mode = 'OVERRIDE',
  canEdit,
  draft,
  effective,
  source,
  engineLabel = '未设置',
  routeLabel = '未设置',
  configVersion,
  capabilities,
  reasoningUnsupported = false,
  incompatible = false,
  onModeChange,
  onChange,
}: RuntimeStrategyPanelProps) {
  const diff = useMemo(() => {
    const rows: Array<{ label: string; value: string }> = []
    const currentMode = option(effective.reasoning_mode, modeLabels)
    const draftMode = option(draft.reasoning_mode, modeLabels)
    if (draftMode !== currentMode) rows.push({ label: '思考模式', value: `${currentMode} → ${draftMode}` })
    const currentEffort = option(effective.reasoning_effort, effortLabels)
    const draftEffort = option(draft.reasoning_effort, effortLabels)
    if (draftEffort !== currentEffort) rows.push({ label: '推理强度', value: `${currentEffort} → ${draftEffort}` })
    const currentVerbosity = option(effective.response_verbosity, verbosityLabels)
    const draftVerbosity = option(draft.response_verbosity, verbosityLabels)
    if (draftVerbosity !== currentVerbosity) rows.push({ label: '答复详细程度', value: `${currentVerbosity} → ${draftVerbosity}` })
    const currentBudget = effective.reasoning_budget_tokens ? `${effective.reasoning_budget_tokens / 1024}K` : '自动'
    const draftBudget = draft.reasoning_budget_tokens ? `${draft.reasoning_budget_tokens / 1024}K` : '自动'
    if (draftBudget !== currentBudget) rows.push({ label: '推理预算', value: `${currentBudget} → ${draftBudget}` })
    return rows
  }, [draft, effective])

  return (
    <div className="runtime-strategy-panel">
      <section className="runtime-strategy-editor" aria-label="回答策略设置">
        <div className="runtime-strategy-section-heading">
          <div>
            <Text strong>回答策略</Text>
            <Text type="secondary">思考阶段只显示状态，不展示原始思考链。</Text>
          </div>
          <Tag color="blue">已接入平台 API</Tag>
        </div>
        {onModeChange && (
          <div className="deployment-runtime-mode-switch" role="group" aria-label="运行配置模式">
            <button type="button" disabled={!canEdit} aria-pressed={mode === 'INHERIT'} onClick={() => onModeChange('INHERIT')}>全部继承默认</button>
            <button type="button" disabled={!canEdit} aria-pressed={mode === 'OVERRIDE'} onClick={() => onModeChange('OVERRIDE')}>设置员工覆盖</button>
          </div>
        )}
        <fieldset disabled={!canEdit} className="runtime-strategy-fieldset">
          <legend>策略设置</legend>
          <div className="runtime-strategy-grid">
            <label>思考模式<select aria-label="思考模式" value={draft.reasoning_mode ?? 'INHERIT'} onChange={(event) => onChange(updateValue(draft, 'reasoning_mode', event.target.value))}><option value="INHERIT">继承默认策略</option><option value="AUTO">自动思考</option><option value="DEEP">深度思考</option><option value="OFF">关闭思考</option></select></label>
            <label>推理强度<select aria-label="推理强度" value={draft.reasoning_effort ?? 'INHERIT'} onChange={(event) => onChange(updateValue(draft, 'reasoning_effort', event.target.value))}><option value="INHERIT">继承默认</option><option value="AUTO">自动</option><option value="LOW">低</option><option value="MEDIUM">中</option><option value="HIGH">高</option></select></label>
            <label>答复详细程度<select aria-label="答复详细程度" value={draft.response_verbosity ?? 'INHERIT'} onChange={(event) => onChange(updateValue(draft, 'response_verbosity', event.target.value))}><option value="INHERIT">继承默认</option><option value="CONCISE">简洁</option><option value="STANDARD">标准</option><option value="DETAILED">详细</option></select></label>
            <label>推理预算<select aria-label="推理预算" value={draft.reasoning_budget_tokens ?? ''} onChange={(event) => onChange(updateValue(draft, 'reasoning_budget_tokens', event.target.value ? Number(event.target.value) : null))}><option value="">自动</option><option value="8192">8K token</option><option value="16384">16K token</option></select></label>
          </div>
        </fieldset>
        <div className="runtime-strategy-capability-list" aria-label="当前引擎能力">
          <span>reasoning <b className={capabilities?.reasoning === false ? 'unavailable' : ''}>{capabilities?.reasoning === false ? '不支持' : '支持'}</b></span>
          <span>reasoning_effort <b className={capabilities?.reasoning_effort === false ? 'unavailable' : ''}>{capabilities?.reasoning_effort === false ? '不支持' : '支持'}</b></span>
          <span>reasoning_budget <b className={capabilities?.reasoning_budget === false ? 'unavailable' : ''}>{capabilities?.reasoning_budget === false ? '不支持' : '支持'}</b></span>
          <span>max_output_tokens <b className={capabilities?.max_output_tokens === false ? 'unavailable' : ''}>{capabilities?.max_output_tokens === false ? '不支持' : '支持'}</b></span>
        </div>
        {reasoningUnsupported && <Alert type="warning" showIcon message="当前组合不支持深度思考" description="请改为自动思考或选择已声明 reasoning capability 的引擎/模型。系统不会静默降级。" />}
        {incompatible && <Alert type="error" showIcon message="当前引擎和模型协议不兼容" description="保存不会替换模型；新 Session 创建前会被拒绝。" />}
      </section>
      <aside className="runtime-strategy-preview" aria-label="运行策略预览">
        <div className="runtime-strategy-preview-heading"><Text strong>本次运行会使用什么</Text><Tag color="success">只影响新 Session</Tag></div>
        <div className="runtime-strategy-preview-card"><span>当前生效策略</span><strong>{option(effective.reasoning_mode, modeLabels)} · {option(effective.reasoning_effort, effortLabels)}</strong><strong>{option(effective.response_verbosity, verbosityLabels)} · {effective.reasoning_budget_tokens ? `${effective.reasoning_budget_tokens / 1024}K token` : '预算自动'}</strong></div>
        <div className="runtime-strategy-preview-card"><span>来源与资源</span><strong>{source ?? '来源未知'}</strong><strong>{engineLabel}</strong><strong>{routeLabel}</strong>{configVersion ? <small>配置修订 v{configVersion}</small> : null}</div>
        <div className="runtime-strategy-preview-card"><span>本次修改</span>{diff.length ? diff.map((row) => <div className="runtime-strategy-diff" key={row.label}><span>{row.label}</span><strong>{row.value}</strong></div>) : <p>未选择覆盖，继续继承默认策略。</p>}</div>
        <div className="runtime-strategy-real-note">真实请求由 Run Worker 发出；API/Web 不读取模型密钥。阶段、Usage 和秒级时间会回到工作台。</div>
      </aside>
    </div>
  )
}
