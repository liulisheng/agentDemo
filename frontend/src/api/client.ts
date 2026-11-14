export interface DigitalEmployeeSummary {
  id: string
  tenant_id: string
  name: string
  role_name: string
  description: string
  status: string
  published_to_gallery: boolean
  resource_counts: {
    knowledge_base: number
    skill: number
    sop: number
  }
}

interface ListResponse<T> {
  items: T[]
}

export async function listDigitalEmployees(): Promise<DigitalEmployeeSummary[]> {
  // 开发阶段使用 Fake 用户；生产环境由后端认证上下文替代该请求头。
  const fakeUser = import.meta.env.VITE_FAKE_USER ?? 'admin-local'
  const response = await fetch('/api/v1/digital-employees', {
    headers: { 'X-Fake-User': fakeUser },
  })

  if (!response.ok) {
    throw new Error(`digital employees request failed: ${response.status}`)
  }

  const body = (await response.json()) as ListResponse<DigitalEmployeeSummary>
  return body.items
}

export interface TraceSpan {
  id: string
  trace_id?: string
  parent_span_id?: string | null
  run_id?: string
  span_type: string
  status: string
  sequence: number
  attributes: Record<string, unknown>
}

export interface RunTrace {
  run: {
    id: string
    status: string
    result: string
    error_category?: string | null
    trace_id?: string
    snapshot: {
      employee_version: number
      employee_id?: string
      skill_ids: string[]
      tool_ids: string[]
      execution_profile_id?: string
    }
  }
  spans: TraceSpan[]
  usage: {
    input_tokens: number
    output_tokens: number
    total_tokens: number
    source: string
    estimated: boolean
  }
  runtime_reference?: {
    engine_name: string
    runtime_session_ref: string
    runtime_result_ref?: string | null
  } | null
}

export async function getRunTrace(runId: string): Promise<RunTrace> {
  const fakeUser = import.meta.env.VITE_FAKE_USER ?? 'admin-local'
  const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}/trace`, {
    headers: { 'X-Fake-User': fakeUser },
  })
  if (!response.ok) {
    throw new Error(`run trace request failed: ${response.status}`)
  }
  return (await response.json()) as RunTrace
}
