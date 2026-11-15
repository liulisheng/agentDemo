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
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  archiveDigitalEmployee,
  createEmployeeVersion,
  getDigitalEmployee,
  listEmployeeVersions,
  patchDigitalEmployee,
} from '../../api/client'
import type { DigitalEmployeeDetail, EmployeeVersion } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Title, Text, Paragraph } = Typography

/** 员工基本资料、版本时间线和精确 Skill/Tool 引用的详情入口。 */
export function EmployeeDetailPage() {
  const { employeeId = '' } = useParams()
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const canEdit = currentIdentity.roleName === '管理员'
  const [editOpen, setEditOpen] = useState(false)
  const [createVersionOpen, setCreateVersionOpen] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
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
      setSelectedVersion(created.version)
      message.success(`草稿版本 v${created.version} 已创建`)
    },
  })

  const orderedVersions = useMemo(
    () => [...(versions.data?.items ?? [])].sort((left, right) => right.version - left.version),
    [versions.data?.items],
  )
  const selected = orderedVersions.find((item) => item.version === selectedVersion)
    ?? orderedVersions[0]
  const versionColumns = useMemo<ColumnsType<EmployeeVersion>>(() => [
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 90,
      render: (value: number) => <Button type="link" onClick={() => setSelectedVersion(value)}>v{value}</Button>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 130,
      render: (value: string) => <Tag color={value === 'PUBLISHED' ? 'green' : value === 'DRAFT' ? 'blue' : 'default'}>{value}</Tag>,
    },
    {
      title: 'Skill / Tool',
      key: 'dependencies',
      render: (_: unknown, item) => `${item.skill_version_refs.length} Skill / ${item.tool_version_refs.length} Tool`,
    },
    {
      title: '',
      key: 'actions',
      width: 120,
      render: (_: unknown, item) => (
        item.status === 'DRAFT'
          ? <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/versions/${item.version}`}>编辑草稿</Link>
          : <Button type="link" onClick={() => setSelectedVersion(item.version)}>查看快照</Button>
      ),
    },
  ], [employeeId])

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
  const employeeIsArchived = data.status === 'ARCHIVED'
  const submitEdit = () => editForm.submit()
  const submitVersion = () => versionForm.submit()

  return (
    <section className="management-page" aria-label="员工详情">
      <div className="management-page-header">
        <div>
          <Link to="/enterprise/employees">← 返回员工列表</Link>
          <Text className="management-eyebrow">员工管理 / 员工详情</Text>
          <Space align="center" size={12}>
            <Title level={2} className="employee-detail-title">{data.name}</Title>
            <Tag color={data.status === 'ARCHIVED' ? 'red' : data.status === 'ACTIVE' ? 'green' : 'blue'}>
              {data.status}
            </Tag>
          </Space>
          <Paragraph type="secondary">{data.role_name} · {data.description}</Paragraph>
        </div>
        {canEdit && !employeeIsArchived && (
          <Space>
            <Button onClick={() => navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/new`)}>Tool 管理</Button>
            <Button onClick={() => setEditOpen(true)}>编辑基本信息</Button>
            <Popconfirm
              title="归档这个数字员工？"
              description="归档后不能新建版本或部署，历史版本和运行记录保留。"
              okText="确认归档"
              cancelText="取消"
              onConfirm={() => archiveMutation.mutate()}
            >
              <Button danger loading={archiveMutation.isPending}>归档员工</Button>
            </Popconfirm>
          </Space>
        )}
      </div>

      <div className="management-two-column">
        <Card className="management-card" title="员工基本信息">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="员工名称">{data.name}</Descriptions.Item>
            <Descriptions.Item label="员工类型">{data.role_name}</Descriptions.Item>
            <Descriptions.Item label="员工描述">{data.description}</Descriptions.Item>
            <Descriptions.Item label="员工 ID"><Text copyable>{data.id}</Text></Descriptions.Item>
            <Descriptions.Item label="版本数量">{data.version_count}</Descriptions.Item>
            <Descriptions.Item label="最新发布版本">
              {data.latest_published_version ? `v${data.latest_published_version.version}` : '尚未发布'}
            </Descriptions.Item>
          </Descriptions>
        </Card>

        <Card className="management-card" title="员工版本">
          {canEdit && !employeeIsArchived && (
            <Button type="primary" block onClick={() => setCreateVersionOpen(true)}>
              新建草稿版本
            </Button>
          )}
          {versions.isLoading && <div className="management-loading">正在加载版本…</div>}
          {versions.isError && <Alert type="error" message={(versions.error as Error).message} />}
          {versions.isSuccess && orderedVersions.length === 0 && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有员工版本" />
          )}
          {orderedVersions.length > 0 && (
            <Table<EmployeeVersion>
              className="employee-version-table"
              rowKey="version"
              size="small"
              pagination={false}
              columns={versionColumns}
              dataSource={orderedVersions}
              onRow={(item) => ({ onClick: () => setSelectedVersion(item.version) })}
            />
          )}
        </Card>
      </div>

      {selected && (
        <Card className="management-card" title={`版本 v${selected.version} 快照`}>
          <div className="version-snapshot-grid">
            <div>
              <Text type="secondary">系统指令</Text>
              <pre className="prompt-preview">{selected.system_prompt}</pre>
            </div>
            <div>
              <div className="management-section-heading">
                <Text strong>Skill 与 Tool 绑定</Text>
                <Text type="secondary">固定在 v{selected.version}</Text>
              </div>
              <div className="resource-ref-list">
                {selected.skill_version_refs.length === 0 && <Text type="secondary">未绑定 Skill</Text>}
                {selected.skill_version_refs.map((ref) => {
                  const skillId = ref.skill_id ?? ref.resource_id ?? ''
                  return (
                    <div className="resource-ref-row" key={`${skillId}:${ref.version}`}>
                      <Space direction="vertical" size={0}>
                        <Text>Skill · v{ref.version}</Text>
                        <Text type="secondary" copyable>{skillId}</Text>
                      </Space>
                      <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/skills/${encodeURIComponent(skillId)}/versions/${ref.version}/files`}>
                        文件工作区
                      </Link>
                    </div>
                  )
                })}
                {selected.tool_version_refs.length === 0 && <Text type="secondary">未绑定 Tool</Text>}
                {selected.tool_version_refs.map((ref) => {
                  const toolId = ref.tool_id ?? ref.resource_id ?? ''
                  return (
                    <div className="resource-ref-row" key={`${toolId}:${ref.version}`}>
                      <Space direction="vertical" size={0}>
                        <Text>Tool · v{ref.version}</Text>
                        <Text type="secondary" copyable>{toolId}</Text>
                      </Space>
                      <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}/tools/${encodeURIComponent(toolId)}`}>
                        工具配置
                      </Link>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        </Card>
      )}

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
          <Form.Item name="description" label="员工描述" rules={[{ required: true, message: '请输入员工描述' }]}>
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
        okText="创建版本"
        cancelText="取消"
        confirmLoading={createVersionMutation.isPending}
        destroyOnHidden
      >
        <Paragraph type="secondary">版本创建后，在员工详情中打开草稿编辑器补充 Skill、Tool 与执行配置。</Paragraph>
        <Form form={versionForm} layout="vertical" onFinish={(values) => createVersionMutation.mutate(values.system_prompt)}>
          <Form.Item name="system_prompt" label="系统指令" rules={[{ required: true, message: '请输入系统指令' }]}>
            <Input.TextArea rows={5} maxLength={65535} placeholder="定义员工的职责、边界和回答要求" />
          </Form.Item>
          {createVersionMutation.isError && <Alert type="error" message={(createVersionMutation.error as Error).message} />}
        </Form>
      </Modal>
    </section>
  )
}
