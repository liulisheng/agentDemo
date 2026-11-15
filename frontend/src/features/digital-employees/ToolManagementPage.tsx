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
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { createTool, listTools, publishTool, updateTool } from '../../api/client'
import type { ToolSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Paragraph, Text, Title } = Typography

interface ToolFormValues {
  name: string
  description: string
  input_schema_json: string
}

/** 员工上下文中的 Tool 目录：创建 DRAFT、编辑 DRAFT 并发布可绑定版本。 */
export function ToolManagementPage() {
  const { employeeId = '', toolId = 'new' } = useParams()
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const canManage = currentIdentity.roleName === '管理员'
  const [modalOpen, setModalOpen] = useState(false)
  const [editingTool, setEditingTool] = useState<ToolSummary>()
  const [form] = Form.useForm<ToolFormValues>()

  const tools = useQuery({
    queryKey: ['tools', fakeUser],
    queryFn: () => listTools(fakeUser),
  })
  const items = tools.data?.items ?? []
  const selectedTool = items.find((item) => item.id === toolId)

  const openCreate = () => {
    setEditingTool(undefined)
    form.resetFields()
    form.setFieldsValue({
      name: '',
      description: '',
      input_schema_json: '{\n  "type": "object"\n}',
    })
    setModalOpen(true)
  }

  const openEdit = (tool: ToolSummary) => {
    setEditingTool(tool)
    form.setFieldsValue({
      name: tool.name,
      description: tool.description,
      input_schema_json: JSON.stringify(tool.input_schema, null, 2),
    })
    setModalOpen(true)
  }

  const refreshTools = () => queryClient.invalidateQueries({ queryKey: ['tools', fakeUser] })
  const createMutation = useMutation({
    mutationFn: (value: ToolFormValues) => createTool({
      name: value.name,
      description: value.description,
      input_schema: JSON.parse(value.input_schema_json) as Record<string, unknown>,
    }, fakeUser),
    onSuccess: async (created) => {
      await refreshTools()
      setModalOpen(false)
      message.success(`Tool「${created.name}」已创建为 DRAFT`)
    },
  })
  const updateMutation = useMutation({
    mutationFn: (value: ToolFormValues) => updateTool(editingTool!.id, {
      name: value.name,
      description: value.description,
      input_schema: JSON.parse(value.input_schema_json) as Record<string, unknown>,
    }, fakeUser),
    onSuccess: async () => {
      await refreshTools()
      setModalOpen(false)
      setEditingTool(undefined)
      message.success('Tool 草稿已保存')
    },
  })
  const publishMutation = useMutation({
    mutationFn: (id: string) => publishTool(id, fakeUser),
    onSuccess: async (published) => {
      await refreshTools()
      message.success(`Tool「${published.name}」已发布，可绑定到员工草稿`)
    },
  })

  const columns = useMemo<ColumnsType<ToolSummary>>(() => [
    { title: 'Tool 名称', dataIndex: 'name', key: 'name' },
    { title: '协议', dataIndex: 'protocol', key: 'protocol' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (value: string) => (
      <Tag color={value === 'PUBLISHED' ? 'green' : 'blue'}>{value}</Tag>
    ) },
    {
      title: '凭据',
      key: 'credentials',
      render: (_: unknown, item) => item.requires_recipient_credentials
        ? (item.credentials_configured ? '已配置接收方凭据' : <Text type="warning">待配置接收方凭据</Text>)
        : '无需凭据',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, item) => (
        <Space>
          <Button type="link" onClick={() => openEdit(item)} disabled={!canManage || item.status !== 'DRAFT'}>编辑草稿</Button>
          {item.status === 'DRAFT' && canManage && (
            <Button
              type="link"
              loading={publishMutation.isPending && publishMutation.variables === item.id}
              onClick={() => publishMutation.mutate(item.id)}
            >发布</Button>
          )}
        </Space>
      ),
    },
  ], [canManage, publishMutation.isPending, publishMutation.variables])

  const submit = () => {
    void form.validateFields().then((values) => {
      try {
        const schema: unknown = JSON.parse(values.input_schema_json)
        if (typeof schema !== 'object' || schema === null || Array.isArray(schema)) {
          throw new Error('JSON Schema 顶层必须是对象')
        }
      } catch (error) {
        message.error(`JSON Schema 格式不正确：${(error as Error).message}`)
        return
      }
      if (editingTool) updateMutation.mutate(values)
      else createMutation.mutate(values)
    })
  }

  const savePending = createMutation.isPending || updateMutation.isPending

  return (
    <section className="management-page" aria-label="Tool 管理">
      <div className="management-page-header">
        <div>
          <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}`}>← 返回员工详情</Link>
          <Text className="management-eyebrow">员工上下文 / Tool 管理</Text>
          <Title level={2}>Tool 管理</Title>
          <Paragraph type="secondary">管理当前租户 Tool。只有 PUBLISHED 版本可以绑定到员工；已发布版本不可原地修改。</Paragraph>
        </div>
        {canManage && <Button type="primary" onClick={openCreate}>新建 Tool</Button>}
      </div>

      {tools.isError && <Alert type="error" showIcon message={(tools.error as Error).message} />}
      {tools.isLoading && <div className="management-loading">正在加载 Tool…</div>}
      {tools.isSuccess && items.length === 0 && (
        <Card className="management-card">
          <Empty description="还没有 Tool；先创建并发布一个 Tool，再回到员工版本配置进行绑定。">
            {canManage && <Button type="primary" onClick={openCreate}>新建 Tool</Button>}
          </Empty>
        </Card>
      )}
      {items.length > 0 && (
        <Card className="management-card" title="当前租户 Tool">
          <Table<ToolSummary>
            rowKey="id"
            columns={columns}
            dataSource={items}
            pagination={false}
            rowClassName={(item) => item.id === selectedTool?.id ? 'ant-table-row-selected' : ''}
          />
        </Card>
      )}
      {selectedTool && (
        <Card className="management-card" title={`Tool 详情 · ${selectedTool.name}`}>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="Tool ID"><Text copyable>{selectedTool.id}</Text></Descriptions.Item>
            <Descriptions.Item label="版本">v{selectedTool.version}</Descriptions.Item>
            <Descriptions.Item label="状态">{selectedTool.status}</Descriptions.Item>
            <Descriptions.Item label="协议">{selectedTool.protocol}</Descriptions.Item>
            <Descriptions.Item label="描述" span={2}>{selectedTool.description || '—'}</Descriptions.Item>
          </Descriptions>
          <Paragraph strong>输入 JSON Schema</Paragraph>
          <pre className="prompt-preview">{JSON.stringify(selectedTool.input_schema, null, 2)}</pre>
        </Card>
      )}
      {(createMutation.isError || updateMutation.isError || publishMutation.isError) && (
        <Alert
          type="error"
          showIcon
          message={(createMutation.error ?? updateMutation.error ?? publishMutation.error as Error).message}
        />
      )}

      <Modal
        title={editingTool ? `编辑 DRAFT Tool · ${editingTool.name}` : '新建 Tool'}
        open={modalOpen}
        onCancel={() => { setModalOpen(false); setEditingTool(undefined) }}
        onOk={submit}
        okText={editingTool ? '保存草稿' : '创建 Tool'}
        cancelText="取消"
        confirmLoading={savePending}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Tool 名称" rules={[{ required: true, message: '请输入 Tool 名称' }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item name="description" label="Tool 描述">
            <Input.TextArea rows={2} maxLength={1024} />
          </Form.Item>
          <Form.Item
            name="input_schema_json"
            label="输入 JSON Schema"
            rules={[{ required: true, message: '请输入 JSON Schema' }]}
          >
            <Input.TextArea rows={8} spellCheck={false} />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  )
}
