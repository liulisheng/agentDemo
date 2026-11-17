import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  archiveDigitalEmployee,
  createEmployeeVersion,
  forkEmployeeVersion,
  getDigitalEmployee,
  listEmployeeVersions,
  listSkills,
  listTools,
  patchDigitalEmployee,
  replaceEmployeeSkillBindings,
  replaceEmployeeToolBindings,
} from '../../api/client'
import type { DigitalEmployeeDetail, EmployeeVersion, SkillSummary, ToolSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'
import { EmployeeResourcePickerDrawer } from './EmployeeResourcePickerDrawer'
import { EmployeeVersionLifecycleActions, employeeVersionStatusLabels } from './EmployeeVersionEditorPage'

const { Title, Text, Paragraph } = Typography

type WorkspaceTab = 'overview' | 'skills' | 'tools' | 'versions'

/** 员工工作台：概览、按类型分区的能力资源、版本发布。 */
export function EmployeeDetailPage() {
  const { employeeId = '' } = useParams()
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const canEdit = currentIdentity.roleName === '管理员'
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('overview')
  const [editOpen, setEditOpen] = useState(false)
  const [createVersionOpen, setCreateVersionOpen] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [resourcePickerKind, setResourcePickerKind] = useState<'skill' | 'tool'>()
  const [resourcePickerVersion, setResourcePickerVersion] = useState<number | null>(null)
  const [forkKind, setForkKind] = useState<'skill' | 'tool'>()
  const [editForm] = Form.useForm<Pick<DigitalEmployeeDetail, 'name' | 'role_name' | 'description'>>()
  const [versionForm] = Form.useForm<{ system_prompt: string }>()

  const employee = useQuery({
    queryKey: ['digital-employee', fakeUser, employeeId],
    queryFn: () => getDigitalEmployee(employeeId, fakeUser),
    enabled: Boolean(employeeId),
  })
  const versions = useQuery({
    queryKey: ['employee-versions', fakeUser, employeeId, 'all'],
    queryFn: () => listEmployeeVersions(employeeId, { limit: 100 }, fakeUser),
    enabled: Boolean(employeeId),
  })
  const skills = useQuery({
    queryKey: ['skills', fakeUser],
    queryFn: () => listSkills(fakeUser, true),
    enabled: activeTab === 'skills',
  })
  const tools = useQuery({
    queryKey: ['tools', fakeUser],
    queryFn: () => listTools(fakeUser),
    enabled: activeTab === 'tools',
  })

  useEffect(() => {
    if (employee.data && editOpen) {
      editForm.setFieldsValue({
        name: employee.data.name,
        role_name: employee.data.role_name,
        description: employee.data.description,
      })
    }
  }, [employee.data, editForm, editOpen])

  const invalidateEmployee = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['digital-employees', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['digital-employee', fakeUser, employeeId] }),
      queryClient.invalidateQueries({ queryKey: ['employee-versions', fakeUser, employeeId] }),
    ])
  }

  const updateMutation = useMutation({
    mutationFn: (values: Partial<Pick<DigitalEmployeeDetail, 'name' | 'role_name' | 'description'>>) =>
      patchDigitalEmployee(employeeId, values, fakeUser),
    onSuccess: async () => {
      await invalidateEmployee()
      setEditOpen(false)
      message.success('员工资料已保存')
    },
  })

  const archiveMutation = useMutation({
    mutationFn: () => archiveDigitalEmployee(employeeId, fakeUser),
    onSuccess: async () => {
      await invalidateEmployee()
      message.success('员工已归档')
      navigate('/enterprise/employees')
    },
  })

  const createVersionMutation = useMutation({
    mutationFn: (systemPrompt: string) => createEmployeeVersion(
      employeeId,
      { system_prompt: systemPrompt, skill_ids: [], tool_ids: [] },
      fakeUser,
    ),
    onSuccess: async (created) => {
      await invalidateEmployee()
      setCreateVersionOpen(false)
      versionForm.resetFields()
      message.success(`草稿版本 v${created.version} 已创建`)
      navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/versions/${created.version}`)
    },
  })

  const forkVersionMutation = useMutation({
    mutationFn: (input: { sourceVersion: number; kind: 'skill' | 'tool' }) =>
      forkEmployeeVersion(employeeId, input.sourceVersion, fakeUser),
    onSuccess: async (created, input) => {
      await invalidateEmployee()
      setSelectedVersion(created.version)
      setResourcePickerVersion(created.version)
      setResourcePickerKind(input.kind)
      setForkKind(undefined)
      message.success(`已从 v${input.sourceVersion} 创建完整继承配置的草稿 v${created.version}`)
    },
    onError: (error) => {
      setForkKind(undefined)
      message.error((error as Error).message)
    },
  })

  const replaceBindingsMutation = useMutation({
    mutationFn: async (input: {
      kind: 'skill' | 'tool'
      version: number
      refs: { resource_id: string; version: number }[]
    }) => input.kind === 'skill'
      ? replaceEmployeeSkillBindings(employeeId, input.version, input.refs, fakeUser)
      : replaceEmployeeToolBindings(employeeId, input.version, input.refs, fakeUser),
    onSuccess: async (_, input) => {
      await invalidateEmployee()
      setResourcePickerKind(undefined)
      setResourcePickerVersion(null)
      message.success(input.kind === 'skill' ? 'Skill 绑定已更新' : 'Tool 绑定已更新')
    },
  })

  const orderedVersions = useMemo(
    () => [...(versions.data?.items ?? [])].sort((left, right) => right.version - left.version),
    [versions.data?.items],
  )
  const selected = orderedVersions.find((item) => item.version === selectedVersion) ?? orderedVersions[0]
  const employeeIsArchived = employee.data?.status === 'ARCHIVED'
  const canConfigure = canEdit && !employeeIsArchived
  const openEditor = (version: number) => {
    navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/versions/${version}`)
  }
  const openResourcePicker = (kind: 'skill' | 'tool') => {
    if (!selected) return
    if (selected.status === 'DRAFT') {
      setResourcePickerVersion(selected.version)
      setResourcePickerKind(kind)
      return
    }
    if (selected.status === 'PUBLISHED') {
      setForkKind(kind)
      forkVersionMutation.mutate({ sourceVersion: selected.version, kind })
      return
    }
    message.warning('此版本不能直接编辑；请从已发布版本创建新的员工草稿。')
  }

  const saveResourceBindings = async (
    kind: 'skill' | 'tool',
    version: number,
    addedRefs: { resource_id: string; version: number }[],
  ) => {
    const sourceVersion = orderedVersions.find((item) => item.version === version)
    const existingRefs = kind === 'skill'
      ? sourceVersion?.skill_version_refs ?? []
      : sourceVersion?.tool_version_refs ?? []
    const refs = [
      ...existingRefs.map((item) => ({
        resource_id: item.skill_id ?? item.tool_id ?? item.resource_id ?? '',
        version: item.version,
      })),
      ...addedRefs,
    ].filter((item) => item.resource_id)
    await replaceBindingsMutation.mutateAsync({ kind, version, refs })
  }

  const removeResourceBinding = (kind: 'skill' | 'tool', resourceId: string, resourceVersion: number) => {
    if (!selected || selected.status !== 'DRAFT') return
    const refs = (kind === 'skill' ? selected.skill_version_refs : selected.tool_version_refs)
      .filter((item) => (item.skill_id ?? item.tool_id ?? item.resource_id) !== resourceId || item.version !== resourceVersion)
      .map((item) => ({
        resource_id: item.skill_id ?? item.tool_id ?? item.resource_id ?? '',
        version: item.version,
      }))
      .filter((item) => item.resource_id)
    replaceBindingsMutation.mutate({ kind, version: selected.version, refs })
  }
  const skillForRef = (ref: EmployeeVersion['skill_version_refs'][number]) => (
    (skills.data?.items ?? []).find((item: SkillSummary) => item.id === (ref.skill_id ?? ref.resource_id) && item.version === ref.version)
  )
  const toolForRef = (ref: EmployeeVersion['tool_version_refs'][number]) => (
    (tools.data?.items ?? []).find((item: ToolSummary) => item.id === (ref.tool_id ?? ref.resource_id) && item.version === ref.version)
  )
  const submitEdit = () => editForm.submit()
  const submitVersion = () => versionForm.submit()

  if (employee.isLoading) {
    return <div className="management-loading"><Spin /> 正在加载员工详情…</div>
  }
  if (employee.isError || !employee.data) {
    return (
      <Alert
        type="error"
        showIcon
        message="员工详情加载失败"
        description={(employee.error as Error | undefined)?.message ?? '员工不存在或无权访问'}
        action={<Button onClick={() => navigate('/enterprise/employees')}>返回员工列表</Button>}
      />
    )
  }

  const data = employee.data

  return (
    <section className="management-page employee-workspace" aria-label="数字员工工作台">
      <div className="employee-workspace-header">
        <div className="employee-workspace-identity">
          <Link to="/enterprise/employees" className="employee-back-link">← 数字员工</Link>
          <Space align="center" size={12} wrap>
            <Title level={2} className="employee-workspace-title">{data.name}</Title>
            <Tag color={data.status === 'ARCHIVED' ? 'red' : data.status === 'ACTIVE' ? 'green' : 'blue'}>
              {data.status}
            </Tag>
          </Space>
          <Paragraph type="secondary" className="employee-workspace-subtitle">
            {data.role_name}<span className="employee-subtitle-separator">·</span>{data.description}
          </Paragraph>
        </div>
        {canConfigure && (
          <Space wrap>
            <Button onClick={() => setEditOpen(true)}>编辑资料</Button>
            <Popconfirm
              title="归档这个数字员工？"
              description="归档后不能新建版本或部署，历史版本和运行记录保留。"
              okText="确认归档"
              cancelText="取消"
              onConfirm={() => archiveMutation.mutate()}
            >
              <Button danger loading={archiveMutation.isPending}>归档</Button>
            </Popconfirm>
          </Space>
        )}
      </div>

      <Tabs
        className="employee-workspace-tabs"
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as WorkspaceTab)}
        items={[
          {
            key: 'overview',
            label: '概览',
            children: (
              <div className="employee-overview-layout">
                <Card className="management-card employee-overview-card" title="员工概览">
                  <Descriptions column={2} size="middle">
                    <Descriptions.Item label="员工名称">{data.name}</Descriptions.Item>
                    <Descriptions.Item label="员工类型">{data.role_name}</Descriptions.Item>
                    <Descriptions.Item label="员工 ID"><Text copyable>{data.id}</Text></Descriptions.Item>
                    <Descriptions.Item label="状态">
                      <Tag color={data.status === 'ARCHIVED' ? 'red' : data.status === 'ACTIVE' ? 'green' : 'blue'}>{data.status}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="用途说明" span={2}>{data.description}</Descriptions.Item>
                  </Descriptions>
                  <div className="employee-overview-footer">
                    <span><Text type="secondary">版本数量</Text><strong>{data.version_count}</strong></span>
                    <span><Text type="secondary">最新发布版本</Text><strong>{data.latest_published_version ? `v${data.latest_published_version.version}` : '尚未发布'}</strong></span>
                    <span><Text type="secondary">当前草稿</Text><strong>{selected?.status === 'DRAFT' ? `v${selected.version}` : '无'}</strong></span>
                  </div>
                </Card>
              </div>
            ),
          },
          {
            key: 'skills',
            label: `Skills${selected ? ` (${selected.skill_version_refs.length})` : ''}`,
            children: (
              <div className="employee-resource-tab">
                <div className="employee-resource-toolbar">
                  <div>
                    <Title level={3}>Skills</Title>
                    <Text type="secondary">只展示当前员工版本绑定的 Skills；每项使用精确版本。</Text>
                  </div>
                  <Space wrap>
                    {orderedVersions.length > 0 && (
                      <Select
                        aria-label="Skill 所属员工版本"
                        value={selected?.version}
                        onChange={setSelectedVersion}
                        options={orderedVersions.map((item) => ({ value: item.version, label: `v${item.version} · ${employeeVersionStatusLabels[item.status] ?? item.status}` }))}
                        style={{ minWidth: 190 }}
                      />
                    )}
                    {canConfigure && selected && selected.status !== 'DEPRECATED' && (
                      <Button type="primary" loading={forkVersionMutation.isPending && forkKind === 'skill'} onClick={() => openResourcePicker('skill')}>
                        添加 Skill
                      </Button>
                    )}
                  </Space>
                </div>
                {!selected && (
                  <Card className="management-card employee-empty-workspace">
                    <Empty description="先创建员工草稿版本，再添加 Skills。">
                      {canConfigure && <Button type="primary" onClick={() => setCreateVersionOpen(true)}>新建草稿版本</Button>}
                    </Empty>
                  </Card>
                )}
                {selected && (
                  <section className="employee-resource-section" aria-label="Skills">
                    <Card className="management-card employee-resource-card" title={`Skills · v${selected.version}`}>
                      {selected.skill_version_refs.length === 0
                        ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此版本还没有绑定 Skill" />
                        : <div className="employee-resource-list">
                          {selected.skill_version_refs.map((ref) => {
                            const id = ref.skill_id ?? ref.resource_id ?? ''
                            const skill = skillForRef(ref)
                            return (
                              <article className="employee-resource-item" key={`${id}:${ref.version}`}>
                                <div className="employee-resource-item-main">
                                  <span className="employee-resource-type-icon skill-icon">S</span>
                                  <div>
                                    <Text strong>{skill?.name ?? `Skill ${id.slice(0, 8)}`}</Text>
                                    <div className="employee-resource-meta">v{ref.version}<span>·</span>{skill?.status ?? '固定引用'}</div>
                                  </div>
                                </div>
                                <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/skills/${encodeURIComponent(id)}/versions/${ref.version}/files`}>打开文件工作区</Link>
                                {canConfigure && selected.status === 'DRAFT' && (
                                  <Popconfirm
                                    title="从当前员工版本移除这个 Skill？"
                                    description="只修改此 DRAFT 的 Skill 绑定，不会删除 Skill 资源。"
                                    okText="确认移除"
                                    cancelText="取消"
                                    onConfirm={() => removeResourceBinding('skill', id, ref.version)}
                                  >
                                    <Button type="link" danger loading={replaceBindingsMutation.isPending}>移除</Button>
                                  </Popconfirm>
                                )}
                              </article>
                            )
                          })}
                        </div>}
                    </Card>
                  </section>
                )}
                {skills.isError && <Alert type="error" showIcon message="Skill 目录读取失败" description={(skills.error as Error).message} />}
              </div>
            ),
          },
          {
            key: 'tools',
            label: `Tools${selected ? ` (${selected.tool_version_refs.length})` : ''}`,
            children: (
              <div className="employee-resource-tab">
                <div className="employee-resource-toolbar">
                  <div>
                    <Title level={3}>Tools</Title>
                    <Text type="secondary">只展示当前员工版本允许使用的 Tools；有效范围仍受 Skill 和执行策略约束。</Text>
                  </div>
                  <Space wrap>
                    {orderedVersions.length > 0 && (
                      <Select
                        aria-label="Tool 所属员工版本"
                        value={selected?.version}
                        onChange={setSelectedVersion}
                        options={orderedVersions.map((item) => ({ value: item.version, label: `v${item.version} · ${employeeVersionStatusLabels[item.status] ?? item.status}` }))}
                        style={{ minWidth: 190 }}
                      />
                    )}
                    {canConfigure && (
                      <Button onClick={() => navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/new`)}>管理 Tools</Button>
                    )}
                    {canConfigure && selected && selected.status !== 'DEPRECATED' && (
                      <Button type="primary" loading={forkVersionMutation.isPending && forkKind === 'tool'} onClick={() => openResourcePicker('tool')}>
                        绑定 Tool
                      </Button>
                    )}
                  </Space>
                </div>
                {!selected && (
                  <Card className="management-card employee-empty-workspace">
                    <Empty description="先创建员工草稿版本，再绑定 Tools。">
                      {canConfigure && <Button type="primary" onClick={() => setCreateVersionOpen(true)}>新建草稿版本</Button>}
                    </Empty>
                  </Card>
                )}
                {selected && (
                  <section className="employee-resource-section" aria-label="Tools">
                    <Card className="management-card employee-resource-card" title={`Tools · v${selected.version}`}>
                      {selected.tool_version_refs.length === 0
                        ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此版本还没有绑定 Tool" />
                        : <div className="employee-resource-list">
                          {selected.tool_version_refs.map((ref) => {
                            const id = ref.tool_id ?? ref.resource_id ?? ''
                            const tool = toolForRef(ref)
                            return (
                              <article className="employee-resource-item" key={`${id}:${ref.version}`}>
                                <div className="employee-resource-item-main">
                                  <span className="employee-resource-type-icon tool-icon">T</span>
                                  <div>
                                    <Text strong>{tool?.name ?? `Tool ${id.slice(0, 8)}`}</Text>
                                    <div className="employee-resource-meta">v{ref.version}<span>·</span>{tool?.status ?? '固定引用'}</div>
                                  </div>
                                </div>
                                <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/${encodeURIComponent(id)}`}>Tool 详情</Link>
                                {canConfigure && selected.status === 'DRAFT' && (
                                  <Popconfirm
                                    title="从当前员工版本移除这个 Tool？"
                                    description="只修改此 DRAFT 的 Tool 绑定，不会删除 Tool 资源。"
                                    okText="确认移除"
                                    cancelText="取消"
                                    onConfirm={() => removeResourceBinding('tool', id, ref.version)}
                                  >
                                    <Button type="link" danger loading={replaceBindingsMutation.isPending}>移除</Button>
                                  </Popconfirm>
                                )}
                              </article>
                            )
                          })}
                        </div>}
                    </Card>
                  </section>
                )}
                {tools.isError && <Alert type="error" showIcon message="Tool 目录读取失败" description={(tools.error as Error).message} />}
              </div>
            ),
          },
          {
            key: 'versions',
            label: '版本与发布',
            children: (
              <div className="employee-version-workspace">
                <div className="employee-version-workspace-header">
                  <div>
                    <Title level={3}>版本与发布</Title>
                    <Paragraph type="secondary">每个版本固定自己的 Skills、Tools 和运行配置。</Paragraph>
                  </div>
                  {canConfigure && <Button type="primary" onClick={() => setCreateVersionOpen(true)}>新建空白草稿</Button>}
                </div>
                {!canEdit && (
                  <Alert type="info" showIcon message={`当前身份：${currentIdentity.label}`} description="成员可查看配置。版本变更需由租户管理员执行。" />
                )}
                {employeeIsArchived && <Alert type="info" showIcon message="员工已归档，版本配置仅供查看。" />}
                {versions.isLoading && <div className="management-loading"><Spin /> 正在加载版本…</div>}
                {versions.isError && <Alert type="error" showIcon message={(versions.error as Error).message} />}
                {versions.isSuccess && orderedVersions.length === 0 && <Empty description="还没有版本。" />}
                <div className="employee-version-card-list">
                  {orderedVersions.map((item) => (
                    <Card className="management-card employee-version-card" key={item.version}>
                      <div className="employee-version-card-header">
                        <Space size={12} wrap>
                          <Title level={4}>版本 v{item.version}</Title>
                          <Tag color={item.status === 'PUBLISHED' ? 'green' : item.status === 'DRAFT' ? 'blue' : 'default'}>{employeeVersionStatusLabels[item.status] ?? item.status}</Tag>
                        </Space>
                        <div className="employee-version-actions">
                          <Button type="link" onClick={() => { setSelectedVersion(item.version); setActiveTab('skills') }}>查看 Skills</Button>
                          <Button type="link" onClick={() => { setSelectedVersion(item.version); setActiveTab('tools') }}>查看 Tools</Button>
                          {canConfigure && item.status === 'DRAFT' ? (
                            <Button type="primary" onClick={() => openEditor(item.version)}>编辑草稿</Button>
                          ) : (
                            <Button onClick={() => openEditor(item.version)}>查看配置</Button>
                          )}
                          {canConfigure && (item.status === 'PUBLISHED' || item.status === 'DEPRECATED') && (
                            <EmployeeVersionLifecycleActions
                              key={`${fakeUser}:${employeeId}:${item.version}`}
                              employeeId={employeeId}
                              version={item}
                            />
                          )}
                        </div>
                      </div>
                      <div className="employee-version-summary">
                        <span>{item.skill_version_refs.length} Skills</span>
                        <span>{item.tool_version_refs.length} Tools</span>
                        <span>{item.execution_profile_id} · v{item.execution_profile_version}</span>
                        <span>{item.model_route_id ? `ModelRoute ${item.model_route_id}` : '默认模型路由'}</span>
                      </div>
                    </Card>
                  ))}
                </div>
              </div>
            ),
          },
        ]}
      />

      <Modal
        title="编辑员工基本信息"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={submitEdit}
        okText="保存修改"
        cancelText="取消"
        confirmLoading={updateMutation.isPending}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" onFinish={(values) => updateMutation.mutate(values)}>
          <Form.Item name="name" label="员工名称" rules={[{ required: true, message: '请输入员工名称' }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item name="role_name" label="员工类型" rules={[{ required: true, message: '请输入员工类型' }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item
            name="description"
            label="员工描述"
            extra="用于员工目录和详情说明，不会作为模型运行指令。"
            rules={[{ required: true, message: '请输入员工描述' }]}
          >
            <Input.TextArea maxLength={1024} rows={3} />
          </Form.Item>
          {updateMutation.isError && <Alert type="error" message={(updateMutation.error as Error).message} />}
        </Form>
      </Modal>

      <Modal
        title="新建草稿版本"
        open={createVersionOpen}
        onCancel={() => setCreateVersionOpen(false)}
        onOk={submitVersion}
        okText="创建并配置"
        cancelText="取消"
        confirmLoading={createVersionMutation.isPending}
        destroyOnHidden
      >
        <Paragraph type="secondary">创建后立即进入版本配置，可分别设置 Skills、Tools 和运行环境。</Paragraph>
        <Form form={versionForm} layout="vertical" onFinish={(values) => createVersionMutation.mutate(values.system_prompt)}>
          <Form.Item
            name="system_prompt"
            label="系统指令"
            extra="定义员工每次处理请求时都遵守的角色、规则和边界。"
            rules={[{ required: true, message: '请输入系统指令' }]}
          >
            <Input.TextArea rows={5} maxLength={65535} placeholder="定义员工的职责、边界和回答要求" />
          </Form.Item>
          {createVersionMutation.isError && <Alert type="error" message={(createVersionMutation.error as Error).message} />}
        </Form>
      </Modal>

      {resourcePickerKind && resourcePickerVersion !== null && selected && (
        <EmployeeResourcePickerDrawer
          open
          kind={resourcePickerKind}
          employeeName={data.name}
          version={resourcePickerVersion}
          fakeUser={fakeUser}
          boundRefs={resourcePickerKind === 'skill' ? selected.skill_version_refs : selected.tool_version_refs}
          saving={replaceBindingsMutation.isPending}
          onCancel={() => {
            setResourcePickerKind(undefined)
            setResourcePickerVersion(null)
          }}
          onCreate={() => navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/skills/new`)}
          onConfirm={(refs) => saveResourceBindings(resourcePickerKind, resourcePickerVersion, refs)}
        />
      )}
    </section>
  )
}
