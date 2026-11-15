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
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  getDigitalEmployee,
  getEmployeeVersion,
  listExecutionProfiles,
  listModelRoutes,
  listSkills,
  listTools,
  publishEmployeeVersion,
  testEmployeeVersion,
  updateEmployeeVersion,
} from '../../api/client'
import type {
  EmployeeVersion,
  ExecutionProfileSummary,
  ModelRouteSummary,
  SkillSummary,
  ToolSummary,
} from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Title, Text, Paragraph } = Typography

interface VersionFormValues {
  system_prompt: string
  skill_keys: string[]
  tool_keys: string[]
  execution_profile_id: string
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

/** DRAFT 员工版本向导：编辑指令、精确资源版本和执行配置。 */
export function EmployeeVersionEditorPage() {
  const { employeeId = '', version: versionParam = '1' } = useParams()
  const versionNumber = Number(versionParam)
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
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
      execution_profile_id: version.data.execution_profile_id,
      model_route_id: version.data.model_route_id ?? undefined,
      workspace_profile_id: version.data.workspace_profile_id ?? undefined,
      workspace_profile_version: version.data.workspace_profile_version ?? undefined,
      sandbox_profile_id: version.data.sandbox_profile_id ?? undefined,
      sandbox_profile_version: version.data.sandbox_profile_version ?? undefined,
      approval_policy: version.data.approval_policy,
    })
    setDirty(false)
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
  const selectedProfile = (profiles.data?.items ?? []).find(
    (profile) => profile.id === selectedValues?.execution_profile_id,
  )
  const toolPolicy = selectedProfile?.tool_policy ?? []
  const declaredToolIds = new Set(selectedSkills.flatMap((skill) => skill.allowed_tool_ids))
  const effectiveTools = selectedTools.filter(
    (tool) => declaredToolIds.has(tool.id) && (toolPolicy.length === 0 || toolPolicy.includes(tool.name)),
  )
  const excludedTools = selectedTools.filter((tool) => !effectiveTools.includes(tool))
  const readOnly = version.data?.status !== 'DRAFT' || employee.data?.status === 'ARCHIVED'
  const admin = currentIdentity.roleName === '管理员'

  const saveMutation = useMutation({
    mutationFn: (values: VersionFormValues) => updateEmployeeVersion(
      employeeId,
      versionNumber,
      {
        system_prompt: values.system_prompt,
        skill_version_refs: selectedRefs(values.skill_keys),
        tool_version_refs: selectedRefs(values.tool_keys),
        execution_profile_id: values.execution_profile_id,
        execution_profile_version: selectedProfile?.version ?? version.data?.execution_profile_version ?? 1,
        model_route_id: values.model_route_id || null,
        workspace_profile_id: values.workspace_profile_id || null,
        workspace_profile_version: values.workspace_profile_version ?? null,
        sandbox_profile_id: values.sandbox_profile_id || null,
        sandbox_profile_version: values.sandbox_profile_version ?? null,
        approval_policy: values.approval_policy,
      },
      fakeUser,
    ),
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

  const runTest = async () => {
    if (dirty) {
      message.warning('请先保存草稿，再测试当前版本')
      return
    }
    testMutation.mutate()
  }

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
  return (
    <section className="management-page" aria-label="员工版本编辑器">
      <div className="management-page-header employee-editor-header">
        <div>
          <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}`}>
            ← 返回员工详情
          </Link>
          <Text className="management-eyebrow">{employee.data.name} / 版本 v{versionNumber}</Text>
          <Space align="center">
            <Title level={2}>员工版本配置</Title>
            <Tag color={currentVersion.status === 'PUBLISHED' ? 'green' : 'blue'}>{currentVersion.status}</Tag>
          </Space>
          <Paragraph type="secondary">绑定精确 Skill/Tool 版本，并预览执行配置的有效工具交集。</Paragraph>
        </div>
        {admin && !readOnly && (
          <Space>
            <Link
              className="ant-btn ant-btn-default"
              to={`/enterprise/employees/${encodeURIComponent(employeeId)}/skills/new`}
            >
              新建 Skill 草稿
            </Link>
            <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/new`}>管理 Tool</Link>
            <Button onClick={() => setTestOpen(true)}>测试版本</Button>
            <Button type="primary" loading={saveMutation.isPending} onClick={() => form.submit()}>保存草稿</Button>
            <Button type="primary" ghost onClick={() => setPublishOpen(true)} disabled={dirty}>发布版本</Button>
          </Space>
        )}
      </div>

      {readOnly && <Alert type="info" showIcon message="此版本只读" description="已发布版本和已归档员工版本不可编辑。" />}
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
        disabled={readOnly || !admin}
        onValuesChange={() => setDirty(true)}
        onFinish={(values) => saveMutation.mutate(values)}
      >
        <div className="employee-version-editor-sections">
          <Card className="management-card employee-editor-card" title="角色指令" aria-label="角色指令">
            <Paragraph type="secondary">描述员工职责、边界和回答要求。</Paragraph>
            <Form.Item name="system_prompt" label="系统指令" rules={[{ required: true, message: '请输入系统指令' }]}>
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
                <Space wrap>
                  {effectiveTools.map((tool) => <Tag key={tool.id} color="green">可用 · {tool.name}</Tag>)}
                  {excludedTools.map((tool) => <Tag key={tool.id} color="orange">被策略排除 · {tool.name}</Tag>)}
                  {selectedTools.length === 0 && <Text type="secondary">选择 Skill 和 Tool 后显示结果</Text>}
                </Space>
              </div>
              <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/new`}>打开 Tool 管理</Link>
            </Card>
          </section>

          <Card className="management-card employee-editor-card" title="运行配置">
            <Paragraph type="secondary">先设置模型与审批策略；可选执行环境默认收起。</Paragraph>
            <div className="employee-runtime-primary-fields">
              <Form.Item name="execution_profile_id" label="ExecutionProfile" rules={[{ required: true, message: '请选择执行配置' }]}>
                <Select
                  options={(profiles.data?.items ?? []).map((profile: ExecutionProfileSummary) => ({
                    value: profile.id,
                    label: `${profile.engine_name} · ${profile.model_alias} · ${profile.status}`,
                    disabled: profile.status !== 'PUBLISHED',
                  }))}
                  placeholder="选择执行配置"
                />
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
            <Paragraph className="employee-profile-policy" type="secondary">
              Profile 工具策略：{selectedProfile?.tool_policy?.join('、') || '不额外限制'}
            </Paragraph>
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
        open={testOpen}
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
        open={publishOpen}
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
