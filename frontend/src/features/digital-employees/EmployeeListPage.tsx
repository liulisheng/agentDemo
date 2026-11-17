import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  createDigitalEmployee,
  listDigitalEmployees,
} from '../../api/client'
import type { DigitalEmployeeSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Title, Text, Paragraph } = Typography

const statusColor: Record<string, string> = {
  DRAFT: 'default',
  ACTIVE: 'green',
  DISABLED: 'orange',
  ARCHIVED: 'red',
}

/** 租户员工目录，负责搜索、状态筛选、游标翻页和新增入口。 */
export function EmployeeListPage() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string>()
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null])
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm<{ name: string; role_name: string; description: string }>()
  const currentCursor = cursorStack[cursorStack.length - 1] ?? null
  const canEdit = currentIdentity.roleName === '管理员'

  const employees = useQuery({
    queryKey: ['digital-employees', fakeUser, keyword, status, currentCursor],
    queryFn: () => listDigitalEmployees({
      keyword: keyword.trim() || undefined,
      status,
      limit: 20,
      cursor: currentCursor,
    }, fakeUser),
  })

  const createMutation = useMutation({
    mutationFn: (input: { name: string; role_name: string; description: string }) =>
      createDigitalEmployee(input, fakeUser),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['digital-employees', fakeUser] })
      setCreateOpen(false)
      form.resetFields()
      message.success('数字员工已创建')
      navigate(`/enterprise/employees/${encodeURIComponent(created.id)}`)
    },
  })

  const columns = useMemo<ColumnsType<DigitalEmployeeSummary>>(() => [
    {
      title: '员工名称',
      dataIndex: 'name',
      key: 'name',
      render: (_: string, employee) => (
        <Link className="employee-name-link" to={`/enterprise/employees/${encodeURIComponent(employee.id)}`}>
          <span className="employee-name-primary">{employee.name}</span>
          <span className="employee-name-secondary">{employee.role_name}</span>
        </Link>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value: string) => <Tag color={statusColor[value] ?? 'default'}>{value}</Tag>,
    },
    {
      title: '能力依赖',
      key: 'resources',
      render: (_: unknown, employee) => (
        <Space size={6} wrap>
          <Tag>{employee.resource_counts.skill} Skill</Tag>
          <Tag>{employee.resource_counts.tool ?? 0} Tool</Tag>
        </Space>
      ),
    },
    {
      title: '员工描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '',
      key: 'action',
      width: 82,
      render: (_: unknown, employee) => (
        <Link to={`/enterprise/employees/${encodeURIComponent(employee.id)}`}>查看详情</Link>
      ),
    },
  ], [])

  const resetPaging = () => setCursorStack([null])
  const submitCreate = () => form.submit()

  return (
    <section className="management-page" aria-label="数字员工目录">
      <div className="management-page-header">
        <div>
          <Text className="management-eyebrow">员工管理 / 目录</Text>
          <Title level={2}>数字员工</Title>
          <Paragraph type="secondary">管理员工基本信息、版本和发布状态。技能与工具在员工详情的版本中维护。</Paragraph>
        </div>
        {canEdit && (
          <Button type="primary" size="large" onClick={() => setCreateOpen(true)}>
            新建数字员工
          </Button>
        )}
      </div>

      <Card className="management-card">
        <div className="management-filter-row">
          <Input.Search
            aria-label="搜索数字员工"
            allowClear
            maxLength={128}
            placeholder="搜索员工名称、类型或描述"
            value={keyword}
            onChange={(event) => {
              setKeyword(event.target.value)
              resetPaging()
            }}
            style={{ width: 320 }}
          />
          <Select
            aria-label="员工状态"
            allowClear
            placeholder="全部状态"
            value={status}
            onChange={(value) => {
              setStatus(value)
              resetPaging()
            }}
            options={[
              { value: 'DRAFT', label: '草稿' },
              { value: 'ACTIVE', label: '启用' },
              { value: 'DISABLED', label: '停用' },
              { value: 'ARCHIVED', label: '已归档' },
            ]}
            style={{ width: 160 }}
          />
          <Text className="management-stat">
            {employees.data?.items.length ?? 0} 条 / 第 {cursorStack.length} 页
          </Text>
        </div>

        {employees.isLoading && <div className="management-loading">正在加载员工目录…</div>}
        {employees.isError && (
          <Alert
            type="error"
            showIcon
            message="员工目录加载失败"
            description={(employees.error as Error).message}
            action={<Button size="small" onClick={() => void employees.refetch()}>重试</Button>}
          />
        )}
        {employees.isSuccess && employees.data.items.length === 0 && (
          <Empty
            className="management-empty"
            description={keyword || status ? '没有符合条件的数字员工' : '还没有数字员工'}
          >
            {!keyword && !status && canEdit && (
              <Button type="primary" onClick={() => setCreateOpen(true)}>创建第一个员工</Button>
            )}
          </Empty>
        )}
        {employees.isSuccess && employees.data.items.length > 0 && (
          <>
            <Table<DigitalEmployeeSummary>
              rowKey="id"
              columns={columns}
              dataSource={employees.data.items}
              pagination={false}
              onRow={(employee) => ({
                onDoubleClick: () => navigate(`/enterprise/employees/${encodeURIComponent(employee.id)}`),
              })}
            />
            <div className="management-pagination">
              <Text type="secondary">名称升序 · 游标分页</Text>
              <Space>
                <Button
                  disabled={cursorStack.length <= 1}
                  onClick={() => setCursorStack((stack) => stack.slice(0, -1))}
                >
                  上一页
                </Button>
                <Button
                  disabled={!employees.data.next_cursor}
                  onClick={() => {
                    if (employees.data?.next_cursor) {
                      setCursorStack((stack) => [...stack, employees.data!.next_cursor!])
                    }
                  }}
                >
                  下一页
                </Button>
              </Space>
            </div>
          </>
        )}
      </Card>

      <Modal
        title="新建数字员工"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        okText="创建员工"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item name="name" label="员工名称" rules={[{ required: true, message: '请输入员工名称' }]}>
            <Input maxLength={128} placeholder="例如：工单助手" />
          </Form.Item>
          <Form.Item name="role_name" label="员工类型" rules={[{ required: true, message: '请输入员工类型' }]}>
            <Input maxLength={128} placeholder="例如：客户支持" />
          </Form.Item>
          <Form.Item
            name="description"
            label="员工描述"
            extra="用于员工目录和详情说明，不会作为模型运行指令。"
            rules={[{ required: true, message: '请输入员工描述' }]}
          >
            <Input.TextArea maxLength={1024} rows={3} placeholder="说明员工的职责和使用范围" />
          </Form.Item>
          {createMutation.isError && <Alert type="error" message={(createMutation.error as Error).message} />}
        </Form>
      </Modal>
    </section>
  )
}
