const DEFAULT_FAKE_USER = import.meta.env.VITE_FAKE_USER ?? 'admin-local'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface Page<T> {
  items: T[]
  next_cursor?: string | null
}

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
    tool?: number
  }
}

export interface EmployeeVersionRef {
  skill_id?: string
  tool_id?: string
  resource_id?: string
  version: number
}

export interface EmployeeVersion {
  employee_id: string
  tenant_id: string
  version: number
  system_prompt: string
  skill_ids: string[]
  tool_ids: string[]
  skill_version_refs: EmployeeVersionRef[]
  tool_version_refs: EmployeeVersionRef[]
  execution_profile_id: string
  execution_profile_version: number
  model_route_id?: string | null
  workspace_profile_id?: string | null
  workspace_profile_version?: number | null
  sandbox_profile_id?: string | null
  sandbox_profile_version?: number | null
  approval_policy: string
  status: string
}

export interface DigitalEmployeeDetail extends DigitalEmployeeSummary {
  version_count: number
  latest_published_version: { version: number; status: string } | null
}

export interface SkillSummary {
  id: string
  tenant_id: string
  name: string
  skill_type: string
  instruction: string
  allowed_tool_ids: string[]
  allowed_tool_versions: { tool_id: string; version: number }[]
  version: number
  status: string
  content_format?: 'INLINE' | 'FILE_PACKAGE'
  entry_path?: string | null
  file_manifest_sha256?: string | null
}

export interface ToolSummary {
  id: string
  tenant_id: string
  name: string
  description: string
  version: number
  status: string
  protocol: string
  requires_recipient_credentials: boolean
  credentials_configured: boolean
  input_schema: Record<string, unknown>
}

export interface ExecutionProfileSummary {
  id: string
  tenant_id?: string
  version?: number
  engine_name: string
  model_alias: string
  tool_policy?: string[]
  context_policy?: string
  model_route_id?: string | null
  workspace_profile_id?: string | null
  workspace_profile_version?: number | null
  sandbox_profile_id?: string | null
  sandbox_profile_version?: number | null
  approval_policy?: string
  status: string
}

export interface ModelRouteSummary {
  route_id: string
  tenant_id?: string
  alias: string
  status: string
  profile: {
    model_name: string
    provider: string
    endpoint: string
    secret_ref?: { secret_id: string } | null
  }
}

export interface DeploymentSummary {
  id: string
  tenant_id: string
  employee_id: string
  version: number
  status: string
  version_summary: {
    version: number
    status: string
    skill_count: number
    tool_count: number
    execution_profile_id: string
    execution_profile_version: number
    model_route_id?: string | null
  }
}

export interface AuditEvent {
  event_id: string
  actor_type: string
  actor_user_id: string | null
  event_type: string
  resource_type: string | null
  resource_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface HubResourceSummary {
  hub_resource_id: string
  resource_type: 'DIGITAL_EMPLOYEE' | 'SKILL' | 'TOOL'
  name: string
  description: string
  publisher_name: string
  visibility: 'PLATFORM' | 'RESTRICTED'
  latest_version: number
  available_modes: ('COPY' | 'REFERENCE')[]
  is_publisher?: boolean
}

export interface HubResourceDetail extends HubResourceSummary {
  version: number
  manifest_sha256: string
  dependency_count: number
}

export interface HubImport {
  import_id: string
  hub_resource_id: string
  hub_version: number
  mode: 'COPY' | 'REFERENCE'
  local_resource_type: string
  local_resource_id: string
  local_resource_version: number
  manifest_sha256: string
  status: string
}

export interface HubGrant {
  grant_id: string
  hub_resource_id: string
  hub_version: number
  recipient_tenant_id: string
  copy_allowed: boolean
  reference_allowed: boolean
  expires_at: string | null
  status: string
  created_by: string
}

export interface HubManifest {
  hub_resource_id: string
  version: number
  manifest_sha256: string
  manifest: {
    resource: { type: string; name: string; version: number }
    content: Record<string, unknown>
    dependencies: { sequence_no: number; type: string; resource_id: string; version: number; required: boolean }[]
    requires_recipient_credentials: boolean
  }
}

export interface SkillFileSummary {
  path: string
  mime_type: string
  size_bytes: number
  sha256: string
  virtual?: boolean
}

export interface SkillFileTree {
  skill_id: string
  version: number
  content_format: 'INLINE' | 'FILE_PACKAGE'
  entry_path: string | null
  file_manifest_sha256: string | null
  status: string
  read_only: boolean
  source_mode?: 'LOCAL' | 'REFERENCE'
  fork_allowed?: boolean
  files: SkillFileSummary[]
}

export interface SkillZipPreview {
  staging_id: string
  skill_id: string
  version: number
  strategy: 'replace' | 'merge'
  archive_sha256: string
  base_manifest_sha256: string | null
  manifest_sha256: string
  entry_path: string
  file_count: number
  expires_in_seconds: number
  files: SkillFileSummary[]
  diff: { added: string[]; updated: string[]; removed: string[] }
}

interface ErrorBody {
  code?: string
  message?: string
  details?: Record<string, unknown>
}

async function request<T>(
  path: string,
  fakeUser = DEFAULT_FAKE_USER,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('X-Fake-User', fakeUser)
  if (typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorBody
    throw new ApiError(
      body.message ?? `请求失败（HTTP ${response.status}）`,
      response.status,
      body.code ?? `HTTP_${response.status}`,
      body.details,
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function queryString(values: object): string {
  const params = new URLSearchParams()
  Object.entries(values as Record<string, string | number | undefined | null>).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  const serialized = params.toString()
  return serialized ? `?${serialized}` : ''
}

function jsonBody(value: unknown): string {
  return JSON.stringify(value)
}

export interface EmployeeListQuery {
  keyword?: string
  status?: string
  limit?: number
  cursor?: string | null
}

export async function listDigitalEmployees(
  query: EmployeeListQuery = {},
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<DigitalEmployeeSummary>> {
  return request(`/api/v1/digital-employees${queryString(query)}`, fakeUser)
}

export async function getDigitalEmployee(id: string, fakeUser = DEFAULT_FAKE_USER): Promise<DigitalEmployeeDetail> {
  return request(`/api/v1/digital-employees/${encodeURIComponent(id)}`, fakeUser)
}

export async function createDigitalEmployee(
  input: { name: string; role_name: string; description: string },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<DigitalEmployeeSummary> {
  return request('/api/v1/digital-employees', fakeUser, { method: 'POST', body: jsonBody(input) })
}

export async function patchDigitalEmployee(
  id: string,
  input: Partial<Pick<DigitalEmployeeSummary, 'name' | 'role_name' | 'description'>>,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<DigitalEmployeeSummary> {
  return request(`/api/v1/digital-employees/${encodeURIComponent(id)}`, fakeUser, {
    method: 'PATCH',
    body: jsonBody(input),
  })
}

export async function archiveDigitalEmployee(id: string, fakeUser = DEFAULT_FAKE_USER): Promise<DigitalEmployeeSummary> {
  return request(`/api/v1/digital-employees/${encodeURIComponent(id)}/archive`, fakeUser, {
    method: 'POST',
  })
}

export interface EmployeeVersionQuery {
  status?: string
  limit?: number
  cursor?: string | null
}

export async function listEmployeeVersions(
  employeeId: string,
  query: EmployeeVersionQuery = {},
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<EmployeeVersion>> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions${queryString(query)}`,
    fakeUser,
  )
}

export async function getEmployeeVersion(
  employeeId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}`,
    fakeUser,
  )
}

export async function forkEmployeeVersion(
  employeeId: string,
  sourceVersion: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${sourceVersion}/fork`,
    fakeUser,
    { method: 'POST' },
  )
}

/** 下线指定员工版本，已有部署保留其当前状态。 */
export async function deprecateEmployeeVersion(
  employeeId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}/deprecate`,
    fakeUser,
    { method: 'POST' },
  )
}

export async function createEmployeeVersion(
  employeeId: string,
  input: {
    system_prompt: string
    skill_ids: string[]
    tool_ids?: string[]
    execution_profile_id?: string
    model_route_id?: string | null
    workspace_profile_id?: string | null
    workspace_profile_version?: number | null
    sandbox_profile_id?: string | null
    sandbox_profile_version?: number | null
    approval_policy?: string
  },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions`,
    fakeUser,
    { method: 'POST', body: jsonBody(input) },
  )
}

export async function updateEmployeeVersion(
  employeeId: string,
  version: number,
  input: {
    system_prompt: string
    skill_version_refs: { resource_id: string; version: number }[]
    tool_version_refs: { resource_id: string; version: number }[]
    execution_profile_id: string
    execution_profile_version: number
    model_route_id?: string | null
    workspace_profile_id?: string | null
    workspace_profile_version?: number | null
    sandbox_profile_id?: string | null
    sandbox_profile_version?: number | null
    approval_policy?: string
  },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}`,
    fakeUser,
    { method: 'PUT', body: jsonBody(input) },
  )
}

export async function replaceEmployeeSkillBindings(
  employeeId: string,
  version: number,
  skillVersionRefs: { resource_id: string; version: number }[],
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}/skill-bindings`,
    fakeUser,
    { method: 'PUT', body: jsonBody({ skill_version_refs: skillVersionRefs }) },
  )
}

export async function replaceEmployeeToolBindings(
  employeeId: string,
  version: number,
  toolVersionRefs: { resource_id: string; version: number }[],
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}/tool-bindings`,
    fakeUser,
    { method: 'PUT', body: jsonBody({ tool_version_refs: toolVersionRefs }) },
  )
}

export async function publishEmployeeVersion(
  employeeId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<EmployeeVersion> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}/publish`,
    fakeUser,
    { method: 'POST' },
  )
}

export async function testEmployeeVersion(
  employeeId: string,
  version: number,
  prompt: string,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<{
  status: string
  result: string
  usage: { input_tokens: number; output_tokens: number; total_tokens: number; source: string; estimated: boolean }
  events: { event_type: string; span_type: string }[]
}> {
  return request(
    `/api/v1/digital-employees/${encodeURIComponent(employeeId)}/versions/${version}/test`,
    fakeUser,
    { method: 'POST', body: jsonBody({ prompt }) },
  )
}

export async function listSkills(
  fakeUser = DEFAULT_FAKE_USER,
  includeVersions = false,
): Promise<Page<SkillSummary>> {
  return request(`/api/v1/skills${queryString({ include_versions: includeVersions ? 'true' : undefined })}`, fakeUser)
}

export async function createSkill(
  input: { name: string; skill_type: string; instruction: string; allowed_tool_ids?: string[] },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<SkillSummary> {
  return request('/api/v1/skills', fakeUser, { method: 'POST', body: jsonBody(input) })
}

export async function listTools(fakeUser = DEFAULT_FAKE_USER): Promise<Page<ToolSummary>> {
  return request('/api/v1/tools', fakeUser)
}

export async function createTool(
  input: { name: string; description?: string; input_schema?: Record<string, unknown>; protocol?: string },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<ToolSummary> {
  return request('/api/v1/tools', fakeUser, { method: 'POST', body: jsonBody(input) })
}

export async function updateTool(
  toolId: string,
  input: { name?: string; description?: string; input_schema?: Record<string, unknown> },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<ToolSummary> {
  return request(`/api/v1/tools/${encodeURIComponent(toolId)}`, fakeUser, {
    method: 'PATCH',
    body: jsonBody(input),
  })
}

export async function publishSkill(skillId: string, fakeUser = DEFAULT_FAKE_USER): Promise<SkillSummary> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/publish`, fakeUser, { method: 'POST' })
}

export async function forkSkillVersion(
  skillId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<SkillSummary> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/fork`, fakeUser, {
    method: 'POST',
  })
}

export async function publishSkillVersion(
  skillId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<SkillSummary> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/publish`, fakeUser, {
    method: 'POST',
  })
}

export async function publishTool(toolId: string, fakeUser = DEFAULT_FAKE_USER): Promise<ToolSummary> {
  return request(`/api/v1/tools/${encodeURIComponent(toolId)}/publish`, fakeUser, { method: 'POST' })
}

export async function listExecutionProfiles(
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<ExecutionProfileSummary>> {
  return request('/api/v1/execution-profiles', fakeUser)
}

export async function listModelRoutes(fakeUser = DEFAULT_FAKE_USER): Promise<Page<ModelRouteSummary>> {
  return request('/api/v1/model-routes', fakeUser)
}

export interface DeploymentQuery {
  employee_id?: string
  status?: string
  limit?: number
  cursor?: string | null
}

export async function listDeployments(
  query: DeploymentQuery = {},
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<DeploymentSummary>> {
  return request(`/api/v1/deployments${queryString(query)}`, fakeUser)
}

export async function createDeployment(
  employeeId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<DeploymentSummary> {
  return request('/api/v1/deployments', fakeUser, {
    method: 'POST',
    body: jsonBody({ employee_id: employeeId, version }),
  })
}

export async function rollbackDeployment(
  deploymentId: string,
  employeeId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<DeploymentSummary> {
  return request(`/api/v1/deployments/${encodeURIComponent(deploymentId)}/rollback`, fakeUser, {
    method: 'POST',
    body: jsonBody({ employee_id: employeeId, version }),
  })
}

export interface AuditEventQuery {
  resource_type?: string
  resource_id?: string
  event_type?: string
  limit?: number
  cursor?: string | null
}

export async function listAuditEvents(
  query: AuditEventQuery = {},
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<AuditEvent>> {
  return request(`/api/v1/audit-events${queryString(query)}`, fakeUser)
}

export async function listHubResources(
  query: { resource_type?: string; keyword?: string; mode?: string; scope?: 'DISCOVERABLE' | 'OWNED'; limit?: number } = {},
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<HubResourceSummary>> {
  return request(`/api/v1/hub/resources${queryString(query)}`, fakeUser)
}

export async function getAuthorizedHubManifest(
  hubId: string,
  version: number,
  mode: 'COPY' | 'REFERENCE',
  fakeUser = DEFAULT_FAKE_USER,
): Promise<HubManifest> {
  return request(
    `/api/v1/hub/resources/${encodeURIComponent(hubId)}/versions/${version}/manifest${queryString({ mode })}`,
    fakeUser,
  )
}

export async function listHubGrants(
  hubId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<HubGrant>> {
  return request(`/api/v1/hub/resources/${encodeURIComponent(hubId)}/versions/${version}/grants`, fakeUser)
}

export async function createHubGrant(
  hubId: string,
  version: number,
  input: {
    recipient_tenant_id: string
    copy_allowed: boolean
    reference_allowed: boolean
    expires_at?: string | null
  },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<HubGrant> {
  return request(`/api/v1/hub/resources/${encodeURIComponent(hubId)}/versions/${version}/grants`, fakeUser, {
    method: 'POST',
    body: jsonBody(input),
  })
}

export async function revokeHubGrant(grantId: string, fakeUser = DEFAULT_FAKE_USER): Promise<{ grant_id: string; status: string }> {
  return request(`/api/v1/hub/grants/${encodeURIComponent(grantId)}`, fakeUser, { method: 'DELETE' })
}

export async function getHubResourceDetail(
  hubId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<HubResourceDetail> {
  return request(`/api/v1/hub/resources/${encodeURIComponent(hubId)}/versions/${version}`, fakeUser)
}

export async function createHubPackage(
  input: { resource_type: string; source_resource_id: string; source_resource_version: number; visibility?: string },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request('/api/v1/hub/resources', fakeUser, { method: 'POST', body: jsonBody(input) })
}

export async function createHubPackageVersion(
  hubId: string,
  input: { source_resource_id: string; source_resource_version: number },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(`/api/v1/hub/resources/${encodeURIComponent(hubId)}/versions`, fakeUser, {
    method: 'POST',
    body: jsonBody(input),
  })
}

export async function publishHubPackage(
  hubId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(`/api/v1/hub/resources/${encodeURIComponent(hubId)}/versions/${version}/publish`, fakeUser, {
    method: 'POST',
  })
}

export async function importHubResource(
  input: { hub_resource_id: string; hub_version: number; mode: 'COPY' | 'REFERENCE' },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<HubImport> {
  return request('/api/v1/hub/imports', fakeUser, { method: 'POST', body: jsonBody(input) })
}

export async function listHubImports(
  query: { status?: string; resource_type?: string; limit?: number } = {},
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Page<HubImport>> {
  return request(`/api/v1/hub/imports${queryString(query)}`, fakeUser)
}

export async function upgradeHubImport(
  importId: string,
  hubVersion: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<HubImport> {
  return request(`/api/v1/hub/imports/${encodeURIComponent(importId)}/upgrade`, fakeUser, {
    method: 'POST',
    body: jsonBody({ hub_version: hubVersion }),
  })
}

export async function revokeHubImport(importId: string, fakeUser = DEFAULT_FAKE_USER): Promise<HubImport> {
  return request(`/api/v1/hub/imports/${encodeURIComponent(importId)}`, fakeUser, { method: 'DELETE' })
}

export async function getSkillFileTree(
  skillId: string,
  version: number,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<SkillFileTree> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files`, fakeUser)
}

export async function getSkillFileContent(
  skillId: string,
  version: number,
  path: string,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<{ path: string; mime_type: string; size_bytes: number; sha256: string | null; content_text: string | null; content_base64: string | null }> {
  return request(
    `/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files/content${queryString({ file_path: path })}`,
    fakeUser,
  )
}

export async function createSkillFile(
  skillId: string,
  version: number,
  input: { file_path: string; content_text?: string; content_base64?: string; mime_type?: string },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files`, fakeUser, {
    method: 'POST',
    body: jsonBody(input),
  })
}

export async function updateSkillFile(
  skillId: string,
  version: number,
  input: { file_path: string; expected_sha256: string; content_text?: string; content_base64?: string; mime_type?: string },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files`, fakeUser, {
    method: 'PUT',
    body: jsonBody(input),
  })
}

export async function renameSkillFile(
  skillId: string,
  version: number,
  input: { file_path: string; new_file_path: string; expected_sha256: string },
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(`/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files/rename`, fakeUser, {
    method: 'POST',
    body: jsonBody(input),
  })
}

export async function deleteSkillFile(
  skillId: string,
  version: number,
  filePath: string,
  expectedSha256: string,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(
    `/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files${queryString({ file_path: filePath, expected_sha256: expectedSha256 })}`,
    fakeUser,
    { method: 'DELETE' },
  )
}

export async function previewSkillZip(
  skillId: string,
  version: number,
  archive: Blob,
  strategy: 'replace' | 'merge',
  fakeUser = DEFAULT_FAKE_USER,
): Promise<SkillZipPreview> {
  return request(
    `/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files/zip/preview${queryString({ strategy })}`,
    fakeUser,
    { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: archive },
  )
}

export async function commitSkillZip(
  skillId: string,
  version: number,
  stagingId: string,
  archive: Blob,
  fakeUser = DEFAULT_FAKE_USER,
): Promise<Record<string, unknown>> {
  return request(
    `/api/v1/skills/${encodeURIComponent(skillId)}/versions/${version}/files/zip/commit/${encodeURIComponent(stagingId)}`,
    fakeUser,
    { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: archive },
  )
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
      hub_resource_refs?: { hub_resource_id: string; hub_version: number; manifest_sha256: string; mode: string }[]
      skill_file_packages?: { skill_id: string; skill_version: number; entry_path: string; manifest_sha256: string; files: SkillFileSummary[] }[]
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

export async function getRunTrace(runId: string, fakeUser = DEFAULT_FAKE_USER): Promise<RunTrace> {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}/trace`, fakeUser)
}
