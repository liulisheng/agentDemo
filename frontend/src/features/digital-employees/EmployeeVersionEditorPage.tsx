import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import {
  deprecateEmployeeVersion,
  forkEmployeeVersion,
  getDigitalEmployee,
  getEmployeeVersion,
  listDeployments,
  listExecutionProfiles,
  listModelRoutes,
  listSkills,
  listTools,
  publishEmployeeVersion,
  testEmployeeVersion,
  updateEmployeeVersion,
} from '../../api/client'
import type {
  DeploymentSummary,
  EmployeeVersion,
  ModelRouteSummary,
  SkillSummary,
  ToolSummary,
} from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Title, Text, Paragraph } = Typography

/** 员工版本时间线和配置页使用相同的状态名称。 */
export const employeeVersionStatusLabels: Readonly<Partial<Record<string, string>>> = {
  DRAFT: '草稿',
  PUBLISHED: '已发布',
  DEPRECATED: '已下线',
}

async function listActiveVersionDeployments(employeeId: string, version: number, fakeUser: string) {
  const deployments: DeploymentSummary[] = []
  let cursor: string | null | undefined
  // 部署列表按员工分页，需要读完才能显示该版本的实际影响范围。
  do {
    const page = await listDeployments({ employee_id: employeeId, status: 'ACTIVE', limit: 200, cursor }, fakeUser)
    deployments.push(...page.items.filter((item) => item.version === version))
    cursor = page.next_cursor
  } while (cursor)
  return deployments
}

/** 两个版本入口复用分叉、下线确认和缓存刷新逻辑。 */
export function EmployeeVersionLifecycleActions({ employeeId, version }: {
  employeeId: string
  version: EmployeeVersion
}) {
  const { fakeUser, getCurrentFakeUser } = useTenantContext()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const deploymentImpact = useQuery({
    queryKey: ['active-version-deployments', fakeUser, employeeId, version.version],
    queryFn: () => listActiveVersionDeployments(employeeId, version.version, fakeUser),
    enabled: confirming && version.status === 'PUBLISHED',
  })
  const invalidateEmployee = (userId: string) => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['digital-employees', userId] }),
    queryClient.invalidateQueries({ queryKey: ['digital-employee', userId, employeeId] }),
    queryClient.invalidateQueries({ queryKey: ['employee-versions', userId, employeeId] }),
  ])
  const fork = useMutation<EmployeeVersion, Error, { fakeUser: string; pathname: string; historyKey?: string }>({
    mutationFn: (source) => forkEmployeeVersion(employeeId, version.version, source.fakeUser),
    onSuccess: async (created, source) => {
      queryClient.setQueryData(['employee-version', source.fakeUser, employeeId, created.version], created)
      await invalidateEmployee(source.fakeUser)
      // 缓存刷新可能卸载来源卡片，通过当前身份和浏览器历史判断是否还应跳转。
      if (
        getCurrentFakeUser() !== source.fakeUser ||
        window.location.pathname !== source.pathname ||
        window.history.state?.key !== source.historyKey
      ) return
      message.success(`已从 v${version.version} 创建草稿 v${created.version}`)
      navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/versions/${created.version}`, {
        state: { sourceVersion: version.version },
      })
    },
    onError: (error) => message.error(error.message),
  })
  const deprecate = useMutation({
    mutationFn: () => deprecateEmployeeVersion(employeeId, version.version, fakeUser),
    onSuccess: async (deprecated) => {
      setConfirming(false)
      queryClient.setQueryData(['employee-version', fakeUser, employeeId, version.version], deprecated)
      await Promise.all([
        invalidateEmployee(fakeUser),
        queryClient.invalidateQueries({ queryKey: ['employee-version', fakeUser, employeeId, version.version] }),
        queryClient.invalidateQueries({ queryKey: ['deployments', fakeUser] }),
      ])
      message.success(`版本 v${version.version} 已下线`)
    },
  })

  const createDraft = () => {
    if (getCurrentFakeUser() !== fakeUser) return
    fork.mutate({
      fakeUser,
      pathname: window.location.pathname,
      historyKey: window.history.state?.key,
    })
  }

  return (
    <>
      <Button loading={fork.isPending} disabled={deprecate.isPending} onClick={createDraft}>
        基于此版本创建草稿
      </Button>
      {version.status === 'PUBLISHED' && (
        <Button
          danger
          disabled={fork.isPending}
          loading={deprecate.isPending}
          onClick={() => {
            deprecate.reset()
            setConfirming(true)
          }}
        >
          下线版本
        </Button>
      )}
      <Modal
        title={`下线版本 v${version.version}？`}
        open={confirming && version.status === 'PUBLISHED'}
        onCancel={() => setConfirming(false)}
        onOk={() => deprecate.mutate()}
        okText="确认下线版本"
        okButtonProps={{
          danger: true,
          disabled: deploymentImpact.isFetching || !deploymentImpact.isSuccess,
        }}
        cancelText="取消"
        confirmLoading={deprecate.isPending}
      >
        {deploymentImpact.isFetching && <Paragraph role="status">正在查询运行中的 Deployment…</Paragraph>}
        {deploymentImpact.isError && (
          <Alert
            type="error"
            showIcon
            message="无法查询运行中的 Deployment"
            description={deploymentImpact.error.message}
            action={<Button onClick={() => void deploymentImpact.refetch()}>重试</Button>}
          />
        )}
        {deploymentImpact.isSuccess && !deploymentImpact.isFetching && (
          <>
            <Paragraph>有 {deploymentImpact.data.length} 个 ACTIVE Deployment 使用此版本。</Paragraph>
            {deploymentImpact.data.length > 0 && (
              <ul className="employee-version-deployments" tabIndex={0} aria-label="使用此版本的 ACTIVE Deployment">
                {deploymentImpact.data.map((item) => <li key={item.id}>{item.id}</li>)}
              </ul>
            )}
          </>
        )}
        <Paragraph>下线后，今后新建部署和回滚均无法选择此版本。现有 Deployment 不会自动停止。</Paragraph>
        <Paragraph>如需停止运行，请前往<Link to="/enterprise/deployments">部署记录</Link>逐个下线。</Paragraph>
        {deprecate.isError && <Alert type="error" showIcon message={deprecate.error.message} />}
      </Modal>
    </>
  )
}

interface VersionFormValues {
  system_prompt: string
  skill_keys: string[]
  tool_keys: string[]
  execution_profile_key: string
  model_route_id?: string
  workspace_profile_id?: string
  workspace_profile_version?: number
  sandbox_profile_id?: string
  sandbox_profile_version?: number
  approval_policy: string
}

const refKey = (resourceId: string, version: number) => `${resourceId}::${version}`

function parseRefKey(value: string) {
  const separator = value.lastIndexOf('::')
  return { resource_id: value.slice(0, separator), version: Number(value.slice(separator + 2)) }
}

function versionSkillRefs(version: EmployeeVersion) {
  return version.skill_version_refs.map((ref) => refKey(ref.skill_id ?? ref.resource_id ?? '', ref.version))
}

function versionToolRefs(version: EmployeeVersion) {
  return version.tool_version_refs.map((ref) => refKey(ref.tool_id ?? ref.resource_id ?? '', ref.version))
}

function selectedRefs(keys: string[]) {
  return keys.map(parseRefKey).filter((item) => item.resource_id && Number.isFinite(item.version))
}

/** 员工版本配置页：编辑草稿或查看历史版本的固定配置。 */
export function EmployeeVersionEditorPage() {
  const { employeeId = '', version: versionParam = '1' } = useParams()
  const versionNumber = Number(versionParam)
  const location = useLocation()
  const sourceVersion = location.state?.sourceVersion as number | undefined
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<VersionFormValues>()
  const [dirty, setDirty] = useState(false)
  const [testOpen, setTestOpen] = useState(false)
  const [testPrompt, setTestPrompt] = useState('请按当前版本配置处理这个请求。')
  const [testResult, setTestResult] = useState<{ result: string; status: string } | null>(null)
  const [publishOpen, setPublishOpen] = useState(false)
  const initializedKey = useRef('')

  const employee = useQuery({
    queryKey: ['digital-employee', fakeUser, employeeId],
    queryFn: () => getDigitalEmployee(employeeId, fakeUser),
    enabled: Boolean(employeeId),
  })
  const version = useQuery({
    queryKey: ['employee-version', fakeUser, employeeId, versionNumber],
    queryFn: () => getEmployeeVersion(employeeId, versionNumber, fakeUser),
    enabled: Boolean(employeeId && Number.isFinite(versionNumber)),
  })
  const skills = useQuery({
    queryKey: ['skills', fakeUser],
    queryFn: () => listSkills(fakeUser, true),
  })
  const tools = useQuery({
    queryKey: ['tools', fakeUser],
    queryFn: () => listTools(fakeUser),
  })
  const profiles = useQuery({
    queryKey: ['execution-profiles', fakeUser],
    queryFn: () => listExecutionProfiles(fakeUser),
  })
  const modelRoutes = useQuery({
    queryKey: ['model-routes', fakeUser],
    queryFn: () => listModelRoutes(fakeUser),
  })

  useEffect(() => {
    if (!version.data) return
    const key = `${fakeUser}:${employeeId}:${versionNumber}`
    if (initializedKey.current === key) return
    initializedKey.current = key
    form.setFieldsValue({
      system_prompt: version.data.system_prompt,
      skill_keys: versionSkillRefs(version.data),
      tool_keys: versionToolRefs(version.data),
      execution_profile_key: refKey(version.data.execution_profile_id, version.data.execution_profile_version),
      model_route_id: version.data.model_route_id ?? undefined,
      workspace_profile_id: version.data.workspace_profile_id ?? undefined,
      workspace_profile_version: version.data.workspace_profile_version ?? undefined,
      sandbox_profile_id: version.data.sandbox_profile_id ?? undefined,
      sandbox_profile_version: version.data.sandbox_profile_version ?? undefined,
      approval_policy: version.data.approval_policy,
    })
    setDirty(false)
    setTestResult(null)
    setTestOpen(false)
    setPublishOpen(false)
  }, [employeeId, fakeUser, form, version.data, versionNumber])

  const selectedValues = Form.useWatch([], form) as VersionFormValues | undefined
  const skillOptions = useMemo(() => {
    const values = skills.data?.items ?? []
    const known = new Set(values.map((item) => refKey(item.id, item.version)))
    const persisted = selectedValues?.skill_keys ?? []
    const fallback = persisted
      .filter((key) => !known.has(key))
      .map((key) => {
        const ref = parseRefKey(key)
        return { value: key, label: `引用 Skill ${ref.resource_id} · v${ref.version}`, disabled: true }
      })
    return [
      ...values.map((item) => ({
        value: refKey(item.id, item.version),
        label: `${item.name} · v${item.version} · ${item.status}`,
        disabled: item.status !== 'PUBLISHED',
      })),
      ...fallback,
    ]
  }, [selectedValues?.skill_keys, skills.data?.items])
  const toolOptions = useMemo(() => {
    const values = tools.data?.items ?? []
    const known = new Set(values.map((item) => refKey(item.id, item.version)))
    const persisted = selectedValues?.tool_keys ?? []
    const fallback = persisted
      .filter((key) => !known.has(key))
      .map((key) => {
        const ref = parseRefKey(key)
        return { value: key, label: `引用 Tool ${ref.resource_id} · v${ref.version}`, disabled: true }
      })
    return [
      ...values.map((item) => ({
        value: refKey(item.id, item.version),
        label: `${item.name} · v${item.version} · ${item.status}`,
        disabled: item.status !== 'PUBLISHED',
      })),
      ...fallback,
    ]
  }, [selectedValues?.tool_keys, tools.data?.items])

  const selectedSkills = useMemo(() => {
    const lookup = new Map((skills.data?.items ?? []).map((skill) => [refKey(skill.id, skill.version), skill]))
    return (selectedValues?.skill_keys ?? []).map((key) => lookup.get(key)).filter((item): item is SkillSummary => Boolean(item))
  }, [selectedValues?.skill_keys, skills.data?.items])
  const selectedTools = useMemo(() => {
    const lookup = new Map((tools.data?.items ?? []).map((tool) => [refKey(tool.id, tool.version), tool]))
    return (selectedValues?.tool_keys ?? []).map((key) => lookup.get(key)).filter((item): item is ToolSummary => Boolean(item))
  }, [selectedValues?.tool_keys, tools.data?.items])
  const profileItems = profiles.data?.items ?? []
  const selectedProfileKey = selectedValues?.execution_profile_key
  const selectedProfile = profileItems.find(
    (profile) => profile.version !== undefined && refKey(profile.id, profile.version) === selectedProfileKey,
  )
  const profileOptions = profileItems.flatMap((profile) => {
    if (profile.version === undefined) return []
    return [{
      value: refKey(profile.id, profile.version),
      label: `${profile.engine_name} · ${profile.model_alias} · v${profile.version} · ${profile.status}`,
      disabled: profile.status !== 'PUBLISHED',
    }]
  })
  if (selectedProfileKey && !selectedProfile) {
    const pinned = parseRefKey(selectedProfileKey)
    profileOptions.push({
      value: selectedProfileKey,
      label: `固定配置 ${pinned.resource_id} · v${pinned.version}`,
      disabled: true,
    })
  }
  const toolPolicy = selectedProfile?.tool_policy
  const declaredToolIds = new Set(selectedSkills.flatMap((skill) => skill.allowed_tool_ids))
  const effectiveTools = selectedTools.filter(
    (tool) => declaredToolIds.has(tool.id) && (toolPolicy?.length === 0 || toolPolicy?.includes(tool.name)),
  )
  const excludedTools = selectedTools.filter((tool) => !effectiveTools.includes(tool))
  const archived = employee.data?.status === 'ARCHIVED'
  const admin = currentIdentity.roleName === '管理员'
  const canEditDraft = admin && !archived && version.data?.status === 'DRAFT'

  const saveMutation = useMutation({
    mutationFn: (values: VersionFormValues) => {
      // 直接提交表单选中的精确引用，目录更新不会改变已固定的版本号。
      const executionProfile = parseRefKey(values.execution_profile_key)
      return updateEmployeeVersion(
        employeeId,
        versionNumber,
        {
          system_prompt: values.system_prompt,
          skill_version_refs: selectedRefs(values.skill_keys),
          tool_version_refs: selectedRefs(values.tool_keys),
          execution_profile_id: executionProfile.resource_id,
          execution_profile_version: executionProfile.version,
          model_route_id: values.model_route_id || null,
          workspace_profile_id: values.workspace_profile_id || null,
          workspace_profile_version: values.workspace_profile_version ?? null,
          sandbox_profile_id: values.sandbox_profile_id || null,
          sandbox_profile_version: values.sandbox_profile_version ?? null,
          approval_policy: values.approval_policy,
        },
        fakeUser,
      )
    },
    onSuccess: async (saved) => {
      initializedKey.current = `${fakeUser}:${employeeId}:${versionNumber}`
      setDirty(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['employee-version', fakeUser, employeeId, versionNumber] }),
        queryClient.invalidateQueries({ queryKey: ['employee-versions', fakeUser, employeeId] }),
      ])
      message.success(`草稿版本 v${saved.version} 已保存`)
    },
  })

  const publishMutation = useMutation({
    mutationFn: () => publishEmployeeVersion(employeeId, versionNumber, fakeUser),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['employee-version', fakeUser, employeeId, versionNumber] }),
        queryClient.invalidateQueries({ queryKey: ['employee-versions', fakeUser, employeeId] }),
        queryClient.invalidateQueries({ queryKey: ['digital-employee', fakeUser, employeeId] }),
      ])
      setPublishOpen(false)
      message.success(`版本 v${versionNumber} 已发布`)
    },
  })

  const testMutation = useMutation({
    mutationFn: () => testEmployeeVersion(employeeId, versionNumber, testPrompt, fakeUser),
    onSuccess: (result) => {
      setTestResult({ result: result.result, status: result.status })
    },
  })

  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  useEffect(() => {
    if (!dirty) return
    const handler = (event: globalThis.MouseEvent) => {
      const target = event.target
      if (!(target instanceof Element)) return
      const anchor = target.closest('a[href]') as HTMLAnchorElement | null
      if (!anchor || !anchor.getAttribute('href')?.startsWith('/')) return
      if (!window.confirm('当前版本有未保存的修改，确定离开编辑器吗？')) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    document.addEventListener('click', handler, true)
    return () => document.removeEventListener('click', handler, true)
  }, [dirty])

  if (version.isLoading || employee.isLoading) return <div className="management-loading">正在加载版本配置…</div>
  if (version.isError || employee.isError || !version.data || !employee.data) {
    return <Alert type="error" showIcon message="版本配置加载失败" description={(version.error ?? employee.error as Error)?.message} />
  }

  const currentVersion = version.data
  const statusLabel = employeeVersionStatusLabels[currentVersion.status] ?? currentVersion.status
  const editorTitle = currentVersion.status === 'DRAFT'
    ? canEditDraft ? '编辑草稿' : '查看草稿'
    : `${statusLabel}版本`
  return (
    <section className="management-page" aria-label="员工版本编辑器">
      <div className="management-page-header employee-editor-header">
        <div>
          <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}`}>
            ← 返回员工详情
          </Link>
          <Text className="management-eyebrow">{employee.data.name}</Text>
          <Space align="center">
            <Title level={2}>{editorTitle} v{versionNumber}</Title>
            <Tag color={currentVersion.status === 'PUBLISHED' ? 'green' : currentVersion.status === 'DRAFT' ? 'blue' : 'default'}>{statusLabel}</Tag>
          </Space>
          {currentVersion.status === 'DRAFT' && typeof sourceVersion === 'number' && (
            <Paragraph type="secondary">基于版本 v{sourceVersion} 创建</Paragraph>
          )}
          {canEditDraft && (
            <Text type="secondary" role="status">{dirty ? '有未保存的修改' : '草稿已保存'}</Text>
          )}
        </div>
        {canEditDraft && (
          <div className="employee-version-actions">
            <Link
              className="ant-btn ant-btn-default"
              to={`/enterprise/employees/${encodeURIComponent(employeeId)}/skills/new`}
            >
              新建 Skill 草稿
            </Link>
            <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/new`}>管理 Tool</Link>
            <Button loading={testMutation.isPending} onClick={() => setTestOpen(true)}>测试版本</Button>
            <Button type="primary" loading={saveMutation.isPending} onClick={() => form.submit()}>保存草稿</Button>
            <Button type="primary" ghost onClick={() => setPublishOpen(true)} disabled={dirty}>发布版本</Button>
          </div>
        )}
        {admin && !archived && (currentVersion.status === 'PUBLISHED' || currentVersion.status === 'DEPRECATED') && (
          <div className="employee-version-actions">
            <EmployeeVersionLifecycleActions
              key={`${fakeUser}:${employeeId}:${versionNumber}`}
              employeeId={employeeId}
              version={currentVersion}
            />
          </div>
        )}
      </div>

      {!admin && (
        <Alert type="info" showIcon message={`当前身份：${currentIdentity.label}`} description="成员可查看配置。版本变更需由租户管理员执行。" />
      )}
      {archived && <Alert type="info" showIcon message="员工已归档，版本配置仅供查看。" />}
      {!archived && admin && currentVersion.status !== 'DRAFT' && (
        <Alert type="info" showIcon message="此版本的配置只读。创建草稿后可编辑继承的配置。" />
      )}
      {(skills.isError || tools.isError || profiles.isError || modelRoutes.isError) && (
        <Alert type="error" showIcon message="依赖目录加载失败" description="Skill、Tool、ExecutionProfile 或 ModelRoute 加载失败，请重试。" />
      )}
      {saveMutation.isError && <Alert type="error" showIcon message={(saveMutation.error as Error).message} />}
      {publishMutation.isError && (
        <Alert
          type="error"
          showIcon
          message="发布预检未通过"
          description={JSON.stringify((publishMutation.error as { details?: unknown }).details ?? (publishMutation.error as Error).message)}
        />
      )}

      <Form<VersionFormValues>
        form={form}
        layout="vertical"
        disabled={!canEditDraft}
        onValuesChange={() => setDirty(true)}
        onFinish={(values) => {
          if (canEditDraft) saveMutation.mutate(values)
        }}
      >
        <div className="employee-version-editor-sections">
          <Card className="management-card employee-editor-card" title="角色指令" aria-label="角色指令">
            <Paragraph type="secondary">描述员工职责、边界和回答要求。</Paragraph>
            <Form.Item
              name="system_prompt"
              label="系统指令"
              extra="定义员工每次处理请求时都遵守的角色、规则和边界。"
              rules={[{ required: true, message: '请输入系统指令' }]}
            >
              <Input.TextArea rows={7} maxLength={65535} placeholder="定义角色职责、流程边界和输出要求" />
            </Form.Item>
          </Card>

          <section className="employee-editor-resource-section" aria-label="Skill 绑定">
            <Card className="management-card employee-editor-card" title="Skills">
              <div className="employee-editor-section-intro">
                <div>
                  <Text strong>这个版本使用的 Skills</Text>
                  <Paragraph type="secondary">每个 Skill 独立选择并固定版本；Tool 选择在下一块单独管理。</Paragraph>
                </div>
                <span className="employee-editor-count">{selectedValues?.skill_keys?.length ?? 0}</span>
              </div>
              <Form.Item name="skill_keys" label="选择 Skill 版本" rules={[{ required: true, message: '至少选择一个 Skill' }]}>
                <Select mode="multiple" options={skillOptions} placeholder="选择已发布的 Skill" />
              </Form.Item>
              <Text type="secondary">这里只绑定已发布的精确版本；创建、编辑和发布 Skill 请从员工 Skills 页进入独立文件工作区。</Text>
            </Card>
          </section>

          <section className="employee-editor-resource-section" aria-label="Tool 绑定">
            <Card className="management-card employee-editor-card" title="Tools">
              <div className="employee-editor-section-intro">
                <div>
                  <Text strong>这个版本允许使用的 Tools</Text>
                  <Paragraph type="secondary">Tool 与 Skill 分开选择；最终可用范围会显示在下面。</Paragraph>
                </div>
                <span className="employee-editor-count">{selectedValues?.tool_keys?.length ?? 0}</span>
              </div>
              <Form.Item name="tool_keys" label="选择 Tool 版本">
                <Select mode="multiple" options={toolOptions} placeholder="选择已发布的 Tool" />
              </Form.Item>
              <div className="employee-tool-policy-panel">
                <Text strong>当前版本可用范围</Text>
                <Paragraph type="secondary">运行时会同时检查员工选择、Skill 声明范围和执行策略。</Paragraph>
                {toolPolicy ? (
                  <Space wrap>
                    {effectiveTools.map((tool) => <Tag key={tool.id} color="green">可用 · {tool.name}</Tag>)}
                    {excludedTools.map((tool) => <Tag key={tool.id} color="orange">被策略排除 · {tool.name}</Tag>)}
                    {selectedTools.length === 0 && <Text type="secondary">选择 Skill 和 Tool 后显示结果</Text>}
                  </Space>
                ) : (
                  <Text type="secondary">
                    {selectedProfileKey ? '无法读取固定版本的工具策略，暂时无法预览。' : '请选择执行配置以预览。'}
                  </Text>
                )}
              </div>
              {canEditDraft && (
                <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/new`}>打开 Tool 管理</Link>
              )}
            </Card>
          </section>

          <Card className="management-card employee-editor-card" title="运行配置">
            <Paragraph type="secondary">先设置模型与审批策略；可选执行环境默认收起。</Paragraph>
            <div className="employee-runtime-primary-fields">
              <Form.Item name="execution_profile_key" label="ExecutionProfile" rules={[{ required: true, message: '请选择执行配置' }]}>
                <Select options={profileOptions} placeholder="选择执行配置" />
              </Form.Item>
              <Form.Item name="model_route_id" label="ModelRoute">
                <Select
                  allowClear
                  options={(modelRoutes.data?.items ?? []).map((route: ModelRouteSummary) => ({
                    value: route.route_id,
                    label: `${route.alias} · ${route.profile.model_name}`,
                  }))}
                  placeholder="使用执行配置默认路由"
                />
              </Form.Item>
              <Form.Item name="approval_policy" label="审批策略">
                <Select options={[
                  { value: 'none', label: '无需审批' },
                  { value: 'required-for-write', label: '写操作需审批' },
                  { value: 'always', label: '所有操作需审批' },
                ]} />
              </Form.Item>
            </div>
            <Collapse
              className="employee-runtime-advanced"
              items={[{
                key: 'advanced',
                label: '高级环境参数（可选）',
                // 保持未展开的字段注册，保存其他字段时也会提交固定环境配置。
                forceRender: true,
                children: (
                  <div className="employee-runtime-advanced-fields">
                    <Form.Item label="Workspace Profile">
                      <Space.Compact block>
                        <Form.Item name="workspace_profile_id" noStyle>
                          <Input placeholder="Profile ID" />
                        </Form.Item>
                        <Form.Item name="workspace_profile_version" noStyle>
                          <InputNumber min={1} placeholder="版本" />
                        </Form.Item>
                      </Space.Compact>
                    </Form.Item>
                    <Form.Item label="Sandbox Profile">
                      <Space.Compact block>
                        <Form.Item name="sandbox_profile_id" noStyle>
                          <Input placeholder="Profile ID" />
                        </Form.Item>
                        <Form.Item name="sandbox_profile_version" noStyle>
                          <InputNumber min={1} placeholder="版本" />
                        </Form.Item>
                      </Space.Compact>
                    </Form.Item>
                  </div>
                ),
              }]}
            />
            {toolPolicy && (
              <Paragraph className="employee-profile-policy" type="secondary">
                Profile 工具策略：{toolPolicy.join('、') || '不额外限制'}
              </Paragraph>
            )}
          </Card>
        </div>
      </Form>

      {testResult && (
        <Card className="management-card" title="版本测试结果" extra={<Tag color={testResult.status === 'succeeded' ? 'green' : 'red'}>{testResult.status}</Tag>}>
          <pre className="prompt-preview">{testResult.result}</pre>
        </Card>
      )}

      <Modal
        title="测试当前草稿版本"
        open={canEditDraft && testOpen}
        onCancel={() => setTestOpen(false)}
        onOk={() => {
          if (dirty) {
            message.warning('请先保存草稿，再运行测试')
            return
          }
          testMutation.mutate()
          setTestOpen(false)
        }}
        okText="运行 Fake Runtime"
        cancelText="取消"
        confirmLoading={testMutation.isPending}
        destroyOnHidden
      >
        <Paragraph type="secondary">测试使用已保存的 DRAFT 快照，不创建正式 Deployment、Session 或 Run。</Paragraph>
        <Input.TextArea
          aria-label="测试 prompt"
          value={testPrompt}
          onChange={(event) => setTestPrompt(event.target.value)}
          rows={3}
        />
        {dirty && <Alert className="modal-warning" type="warning" message="有未保存修改，请先保存后再测试。" />}
      </Modal>

      <Modal
        title="发布员工版本"
        open={canEditDraft && publishOpen}
        onCancel={() => setPublishOpen(false)}
        onOk={() => publishMutation.mutate()}
        okText="确认发布"
        cancelText="返回修改"
        confirmLoading={publishMutation.isPending}
      >
        <Paragraph>发布后版本不可修改，系统会重新验证 Skill/Tool 发布状态、Hub Grant、摘要和凭据。</Paragraph>
        <Paragraph strong>{employee.data.name} · v{versionNumber}</Paragraph>
      </Modal>
    </section>
  )
}
