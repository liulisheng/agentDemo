import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  createDeployment,
  listAuditEvents,
  listDeployments,
  listDigitalEmployees,
  listEmployeeVersions,
  rollbackDeployment,
} from '../../api/client'
import type { AuditEvent, DeploymentSummary, EmployeeVersion } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Paragraph, Text, Title } = Typography

/** Deployment 列表：选择发布版本、回滚并查看租户审计记录。 */
export function DeploymentListPage() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const isAdmin = currentIdentity.roleName === '管理员'
  const [employeeFilter, setEmployeeFilter] = useState<string>()
  const [statusFilter, setStatusFilter] = useState<string>()
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null])
  const [createOpen, setCreateOpen] = useState(false)
  const [createEmployeeId, setCreateEmployeeId] = useState<string>()
  const [createVersion, setCreateVersion] = useState<number>()
  const [rollbackTarget, setRollbackTarget] = useState<DeploymentSummary>()
  const [rollbackVersion, setRollbackVersion] = useState<number>()
  const [auditDeploymentId, setAuditDeploymentId] = useState<string>()
  const cursor = cursorStack[cursorStack.length - 1] ?? null

  const deployments = useQuery({
    queryKey: ['deployments', fakeUser, employeeFilter, statusFilter, cursor],
    queryFn: () => listDeployments({
      employee_id: employeeFilter,
      status: statusFilter,
      limit: 20,
      cursor,
    }, fakeUser),
  })
  const employeeOptions = useQuery({
    queryKey: ['employee-options', fakeUser],
    queryFn: () => listDigitalEmployees({ limit: 100 }, fakeUser),
  })
  const versionEmployeeId = rollbackTarget?.employee_id ?? createEmployeeId
  const publishedVersions = useQuery({
    queryKey: ['employee-versions', fakeUser, versionEmployeeId, 'PUBLISHED'],
    queryFn: () => listEmployeeVersions(versionEmployeeId!, { status: 'PUBLISHED', limit: 100 }, fakeUser),
    enabled: Boolean(versionEmployeeId),
  })
  const auditEvents = useQuery({
    queryKey: ['deployment-audit', fakeUser, auditDeploymentId],
    queryFn: () => listAuditEvents({
      resource_type: 'deployment',
      resource_id: auditDeploymentId,
      limit: 20,
    }, fakeUser),
    enabled: Boolean(auditDeploymentId),
  })

  const invalidateDeployments = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['deployments', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['deployment-audit', fakeUser] }),
  ])
  const createMutation = useMutation({
    mutationFn: () => {
      if (!createEmployeeId || !createVersion) throw new Error('请选择员工和已发布版本')
      return createDeployment(createEmployeeId, createVersion, fakeUser)
    },
    onSuccess: async () => {
      await invalidateDeployments()
      setCreateOpen(false)
      setCreateEmployeeId(undefined)
      setCreateVersion(undefined)
      message.success('Deployment 已创建')
    },
  })
  const rollbackMutation = useMutation({
    mutationFn: () => {
      if (!rollbackTarget || !rollbackVersion) throw new Error('请选择回滚目标版本')
      return rollbackDeployment(
        rollbackTarget.id,
        rollbackTarget.employee_id,
        rollbackVersion,
        fakeUser,
      )
    },
    onSuccess: async () => {
      const deploymentId = rollbackTarget?.id
      await invalidateDeployments()
      setRollbackTarget(undefined)
      setRollbackVersion(undefined)
      if (deploymentId) setAuditDeploymentId(deploymentId)
      message.success('Deployment 已回滚，历史 Run 保持原版本快照')
    },
  })

  const employeeNames = useMemo(
    () => new Map((employeeOptions.data?.items ?? []).map((employee) => [employee.id, employee.name])),
    [employeeOptions.data?.items],
  )
  const columns = useMemo<ColumnsType<DeploymentSummary>>(() => [
    {
      title: '数字员工',
      dataIndex: 'employee_id',
      key: 'employee',
      render: (employeeId: string, item) => (
        <Space direction="vertical" size={0}>
          <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}`}>
            {employeeNames.get(employeeId) ?? employeeId}
          </Link>
          <Text type="secondary">Deployment {item.id}</Text>
        </Space>
      ),
    },
    {
      title: '当前版本',
      dataIndex: 'version',
      key: 'version',
      render: (_: number, item) => (
        <Space direction="vertical" size={0}>
          <Text strong>v{item.version_summary.version}</Text>
          <Text type="secondary">
            {item.version_summary.skill_count} Skill · {item.version_summary.tool_count} Tool
          </Text>
        </Space>
      ),
    },
    {
      title: '执行配置',
      key: 'profile',
      render: (_: unknown, item) => (
        <Text>{item.version_summary.execution_profile_id} · v{item.version_summary.execution_profile_version}</Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value: string) => <Tag color={value === 'ACTIVE' ? 'green' : 'default'}>{value}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 190,
      render: (_: unknown, item) => (
        <Space>
          <Button type="link" onClick={() => setAuditDeploymentId(item.id)}>审计记录</Button>
          {isAdmin && <Button type="link" onClick={() => { setRollbackTarget(item); setRollbackVersion(undefined) }}>回滚</Button>}
        </Space>
      ),
    },
  ], [employeeNames, isAdmin])

  const closeCreate = () => {
    setCreateOpen(false)
    setCreateEmployeeId(undefined)
    setCreateVersion(undefined)
  }

  return (
    <section className="management-page" aria-label="部署记录">
      <div className="management-page-header">
        <div>
          <Text className="management-eyebrow">运行管理 / Deployment</Text>
          <Title level={2}>部署记录</Title>
          <Paragraph type="secondary">每个 Deployment 固定一个已发布员工版本；回滚只改变当前指向，不改写历史 Run。</Paragraph>
        </div>
        {isAdmin && <Button type="primary" onClick={() => setCreateOpen(true)}>新建 Deployment</Button>}
      </div>

      <Card className="management-card">
        <div className="management-filter-row">
          <Select
            allowClear
            aria-label="按员工筛选部署"
            placeholder="全部员工"
            value={employeeFilter}
            onChange={(value) => { setEmployeeFilter(value); setCursorStack([null]) }}
            options={(employeeOptions.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))}
            style={{ width: 240 }}
          />
          <Select
            allowClear
            aria-label="按部署状态筛选"
            placeholder="全部状态"
            value={statusFilter}
            onChange={(value) => { setStatusFilter(value); setCursorStack([null]) }}
            options={[{ value: 'ACTIVE', label: 'ACTIVE' }, { value: 'DISABLED', label: 'DISABLED' }]}
            style={{ width: 170 }}
          />
          <Text className="management-stat">{deployments.data?.items.length ?? 0} 条 / 第 {cursorStack.length} 页</Text>
        </div>
        {deployments.isLoading && <div className="management-loading">正在加载部署记录…</div>}
        {deployments.isError && <Alert type="error" showIcon message="部署记录加载失败" description={(deployments.error as Error).message} />}
        {deployments.isSuccess && deployments.data.items.length === 0 && (
          <Empty className="management-empty" description="当前筛选下没有部署记录" />
        )}
        {deployments.isSuccess && deployments.data.items.length > 0 && (
          <>
            <Table<DeploymentSummary> rowKey="id" columns={columns} dataSource={deployments.data.items} pagination={false} />
            <div className="management-pagination">
              <Text type="secondary">部署 ID 稳定排序</Text>
              <Space>
                <Button disabled={cursorStack.length <= 1} onClick={() => setCursorStack((stack) => stack.slice(0, -1))}>上一页</Button>
                <Button disabled={!deployments.data.next_cursor} onClick={() => {
                  if (deployments.data?.next_cursor) setCursorStack((stack) => [...stack, deployments.data!.next_cursor!])
                }}>下一页</Button>
              </Space>
            </div>
          </>
        )}
      </Card>

      {auditDeploymentId && (
        <Card
          className="management-card"
          title="Deployment 审计事件"
          extra={<Button onClick={() => setAuditDeploymentId(undefined)}>关闭</Button>}
        >
          {auditEvents.isLoading && <div className="management-loading">正在读取审计记录…</div>}
          {auditEvents.isError && <Alert type="error" message={(auditEvents.error as Error).message} />}
          {auditEvents.data && auditEvents.data.items.length === 0 && <Empty description="尚无审计事件" />}
          {auditEvents.data?.items.map((event) => (
            <div className="audit-event-row" key={event.event_id}>
              <Space direction="vertical" size={2}>
                <Text strong>{event.event_type}</Text>
                <Text type="secondary">{event.created_at} · {event.actor_user_id ?? event.actor_type}</Text>
              </Space>
              <Text>{String(event.payload.previous_version ?? '—')} → {String(event.payload.version ?? '—')}</Text>
            </div>
          ))}
        </Card>
      )}

      <Modal
        title="新建 Deployment"
        open={createOpen}
        onCancel={closeCreate}
        onOk={() => createMutation.mutate()}
        okText="创建 Deployment"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        destroyOnHidden
      >
        <Space direction="vertical" size={14} className="deployment-modal-content">
          <div>
            <Text strong>选择数字员工</Text>
            <Select
              aria-label="选择数字员工"
              value={createEmployeeId}
              onChange={(value) => { setCreateEmployeeId(value); setCreateVersion(undefined) }}
              options={(employeeOptions.data?.items ?? [])
                .filter((item) => item.status !== 'ARCHIVED')
                .map((item) => ({ value: item.id, label: `${item.name} · ${item.role_name}` }))}
              placeholder="选择员工"
              style={{ width: '100%', marginTop: 6 }}
            />
          </div>
          <div>
            <Text strong>选择 PUBLISHED 版本</Text>
            <Select
              aria-label="选择已发布版本"
              value={createVersion}
              onChange={setCreateVersion}
              disabled={!createEmployeeId}
              loading={publishedVersions.isLoading}
              options={(publishedVersions.data?.items ?? []).map((item) => ({
                value: item.version,
                label: `v${item.version} · ${item.skill_version_refs.length} Skill / ${item.tool_version_refs.length} Tool`,
              }))}
              placeholder="选择发布版本"
              style={{ width: '100%', marginTop: 6 }}
            />
          </div>
          {createMutation.isError && <Alert type="error" message={(createMutation.error as Error).message} />}
        </Space>
      </Modal>

      <Modal
        title="回滚 Deployment"
        open={Boolean(rollbackTarget)}
        onCancel={() => setRollbackTarget(undefined)}
        footer={null}
        destroyOnHidden
      >
        {rollbackTarget && (
          <Space direction="vertical" size={16} className="deployment-modal-content">
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="员工">{employeeNames.get(rollbackTarget.employee_id) ?? rollbackTarget.employee_id}</Descriptions.Item>
              <Descriptions.Item label="当前版本">v{rollbackTarget.version}</Descriptions.Item>
            </Descriptions>
            <div>
              <Text strong>回滚目标（仅已发布版本）</Text>
              <Select
                aria-label="回滚目标版本"
                value={rollbackVersion}
                onChange={setRollbackVersion}
                loading={publishedVersions.isLoading}
                options={(publishedVersions.data?.items ?? []).map((item) => ({ value: item.version, label: `v${item.version} · ${item.status}` }))}
                placeholder="选择回滚版本"
                style={{ width: '100%', marginTop: 6 }}
              />
            </div>
            <Alert type="info" message="回滚会更新 Deployment 当前版本；已创建的 RunSnapshot 不会改变。" />
            {rollbackMutation.isError && <Alert type="error" message={(rollbackMutation.error as Error).message} />}
            <div className="deployment-modal-actions">
              <Button onClick={() => setRollbackTarget(undefined)}>取消</Button>
              <Popconfirm
                title="确认回滚？"
                description={`Deployment 将从 v${rollbackTarget.version} 切换到 v${rollbackVersion ?? '—'}。`}
                okText="确认回滚"
                cancelText="返回"
                onConfirm={() => rollbackMutation.mutate()}
              >
                <Button type="primary" danger disabled={!rollbackVersion} loading={rollbackMutation.isPending}>确认回滚</Button>
              </Popconfirm>
            </div>
          </Space>
        )}
      </Modal>
    </section>
  )
}
