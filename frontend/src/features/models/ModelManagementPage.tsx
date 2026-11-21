import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  addManualModel,
  createModelConnection,
  createModelRoute,
  getModelTest,
  listConnectionModels,
  listModelConnections,
  listModelRoutes,
  queueConnectionTest,
  queueModelRouteTest,
  setModelRouteStatus,
  syncConnectionModels,
  updateModelConnection,
} from '../../api/client'
import type {
  ModelCatalogSummary,
  ModelConnectionSummary,
  ModelRouteSummary,
  ModelTestRecord,
} from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Paragraph, Text, Title } = Typography

const testStateLabels: Record<ModelTestRecord['state'], string> = {
  QUEUED: '排队中',
  RUNNING: '测试中',
  SUCCEEDED: '可用',
  FAILED: '失败',
  UNKNOWN: '结果待确认',
}

const testStateColors: Record<ModelTestRecord['state'], string> = {
  QUEUED: 'default',
  RUNNING: 'processing',
  SUCCEEDED: 'success',
  FAILED: 'error',
  UNKNOWN: 'warning',
}

function authorizationSourceLabel(source?: string) {
  if (!source || source === 'platform') return '平台授权'
  if (source === 'dadi') return 'Dadi 授权'
  return source
}

export function ModelTestResultPanel({ record }: { record: ModelTestRecord }) {
  const isActive = record.state === 'QUEUED' || record.state === 'RUNNING'
  const isUnknown = record.state === 'UNKNOWN'
  return (
    <section className={`model-test-result model-test-result-${record.state.toLowerCase()}`} aria-live="polite">
      <div className="model-test-result-heading">
        <div>
          <Text className="model-test-result-label">{record.test_kind === 'MODEL' ? '模型推理测试' : '连接测试'}</Text>
          <strong>{record.model_id ?? record.connection_id}</strong>
        </div>
        <Tag color={testStateColors[record.state]}>{testStateLabels[record.state]}</Tag>
      </div>
      {isActive ? (
        <Text type="secondary">测试由 Worker 处理，页面会自动更新。</Text>
      ) : isUnknown ? (
        <Alert
          type="warning"
          showIcon
          message="无法确认上游是否完成请求"
          description={`${record.error_category ?? 'MODEL_TEST_OUTCOME_UNKNOWN'}。系统不会自动重发，请检查供应商侧记录后再决定是否重新测试。`}
        />
      ) : record.state === 'FAILED' ? (
        <Alert
          type="error"
          showIcon
          message="测试未通过"
          description={record.error_category ?? 'MODEL_TEST_FAILED'}
        />
      ) : null}
      {!isActive && (
        <div className="model-test-result-grid">
          <span>耗时<strong>{record.latency_ms === null ? '—' : `${record.latency_ms} ms`}</strong></span>
          {record.test_kind === 'MODEL' && (
            <span>用量<strong>{record.usage_status ?? '—'}</strong></span>
          )}
          {record.total_tokens !== null && (
            <span>Token<strong>{record.total_tokens}（入 {record.input_tokens ?? 0} / 出 {record.output_tokens ?? 0}）</strong></span>
          )}
          {record.provider_request_id && (
            <span className="model-test-request">上游请求引用<strong>{record.provider_request_id}</strong></span>
          )}
        </div>
      )}
      {record.state === 'SUCCEEDED' && record.test_kind === 'MODEL' && (
        <Text className="model-test-result-note">结果仅代表这次测试请求。</Text>
      )}
    </section>
  )
}

/** 租户模型连接、模型目录、路由授权与异步测试管理页面。 */
export function ModelManagementPage() {
  const { fakeUser } = useTenantContext()
  return <ModelManagementWorkspace key={fakeUser} />
}

function ModelManagementWorkspace() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const [selectedConnectionId, setSelectedConnectionId] = useState<string>()
  const [connectionDrawerOpen, setConnectionDrawerOpen] = useState(false)
  const [editingConnection, setEditingConnection] = useState<ModelConnectionSummary>()
  const [manualModelOpen, setManualModelOpen] = useState(false)
  const [routeDrawerOpen, setRouteDrawerOpen] = useState(false)
  const [routeModel, setRouteModel] = useState<ModelCatalogSummary>()
  const [testConfirmationRoute, setTestConfirmationRoute] = useState<ModelRouteSummary>()
  const [activeTestId, setActiveTestId] = useState<string>()
  const [connectionForm] = Form.useForm()
  const [modelForm] = Form.useForm()
  const [routeForm] = Form.useForm()
  const isAdmin = currentIdentity.roleName === '管理员'
  const completedTestRef = useRef('')

  const connections = useQuery({
    queryKey: ['model-connections', fakeUser],
    queryFn: () => listModelConnections(fakeUser),
  })
  const routes = useQuery({
    queryKey: ['model-routes', fakeUser],
    queryFn: () => listModelRoutes(fakeUser),
  })
  const selectedConnection = connections.data?.items.find(
    (item) => item.connection_id === selectedConnectionId,
  )
  const models = useQuery({
    queryKey: ['connection-models', fakeUser, selectedConnectionId],
    queryFn: () => listConnectionModels(selectedConnectionId!, fakeUser),
    enabled: Boolean(selectedConnectionId),
  })
  const testRecord = useQuery({
    queryKey: ['model-test', fakeUser, activeTestId],
    queryFn: () => getModelTest(activeTestId!, fakeUser),
    enabled: Boolean(activeTestId),
    refetchInterval: (query) => {
      const state = query.state.data?.state
      return state === 'QUEUED' || state === 'RUNNING' ? 900 : false
    },
  })

  useEffect(() => {
    const first = connections.data?.items[0]
    if (!selectedConnectionId && first) setSelectedConnectionId(first.connection_id)
    if (
      selectedConnectionId &&
      connections.data &&
      !connections.data.items.some((item) => item.connection_id === selectedConnectionId)
    ) {
      setSelectedConnectionId(first?.connection_id)
    }
  }, [connections.data, selectedConnectionId])

  useEffect(() => {
    const record = testRecord.data
    if (!record || record.state === 'QUEUED' || record.state === 'RUNNING') return
    const key = `${record.test_id}:${record.state}`
    if (completedTestRef.current === key) return
    completedTestRef.current = key
    if (record.test_kind === 'CONNECTION' && record.state === 'SUCCEEDED') {
      void queryClient.invalidateQueries({
        queryKey: ['connection-models', fakeUser, record.connection_id],
      })
    }
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['model-connections', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['model-routes', fakeUser] }),
    ])
  }, [fakeUser, queryClient, testRecord.data])

  const invalidateCatalog = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['model-connections', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['connection-models', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['model-routes', fakeUser] }),
  ])

  const createConnectionMutation = useMutation({
    mutationFn: (values: ReturnType<typeof connectionForm.getFieldsValue>) => createModelConnection({
      name: values.name,
      provider_kind: 'openai-compatible',
      protocol: values.protocol,
      endpoint: values.endpoint,
      secret_id: values.secret_id?.trim() || undefined,
      catalog_mode: values.catalog_mode,
    }, fakeUser),
    onSuccess: async (connection) => {
      setConnectionDrawerOpen(false)
      setEditingConnection(undefined)
      connectionForm.resetFields()
      setSelectedConnectionId(connection.connection_id)
      await invalidateCatalog()
      message.success('模型连接已创建')
    },
  })
  const updateConnectionMutation = useMutation({
    mutationFn: (values: ReturnType<typeof connectionForm.getFieldsValue>) => {
      if (!editingConnection) throw new Error('请选择要编辑的连接')
      return updateModelConnection(editingConnection.connection_id, {
        name: values.name,
        endpoint: values.endpoint,
        protocol: values.protocol,
        catalog_mode: values.catalog_mode,
        ...(values.secret_id?.trim() ? { secret_id: values.secret_id.trim() } : {}),
      }, fakeUser)
    },
    onSuccess: async () => {
      setConnectionDrawerOpen(false)
      setEditingConnection(undefined)
      connectionForm.resetFields()
      await invalidateCatalog()
      message.success('连接新版本已保存')
    },
  })
  const addManualModelMutation = useMutation({
    mutationFn: (values: ReturnType<typeof modelForm.getFieldsValue>) => {
      if (!selectedConnectionId) throw new Error('请先选择模型连接')
      return addManualModel(selectedConnectionId, {
        model_id: values.model_id,
        display_name: values.display_name,
        ...(values.context_window ? { context_window: values.context_window } : {}),
      }, fakeUser)
    },
    onSuccess: async () => {
      setManualModelOpen(false)
      modelForm.resetFields()
      await queryClient.invalidateQueries({
        queryKey: ['connection-models', fakeUser, selectedConnectionId],
      })
      message.success('模型已添加到连接目录')
    },
  })
  const syncModelsMutation = useMutation({
    mutationFn: () => {
      if (!selectedConnectionId) throw new Error('请先选择模型连接')
      return syncConnectionModels(selectedConnectionId, fakeUser)
    },
    onSuccess: (job) => {
      completedTestRef.current = ''
      setActiveTestId(job.test_id)
      message.success('模型目录同步已排队')
    },
  })
  const connectionTestMutation = useMutation({
    mutationFn: () => {
      if (!selectedConnectionId) throw new Error('请先选择模型连接')
      return queueConnectionTest(selectedConnectionId, fakeUser)
    },
    onSuccess: (job) => {
      completedTestRef.current = ''
      setActiveTestId(job.test_id)
      message.success('连接测试已排队')
    },
  })
  const createRouteMutation = useMutation({
    mutationFn: (values: ReturnType<typeof routeForm.getFieldsValue>) => {
      if (!selectedConnectionId || !routeModel) throw new Error('请选择模型')
      return createModelRoute({
        alias: values.alias,
        connection_id: selectedConnectionId,
        catalog_model_id: routeModel.model_id,
        model_name: routeModel.model_id,
        parameters: values.temperature === undefined ? {} : { temperature: values.temperature },
      }, fakeUser)
    },
    onSuccess: async () => {
      setRouteDrawerOpen(false)
      setRouteModel(undefined)
      routeForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['model-routes', fakeUser] })
      message.success('模型路由已创建')
    },
  })
  const routeTestMutation = useMutation({
    mutationFn: (route: ModelRouteSummary) => queueModelRouteTest(route.route_id, fakeUser),
    onSuccess: (job) => {
      completedTestRef.current = ''
      setActiveTestId(job.test_id)
      setTestConfirmationRoute(undefined)
      message.success('模型测试已排队')
    },
  })
  const routeStatusMutation = useMutation({
    mutationFn: (route: ModelRouteSummary) => setModelRouteStatus(
      route.route_id,
      route.status !== 'ACTIVE',
      fakeUser,
    ),
    onSuccess: async (route) => {
      await invalidateCatalog()
      message.success(route.status === 'ACTIVE' ? '模型路由已启用' : '模型路由已停用')
    },
  })

  const connectionRows = connections.data?.items ?? []
  const modelRows = models.data?.items ?? []
  const connectionNameById = useMemo(
    () => new Map(connectionRows.map((connection) => [connection.connection_id, connection.name])),
    [connectionRows],
  )

  const modelColumns: ColumnsType<ModelCatalogSummary> = [
    {
      title: '模型 ID',
      dataIndex: 'model_id',
      render: (_value, model) => (
        <span className="model-catalog-name">
          <strong>{model.display_name}</strong>
          <code>{model.model_id}</code>
        </span>
      ),
    },
    {
      title: '来源',
      dataIndex: 'catalog_source',
      width: 110,
      render: (source: ModelCatalogSummary['catalog_source']) =>
        source === 'REMOTE' ? <Tag color="blue">远端发现</Tag> : <Tag>手工维护</Tag>,
    },
    {
      title: '上下文',
      dataIndex: 'context_window',
      width: 100,
      render: (value: number | null) => value ? value.toLocaleString() : '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status: ModelCatalogSummary['status']) =>
        <Tag color={status === 'ACTIVE' ? 'success' : 'default'}>{status === 'ACTIVE' ? '可用' : '已停用'}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_value, model) => (
        <Button
          type="link"
          disabled={!isAdmin || model.status !== 'ACTIVE' || selectedConnection?.status !== 'ACTIVE'}
          onClick={() => {
            setRouteModel(model)
            routeForm.setFieldsValue({ alias: model.model_id, temperature: undefined })
            setRouteDrawerOpen(true)
          }}
        >
          创建路由
        </Button>
      ),
    },
  ]

  const routeColumns: ColumnsType<ModelRouteSummary> = [
    {
      title: '路由别名',
      dataIndex: 'alias',
      render: (alias: string, route) => (
        <span className="model-route-name">
          <strong>{alias}</strong>
          <code>{route.profile.catalog_model_id ?? route.profile.model_name}</code>
        </span>
      ),
    },
    {
      title: '连接',
      dataIndex: ['profile', 'connection_id'],
      render: (connectionId: string | null) => connectionId
        ? connectionNameById.get(connectionId) ?? '连接不可用'
        : '旧版直连',
    },
    {
      title: '授权来源',
      dataIndex: 'authorization_source',
      width: 120,
      render: (source: string | undefined) => <Tag color="cyan">{authorizationSourceLabel(source)}</Tag>,
    },
    {
      title: '版本',
      dataIndex: 'route_version',
      width: 80,
      render: (version: number | undefined) => version ? `v${version}` : '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 95,
      render: (status: string) => <Tag color={status === 'ACTIVE' ? 'success' : 'default'}>{status === 'ACTIVE' ? '启用中' : '已停用'}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      render: (_value, route) => (
        <Space size={2}>
          <Button type="link" disabled={!isAdmin || route.status !== 'ACTIVE'} onClick={() => setTestConfirmationRoute(route)}>
            测试模型
          </Button>
          {isAdmin && (
            <Popconfirm
              title={route.status === 'ACTIVE' ? '停用此模型路由？' : '重新启用此模型路由？'}
              description="员工版本将只能选择启用且当前身份有权使用的路由。"
              okText={route.status === 'ACTIVE' ? '停用' : '启用'}
              cancelText="取消"
              onConfirm={() => routeStatusMutation.mutate(route)}
            >
              <Button type="link" loading={routeStatusMutation.isPending}>
                {route.status === 'ACTIVE' ? '停用' : '启用'}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const openNewConnection = () => {
    setEditingConnection(undefined)
    connectionForm.setFieldsValue({
      name: '',
      protocol: 'openai-chat-completions',
      endpoint: '',
      secret_id: '',
      catalog_mode: 'REMOTE',
    })
    setConnectionDrawerOpen(true)
  }
  const openEditConnection = () => {
    if (!selectedConnection) return
    setEditingConnection(selectedConnection)
    connectionForm.setFieldsValue({
      name: selectedConnection.name,
      protocol: selectedConnection.protocol,
      endpoint: selectedConnection.endpoint,
      secret_id: '',
      catalog_mode: selectedConnection.catalog_mode,
    })
    setConnectionDrawerOpen(true)
  }

  return (
    <section className="management-page model-management-page">
      <header className="management-page-header model-management-header">
        <div>
          <Text className="management-eyebrow">平台配置</Text>
          <Title level={2}>模型管理</Title>
          <Paragraph>管理租户模型连接、目录、路由与可用性测试。</Paragraph>
        </div>
        {isAdmin && (
          <Button type="primary" onClick={openNewConnection}>添加连接</Button>
        )}
      </header>

      {testRecord.data && <ModelTestResultPanel record={testRecord.data} />}
      {testRecord.isError && <Alert type="error" showIcon message="无法读取测试结果" description={(testRecord.error as Error).message} />}
      {[
        syncModelsMutation,
        connectionTestMutation,
        routeTestMutation,
        routeStatusMutation,
      ].some((mutation) => mutation.isError) && (
        <Alert
          className="model-management-action-error"
          type="error"
          showIcon
          message="模型管理操作失败"
          description={[
            syncModelsMutation,
            connectionTestMutation,
            routeTestMutation,
            routeStatusMutation,
          ].find((mutation) => mutation.isError)?.error instanceof Error
            ? ([
                syncModelsMutation,
                connectionTestMutation,
                routeTestMutation,
                routeStatusMutation,
              ].find((mutation) => mutation.isError)?.error as Error).message
            : '请检查当前租户权限和模型连接状态后重试。'}
        />
      )}

      <Tabs
        className="model-management-tabs"
        items={[
          {
            key: 'connections',
            label: '连接与模型',
            children: (
              <div className="model-connection-workspace">
                <Card className="management-card model-connection-list-card" title="模型连接" extra={isAdmin && <Button type="link" onClick={openNewConnection}>添加连接</Button>}>
                  {connections.isPending ? (
                    <div className="management-loading"><Spin /><span>正在读取连接</span></div>
                  ) : connections.isError ? (
                    <Alert
                      type="error"
                      showIcon
                      message="无法读取模型连接"
                      description={(connections.error as Error).message}
                      action={<Button onClick={() => void connections.refetch()}>重试</Button>}
                    />
                  ) : connectionRows.length === 0 ? (
                    <Empty
                      className="management-empty"
                      description={<span>还没有模型连接。添加一个 OpenAI 兼容入口后，可在同一连接下维护多个模型 ID。</span>}
                    >
                      {isAdmin && <Button type="primary" onClick={openNewConnection}>添加第一个连接</Button>}
                    </Empty>
                  ) : (
                    <div className="model-connection-list" role="list" aria-label="模型连接列表">
                      {connectionRows.map((connection) => (
                        <button
                          type="button"
                          role="listitem"
                          key={connection.connection_id}
                          aria-current={selectedConnectionId === connection.connection_id ? 'true' : undefined}
                          className={`model-connection-row${selectedConnectionId === connection.connection_id ? ' selected' : ''}`}
                          onClick={() => setSelectedConnectionId(connection.connection_id)}
                        >
                          <span className="model-connection-row-main">
                            <strong>{connection.name}</strong>
                            <code>{connection.endpoint}</code>
                          </span>
                          <span className="model-connection-row-meta">
                            <Tag color={connection.status === 'ACTIVE' ? 'success' : 'default'}>
                              {connection.status === 'ACTIVE' ? '启用' : '停用'}
                            </Tag>
                            <span>v{connection.version}</span>
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </Card>

                <section className="model-connection-detail">
                  {!selectedConnection && connectionRows.length > 0 ? (
                    <Card className="management-card model-empty-detail">
                      <Empty description="选择一个连接查看其模型目录" />
                    </Card>
                  ) : selectedConnection ? (
                    <>
                      <Card
                        className="management-card model-connection-detail-card"
                        title={<span className="model-detail-title"><strong>{selectedConnection.name}</strong><Tag color={selectedConnection.status === 'ACTIVE' ? 'success' : 'default'}>{selectedConnection.status === 'ACTIVE' ? '启用' : '停用'}</Tag></span>}
                        extra={isAdmin && <Button onClick={openEditConnection}>编辑连接</Button>}
                      >
                        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
                          <Descriptions.Item label="协议">{selectedConnection.protocol}</Descriptions.Item>
                          <Descriptions.Item label="目录方式">{selectedConnection.catalog_mode === 'REMOTE' ? '远端发现' : '手工维护'}</Descriptions.Item>
                          <Descriptions.Item label="凭据引用">
                            {selectedConnection.secret_ref ? selectedConnection.secret_ref.secret_id : '未配置'}
                          </Descriptions.Item>
                        </Descriptions>
                        <Text className="model-endpoint-guidance">
                          Docker Worker 请使用 <code>host.docker.internal</code> 访问宿主机；容器内的 <code>localhost</code> 指向容器自身。
                        </Text>
                        <div className="model-connection-actions">
                          <Space wrap>
                            <Button
                              loading={connectionTestMutation.isPending}
                              disabled={!isAdmin || selectedConnection.status !== 'ACTIVE'}
                              onClick={() => connectionTestMutation.mutate()}
                            >
                              测试连接
                            </Button>
                            <Button
                              loading={syncModelsMutation.isPending}
                              disabled={!isAdmin || selectedConnection.status !== 'ACTIVE' || selectedConnection.catalog_mode === 'MANUAL'}
                              onClick={() => syncModelsMutation.mutate()}
                            >
                              同步模型目录
                            </Button>
                            {isAdmin && selectedConnection.catalog_mode === 'MANUAL' && (
                              <Button onClick={() => setManualModelOpen(true)}>添加模型 ID</Button>
                            )}
                          </Space>
                          {selectedConnection.catalog_mode === 'MANUAL' && (
                            <Text type="secondary">此连接使用手工目录；测试连接仍会尝试认证后的模型列表接口。</Text>
                          )}
                        </div>
                      </Card>

                      <Card
                        className="management-card model-catalog-card"
                        title={<span>模型目录 <Text type="secondary">{modelRows.length} 个模型</Text></span>}
                        extra={isAdmin && selectedConnection.catalog_mode === 'MANUAL' && <Button type="link" onClick={() => setManualModelOpen(true)}>添加模型</Button>}
                      >
                        {models.isPending ? (
                          <div className="management-loading"><Spin /><span>正在读取模型目录</span></div>
                        ) : models.isError ? (
                          <Alert type="error" showIcon message="无法读取模型目录" description={(models.error as Error).message} action={<Button onClick={() => void models.refetch()}>重试</Button>} />
                        ) : modelRows.length === 0 ? (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={selectedConnection.catalog_mode === 'REMOTE' ? '目录为空。同步模型，或改用手工目录录入模型 ID。' : '尚未添加模型 ID。'}
                          >
                            {isAdmin && (
                              <Space>
                                {selectedConnection.catalog_mode === 'REMOTE' && <Button onClick={() => syncModelsMutation.mutate()}>同步目录</Button>}
                                <Button type="primary" onClick={() => setManualModelOpen(true)}>添加模型 ID</Button>
                              </Space>
                            )}
                          </Empty>
                        ) : (
                          <Table
                            rowKey="model_id"
                            size="small"
                            pagination={false}
                            dataSource={modelRows}
                            columns={modelColumns}
                            scroll={{ x: 620 }}
                          />
                        )}
                      </Card>
                    </>
                  ) : null}
                </section>
              </div>
            ),
          },
          {
            key: 'routes',
            label: '模型路由',
            children: (
              <Card className="management-card model-routes-card" title="ModelRoute" extra={<Text type="secondary">员工版本绑定路由别名；运行时固定连接和模型版本。</Text>}>
                {routes.isPending ? (
                  <div className="management-loading"><Spin /><span>正在读取模型路由</span></div>
                ) : routes.isError ? (
                  <Alert type="error" showIcon message="无法读取模型路由" description={(routes.error as Error).message} action={<Button onClick={() => void routes.refetch()}>重试</Button>} />
                ) : (routes.data?.items.length ?? 0) === 0 ? (
                  <Empty className="management-empty" description="还没有模型路由。先在连接目录中选择模型并创建路由。" />
                ) : (
                  <Table
                    rowKey="route_id"
                    size="middle"
                    pagination={{ pageSize: 10, showSizeChanger: false }}
                    dataSource={routes.data?.items ?? []}
                    columns={routeColumns}
                    scroll={{ x: 820 }}
                  />
                )}
              </Card>
            ),
          },
        ]}
      />

      <Drawer
        title={editingConnection ? '编辑模型连接' : '添加模型连接'}
        open={connectionDrawerOpen}
        width={520}
        destroyOnHidden
        onClose={() => {
          setConnectionDrawerOpen(false)
          setEditingConnection(undefined)
          connectionForm.resetFields()
        }}
        extra={(
          <Button
            type="primary"
            loading={createConnectionMutation.isPending || updateConnectionMutation.isPending}
            onClick={() => void connectionForm.submit()}
          >
            {editingConnection ? '保存连接版本' : '创建连接'}
          </Button>
        )}
      >
        <Form
          form={connectionForm}
          layout="vertical"
          onFinish={(values) => editingConnection
            ? updateConnectionMutation.mutate(values)
            : createConnectionMutation.mutate(values)}
        >
          <Form.Item name="name" label="连接名称" rules={[{ required: true, message: '请输入连接名称' }]}>
            <Input maxLength={128} placeholder="例如：本地推理服务" />
          </Form.Item>
          <Form.Item name="provider_kind" label="服务类型" initialValue="openai-compatible">
            <Select disabled options={[{ value: 'openai-compatible', label: 'OpenAI 兼容' }]} />
          </Form.Item>
          <Form.Item name="protocol" label="API 协议" rules={[{ required: true }]}>
            <Select options={[
              { value: 'openai-chat-completions', label: 'Chat Completions' },
              { value: 'openai-responses', label: 'Responses' },
              { value: 'openai-completions', label: 'Completions' },
            ]} />
          </Form.Item>
          <Form.Item name="endpoint" label="Base URL" rules={[{ required: true, message: '请输入模型服务地址' }, { type: 'url', message: '请输入有效的 HTTP(S) 地址' }]}>
            <Input placeholder="http://host.docker.internal:63072/v1" />
          </Form.Item>
          <Form.Item name="secret_id" label={editingConnection ? '更新凭据引用 ID（可留空保留原引用）' : '凭据引用 ID（可选）'}>
            <Input autoComplete="off" placeholder="例如：local-model-key" />
          </Form.Item>
          <Paragraph className="model-secret-help">此处只填写凭据引用，不填写 API Key。Worker 从租户范围的环境变量或后续接入的密钥服务读取实际凭据。</Paragraph>
          <Form.Item name="catalog_mode" label="模型目录方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'REMOTE', label: '远端发现（GET /models）' },
              { value: 'MANUAL', label: '手工维护模型 ID' },
            ]} />
          </Form.Item>
          {(createConnectionMutation.isError || updateConnectionMutation.isError) && (
            <Alert type="error" showIcon message="连接未保存" description={(createConnectionMutation.error ?? updateConnectionMutation.error as Error).message} />
          )}
        </Form>
      </Drawer>

      <Drawer
        title="添加模型 ID"
        open={manualModelOpen}
        width={440}
        onClose={() => { setManualModelOpen(false); modelForm.resetFields() }}
        extra={<Button type="primary" loading={addManualModelMutation.isPending} onClick={() => void modelForm.submit()}>添加模型</Button>}
      >
        <Form form={modelForm} layout="vertical" onFinish={(values) => addManualModelMutation.mutate(values)}>
          <Form.Item name="model_id" label="外部模型 ID" rules={[{ required: true, message: '请输入供应商使用的模型 ID' }]}>
            <Input maxLength={255} placeholder="例如：qwen2.5-14b-instruct" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true, message: '请输入显示名称' }]}>
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item name="context_window" label="上下文长度（可选）">
            <InputNumber min={1} className="model-form-full-width" />
          </Form.Item>
          {addManualModelMutation.isError && <Alert type="error" showIcon message="模型未添加" description={(addManualModelMutation.error as Error).message} />}
        </Form>
      </Drawer>

      <Drawer
        title="创建模型路由"
        open={routeDrawerOpen}
        width={440}
        onClose={() => { setRouteDrawerOpen(false); setRouteModel(undefined); routeForm.resetFields() }}
        extra={<Button type="primary" loading={createRouteMutation.isPending} onClick={() => void routeForm.submit()}>创建路由</Button>}
      >
        {routeModel && (
          <div className="model-route-target">
            <Text type="secondary">连接中的模型</Text>
            <strong>{routeModel.display_name}</strong>
            <code>{routeModel.model_id}</code>
          </div>
        )}
        <Form form={routeForm} layout="vertical" onFinish={(values) => createRouteMutation.mutate(values)}>
          <Form.Item name="alias" label="路由别名" rules={[{ required: true, message: '请输入员工版本中显示的路由别名' }]}>
            <Input maxLength={128} placeholder="例如：客服主模型" />
          </Form.Item>
          <Form.Item name="temperature" label="Temperature（可选）">
            <InputNumber min={0} max={2} step={0.1} className="model-form-full-width" />
          </Form.Item>
          {createRouteMutation.isError && <Alert type="error" showIcon message="路由未创建" description={(createRouteMutation.error as Error).message} />}
        </Form>
      </Drawer>

      <Modal
        title="确认测试模型"
        open={Boolean(testConfirmationRoute)}
        onCancel={() => setTestConfirmationRoute(undefined)}
        onOk={() => testConfirmationRoute && routeTestMutation.mutate(testConfirmationRoute)}
        okText="确认并测试模型"
        cancelText="取消"
        confirmLoading={routeTestMutation.isPending}
        destroyOnHidden
      >
        <div className="model-test-confirmation">
          <strong>{testConfirmationRoute?.alias} · {testConfirmationRoute?.profile.catalog_model_id ?? testConfirmationRoute?.profile.model_name}</strong>
          <Paragraph>系统会向该模型发送一条固定的合成请求，不会使用员工提示词或工具，最多生成 16 个 Token。真实请求可能产生供应商费用。</Paragraph>
          <Alert type="warning" showIcon message="只会在点击“确认并测试模型”后发送请求。关闭此窗口不会创建测试任务。" />
        </div>
        {routeTestMutation.isError && <Alert className="model-test-confirmation-error" type="error" showIcon message="测试任务未创建" description={(routeTestMutation.error as Error).message} />}
      </Modal>
    </section>
  )
}
