import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  createSkill,
  createTool,
  forkSkillVersion,
  listSkills,
  listTools,
  publishSkillVersion,
  publishTool,
  updateTool,
} from '../../api/client'
import type { SkillSummary, ToolSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Text } = Typography

interface SkillFormValues {
  name: string
  skill_type: string
  instruction: string
  allowed_tool_ids: string[]
}

interface ToolFormValues {
  name: string
  description: string
  input_schema_json: string
}

const statusLabels: Record<string, string> = {
  DRAFT: '草稿',
  PUBLISHED: '已发布',
  ARCHIVED: '已归档',
}

const skillTypeLabels: Record<string, string> = {
  instruction: '指令型',
  'tool-bundle': '工具组合',
  workflow: '流程型',
}

function getStatusColor(status: string) {
  if (status === 'PUBLISHED') return 'green'
  if (status === 'DRAFT') return 'blue'
  return 'default'
}

/** 租户能力目录：集中管理 Skill 与 Tool 草稿，并保留发布资源的版本边界。 */
export function ResourceCatalogPanel() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const canManage = currentIdentity.roleName === '管理员'
  const [activeType, setActiveType] = useState<'skills' | 'tools'>('skills')
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>()
  const [skillDrawerOpen, setSkillDrawerOpen] = useState(false)
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false)
  const [editingTool, setEditingTool] = useState<ToolSummary>()
  const [viewingTool, setViewingTool] = useState<ToolSummary>()
  const [skillForm] = Form.useForm<SkillFormValues>()
  const [toolForm] = Form.useForm<ToolFormValues>()

  const skills = useQuery({
    queryKey: ['skills', fakeUser],
    queryFn: () => listSkills(fakeUser),
  })
  const tools = useQuery({
    queryKey: ['tools', fakeUser],
    queryFn: () => listTools(fakeUser),
  })

  const refreshSkills = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['skills', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['hub-publish-skills', fakeUser] }),
  ])
  const refreshTools = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['tools', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['hub-publish-tools', fakeUser] }),
  ])

  const createSkillMutation = useMutation({
    mutationFn: (values: SkillFormValues) => createSkill({
      name: values.name,
      skill_type: values.skill_type,
      instruction: values.instruction,
      allowed_tool_ids: values.allowed_tool_ids ?? [],
    }, fakeUser),
    onSuccess: async (created) => {
      await refreshSkills()
      setSkillDrawerOpen(false)
      skillForm.resetFields()
      message.success(`技能「${created.name}」草稿已创建`)
      navigate(`/enterprise/resources/skills/${encodeURIComponent(created.id)}/versions/${created.version}/files`)
    },
  })

  const createToolMutation = useMutation({
    mutationFn: (values: ToolFormValues) => createTool({
      name: values.name,
      description: values.description,
      input_schema: JSON.parse(values.input_schema_json) as Record<string, unknown>,
    }, fakeUser),
    onSuccess: async (created) => {
      await refreshTools()
      setToolDrawerOpen(false)
      toolForm.resetFields()
      message.success(`工具「${created.name}」草稿已创建`)
    },
  })

  const updateToolMutation = useMutation({
    mutationFn: (values: ToolFormValues) => updateTool(editingTool!.id, {
      name: values.name,
      description: values.description,
      input_schema: JSON.parse(values.input_schema_json) as Record<string, unknown>,
    }, fakeUser),
    onSuccess: async () => {
      await refreshTools()
      setToolDrawerOpen(false)
      setEditingTool(undefined)
      toolForm.resetFields()
      message.success('工具草稿已保存')
    },
  })

  const publishSkillMutation = useMutation({
    mutationFn: (skill: SkillSummary) => publishSkillVersion(skill.id, skill.version, fakeUser),
    onSuccess: async (published) => {
      await refreshSkills()
      message.success(`技能「${published.name}」v${published.version} 已发布`)
    },
  })

  const forkSkillMutation = useMutation({
    mutationFn: (skill: SkillSummary) => forkSkillVersion(skill.id, skill.version, fakeUser),
    onSuccess: async (draft) => {
      await refreshSkills()
      message.success(`已创建技能草稿 v${draft.version}`)
      navigate(`/enterprise/resources/skills/${encodeURIComponent(draft.id)}/versions/${draft.version}/files`)
    },
  })

  const publishToolMutation = useMutation({
    mutationFn: (tool: ToolSummary) => publishTool(tool.id, fakeUser),
    onSuccess: async (published) => {
      await refreshTools()
      message.success(`工具「${published.name}」已发布`)
    },
  })

  const matchingSkills = useMemo(() => (skills.data?.items ?? []).filter((skill) => {
    const matchesKeyword = `${skill.name} ${skill.instruction} ${skillTypeLabels[skill.skill_type] ?? skill.skill_type}`
      .toLocaleLowerCase().includes(keyword.trim().toLocaleLowerCase())
    return matchesKeyword && (!statusFilter || skill.status === statusFilter)
  }), [keyword, skills.data?.items, statusFilter])

  const matchingTools = useMemo(() => (tools.data?.items ?? []).filter((tool) => {
    const matchesKeyword = `${tool.name} ${tool.description}`
      .toLocaleLowerCase().includes(keyword.trim().toLocaleLowerCase())
    return matchesKeyword && (!statusFilter || tool.status === statusFilter)
  }), [keyword, statusFilter, tools.data?.items])

  const skillColumns = useMemo<ColumnsType<SkillSummary>>(() => [
    {
      title: '技能',
      key: 'skill',
      render: (_: unknown, skill) => (
        <div className="resource-catalog-name">
          <span className="resource-type-mark skill-mark" aria-hidden="true">S</span>
          <div>
            <Text strong>{skill.name}</Text>
            <Text type="secondary">{skillTypeLabels[skill.skill_type] ?? skill.skill_type} · {skill.content_format === 'FILE_PACKAGE' ? '文件包' : '指令内容'}</Text>
          </div>
        </div>
      ),
    },
    { title: '版本', dataIndex: 'version', key: 'version', render: (version: number) => `v${version}` },
    { title: '工具依赖', key: 'dependencies', render: (_: unknown, skill) => `${skill.allowed_tool_ids?.length ?? 0} 个工具` },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (status: string) => <Tag color={getStatusColor(status)}>{statusLabels[status] ?? status}</Tag>,
    },
    {
      title: '操作', key: 'actions',
      render: (_: unknown, skill) => (
        <Space wrap>
          {skill.status === 'DRAFT' && (
            <Button type="link" onClick={() => navigate(`/enterprise/resources/skills/${encodeURIComponent(skill.id)}/versions/${skill.version}/files`)}>
              {canManage ? '编辑内容' : '查看内容'}
            </Button>
          )}
          {canManage && skill.status === 'DRAFT' && (
            <Button
              type="link"
              loading={publishSkillMutation.isPending && publishSkillMutation.variables?.id === skill.id}
              onClick={() => publishSkillMutation.mutate(skill)}
            >发布</Button>
          )}
          {canManage && skill.status === 'PUBLISHED' && (
            <Button
              type="link"
              loading={forkSkillMutation.isPending && forkSkillMutation.variables?.id === skill.id}
              onClick={() => forkSkillMutation.mutate(skill)}
            >创建新草稿</Button>
          )}
        </Space>
      ),
    },
  ], [canManage, forkSkillMutation.isPending, forkSkillMutation.variables, navigate, publishSkillMutation.isPending, publishSkillMutation.variables])

  const toolColumns = useMemo<ColumnsType<ToolSummary>>(() => [
    {
      title: '工具',
      key: 'tool',
      render: (_: unknown, tool) => (
        <div className="resource-catalog-name">
          <span className="resource-type-mark tool-mark" aria-hidden="true">T</span>
          <div>
            <Text strong>{tool.name}</Text>
            <Text type="secondary">{tool.description || '暂无说明'}</Text>
          </div>
        </div>
      ),
    },
    { title: '版本', dataIndex: 'version', key: 'version', render: (version: number) => `v${version}` },
    {
      title: '凭据', key: 'credentials',
      render: (_: unknown, tool) => tool.requires_recipient_credentials
        ? (tool.credentials_configured ? '已配置' : <Text type="warning">待配置</Text>)
        : '无需凭据',
    },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (status: string) => <Tag color={getStatusColor(status)}>{statusLabels[status] ?? status}</Tag>,
    },
    {
      title: '操作', key: 'actions',
      render: (_: unknown, tool) => (
        <Space wrap>
          <Button type="link" onClick={() => setViewingTool(tool)}>查看</Button>
          {canManage && tool.status === 'DRAFT' && (
            <Button type="link" onClick={() => openEditTool(tool)}>编辑草稿</Button>
          )}
          {canManage && tool.status === 'DRAFT' && (
            <Button
              type="link"
              loading={publishToolMutation.isPending && publishToolMutation.variables?.id === tool.id}
              onClick={() => publishToolMutation.mutate(tool)}
            >发布</Button>
          )}
        </Space>
      ),
    },
  ], [canManage, publishToolMutation.isPending, publishToolMutation.variables])

  function openCreateSkill() {
    skillForm.resetFields()
    skillForm.setFieldsValue({ skill_type: 'instruction', allowed_tool_ids: [] })
    setSkillDrawerOpen(true)
  }

  function openCreateTool() {
    setEditingTool(undefined)
    toolForm.resetFields()
    toolForm.setFieldsValue({ name: '', description: '', input_schema_json: '{\n  "type": "object"\n}' })
    setToolDrawerOpen(true)
  }

  function openEditTool(tool: ToolSummary) {
    setEditingTool(tool)
    toolForm.setFieldsValue({
      name: tool.name,
      description: tool.description,
      input_schema_json: JSON.stringify(tool.input_schema, null, 2),
    })
    setToolDrawerOpen(true)
  }

  function submitTool() {
    void toolForm.validateFields().then((values) => {
      try {
        const schema: unknown = JSON.parse(values.input_schema_json)
        if (typeof schema !== 'object' || schema === null || Array.isArray(schema)) {
          throw new Error('JSON Schema 顶层必须是对象')
        }
      } catch (error) {
        message.error(`输入 Schema 格式不正确：${(error as Error).message}`)
        return
      }
      if (editingTool) updateToolMutation.mutate(values)
      else createToolMutation.mutate(values)
    })
  }

  const activeItems = activeType === 'skills' ? matchingSkills.length : matchingTools.length
  const queryError = activeType === 'skills' ? skills.error : tools.error
  const queryLoading = activeType === 'skills' ? skills.isLoading : tools.isLoading
  const mutationError = createSkillMutation.error
    ?? createToolMutation.error
    ?? updateToolMutation.error
    ?? publishSkillMutation.error
    ?? forkSkillMutation.error
    ?? publishToolMutation.error

  return (
    <div className="resource-catalog-panel">
      <div className="resource-catalog-toolbar">
        <div className="resource-catalog-tabs" role="group" aria-label="能力类型">
          <button
            className={activeType === 'skills' ? 'resource-type-tab selected' : 'resource-type-tab'}
            type="button"
            aria-pressed={activeType === 'skills'}
            onClick={() => setActiveType('skills')}
          >技能 <span>{skills.data?.items.length ?? 0}</span></button>
          <button
            className={activeType === 'tools' ? 'resource-type-tab selected' : 'resource-type-tab'}
            type="button"
            aria-pressed={activeType === 'tools'}
            onClick={() => setActiveType('tools')}
          >工具 <span>{tools.data?.items.length ?? 0}</span></button>
        </div>
        {canManage && (
          <Button type="primary" onClick={activeType === 'skills' ? openCreateSkill : openCreateTool}>
            新建{activeType === 'skills' ? '技能' : '工具'}
          </Button>
        )}
      </div>

      <div className="management-filter-row resource-catalog-filters">
        <Input.Search
          aria-label={activeType === 'skills' ? '搜索技能' : '搜索工具'}
          allowClear
          maxLength={128}
          placeholder={activeType === 'skills' ? '搜索技能名称或内容' : '搜索工具名称或说明'}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <Select
          aria-label="能力状态"
          allowClear
          placeholder="全部状态"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: 'DRAFT', label: '草稿' },
            { value: 'PUBLISHED', label: '已发布' },
          ]}
        />
        <Text className="management-stat">{activeItems} 项</Text>
      </div>

      {queryLoading && <div className="management-loading">正在读取{activeType === 'skills' ? '技能' : '工具'}目录…</div>}
      {queryError && <Alert type="error" showIcon message="能力目录加载失败" description={(queryError as Error).message} />}
      {mutationError && <Alert className="resource-catalog-error" type="error" showIcon message={(mutationError as Error).message} />}
      {activeType === 'skills' && skills.isSuccess && (
        matchingSkills.length > 0
          ? <Table<SkillSummary> rowKey="id" columns={skillColumns} dataSource={matchingSkills} pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 760 }} />
          : <Empty className="management-empty" description={keyword || statusFilter ? '没有符合条件的技能' : '还没有技能'}>
            {canManage && !keyword && !statusFilter && <Button type="primary" onClick={openCreateSkill}>新建技能</Button>}
          </Empty>
      )}
      {activeType === 'tools' && tools.isSuccess && (
        matchingTools.length > 0
          ? <Table<ToolSummary> rowKey="id" columns={toolColumns} dataSource={matchingTools} pagination={{ pageSize: 10, hideOnSinglePage: true }} scroll={{ x: 760 }} />
          : <Empty className="management-empty" description={keyword || statusFilter ? '没有符合条件的工具' : '还没有工具'}>
            {canManage && !keyword && !statusFilter && <Button type="primary" onClick={openCreateTool}>新建工具</Button>}
          </Empty>
      )}

      <Drawer
        className="resource-form-drawer"
        title="新建技能"
        open={skillDrawerOpen}
        onClose={() => setSkillDrawerOpen(false)}
        width={640}
        destroyOnClose
        footer={(
          <div className="resource-form-footer">
            <Button onClick={() => setSkillDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={createSkillMutation.isPending} onClick={() => skillForm.submit()}>
              创建草稿并编辑内容
            </Button>
          </div>
        )}
      >
        <Form
          form={skillForm}
          layout="vertical"
          onFinish={(values) => createSkillMutation.mutate(values)}
          initialValues={{ skill_type: 'instruction', allowed_tool_ids: [] }}
        >
          <section className="resource-form-section" aria-labelledby="skill-basic-heading">
            <h3 id="skill-basic-heading">基本信息</h3>
            <Form.Item name="name" label="技能名称" rules={[{ required: true, message: '请输入技能名称' }]}>
              <Input maxLength={128} placeholder="例如：工单分类规范" />
            </Form.Item>
            <Form.Item name="skill_type" label="技能类型" rules={[{ required: true, message: '请选择技能类型' }]}>
              <Select options={[
                { value: 'instruction', label: '指令型技能' },
                { value: 'tool-bundle', label: '工具组合技能' },
                { value: 'workflow', label: '流程型技能' },
              ]} />
            </Form.Item>
          </section>
          <section className="resource-form-section" aria-labelledby="skill-content-heading">
            <h3 id="skill-content-heading">指令内容</h3>
            <Form.Item name="instruction" label="初始指令" rules={[{ required: true, message: '请输入初始指令' }]}>
              <Input.TextArea rows={7} maxLength={65535} placeholder="描述技能要完成的工作、处理步骤和边界。创建后可在文件工作区继续编辑。" />
            </Form.Item>
          </section>
          <section className="resource-form-section" aria-labelledby="skill-dependency-heading">
            <h3 id="skill-dependency-heading">工具依赖</h3>
            <Form.Item name="allowed_tool_ids" label="可调用工具">
              <Select
                mode="multiple"
                allowClear
                loading={tools.isLoading}
                placeholder="选择已发布的工具，可稍后在员工版本中进一步配置"
                options={(tools.data?.items ?? [])
                  .filter((tool) => tool.status === 'PUBLISHED')
                  .map((tool) => ({ value: tool.id, label: `${tool.name} · v${tool.version}` }))}
              />
            </Form.Item>
            {tools.isError && <Alert type="warning" showIcon message="工具依赖列表暂不可用" description={(tools.error as Error).message} />}
          </section>
        </Form>
      </Drawer>

      <Drawer
        className="resource-form-drawer"
        title={editingTool ? `编辑工具草稿 · ${editingTool.name}` : '新建工具'}
        open={toolDrawerOpen}
        onClose={() => { setToolDrawerOpen(false); setEditingTool(undefined) }}
        width={640}
        destroyOnClose
        footer={(
          <div className="resource-form-footer">
            <Button onClick={() => { setToolDrawerOpen(false); setEditingTool(undefined) }}>取消</Button>
            <Button type="primary" loading={createToolMutation.isPending || updateToolMutation.isPending} onClick={submitTool}>
              {editingTool ? '保存草稿' : '创建工具草稿'}
            </Button>
          </div>
        )}
      >
        <Form form={toolForm} layout="vertical">
          <section className="resource-form-section" aria-labelledby="tool-basic-heading">
            <h3 id="tool-basic-heading">基本信息</h3>
            <Form.Item name="name" label="工具名称" rules={[{ required: true, message: '请输入工具名称' }]}>
              <Input maxLength={128} placeholder="例如：查询客户订单" />
            </Form.Item>
            <Form.Item name="description" label="用途说明">
              <Input.TextArea rows={3} maxLength={1024} placeholder="说明工具何时使用以及它会返回什么。" />
            </Form.Item>
          </section>
          <section className="resource-form-section" aria-labelledby="tool-contract-heading">
            <h3 id="tool-contract-heading">调用输入</h3>
            <Form.Item
              name="input_schema_json"
              label="输入 Schema（JSON）"
              extra="定义工具接收的字段；顶层必须是 object。"
              rules={[{ required: true, message: '请输入输入 Schema' }]}
            >
              <Input.TextArea rows={11} spellCheck={false} className="resource-schema-input" />
            </Form.Item>
          </section>
        </Form>
      </Drawer>

      <Drawer
        className="resource-form-drawer"
        title="工具详情"
        open={Boolean(viewingTool)}
        onClose={() => setViewingTool(undefined)}
        width={560}
      >
        {viewingTool && (
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="名称">{viewingTool.name}</Descriptions.Item>
            <Descriptions.Item label="版本">v{viewingTool.version}</Descriptions.Item>
            <Descriptions.Item label="状态">{statusLabels[viewingTool.status] ?? viewingTool.status}</Descriptions.Item>
            <Descriptions.Item label="说明">{viewingTool.description || '暂无说明'}</Descriptions.Item>
            <Descriptions.Item label="凭据">{viewingTool.requires_recipient_credentials
              ? (viewingTool.credentials_configured ? '接收方凭据已配置' : '需要配置接收方凭据')
              : '无需凭据'}</Descriptions.Item>
            <Descriptions.Item label="输入 Schema">
              <pre className="prompt-preview">{JSON.stringify(viewingTool.input_schema, null, 2)}</pre>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  )
}
