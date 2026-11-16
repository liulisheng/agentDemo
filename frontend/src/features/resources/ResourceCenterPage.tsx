import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useMemo, useState } from 'react'

import {
  createHubGrant,
  createHubPackage,
  createHubPackageVersion,
  getAuthorizedHubManifest,
  getDigitalEmployee,
  getHubResourceDetail,
  importHubResource,
  listDigitalEmployees,
  listEmployeeVersions,
  listHubGrants,
  listHubImports,
  listHubResources,
  listSkills,
  listTools,
  publishHubPackage,
  revokeHubGrant,
  revokeHubImport,
  upgradeHubImport,
} from '../../api/client'
import type { HubGrant, HubImport, HubResourceSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'
import { ResourceCatalogPanel } from './ResourceCatalogPanel'

const { Paragraph, Text, Title } = Typography

const resourceTypeLabels: Record<string, string> = {
  DIGITAL_EMPLOYEE: '数字员工',
  SKILL: 'Skill',
  TOOL: 'Tool',
}

interface PublishFormValues {
  resource_type: string
  source_resource_id: string
  source_resource_version: number
  visibility: 'PLATFORM' | 'RESTRICTED'
}

interface GrantFormValues {
  recipient_tenant_id: string
  copy_allowed: boolean
  reference_allowed: boolean
  expires_at?: dayjs.Dayjs
}

/** 资源中心：发现、发布、授权、导入、显式升级和引用撤销。 */
export function ResourceCenterPage() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const canManage = currentIdentity.roleName === '管理员'
  const [activeTab, setActiveTab] = useState<'catalog' | 'discover' | 'published' | 'imports'>('catalog')
  const [keyword, setKeyword] = useState('')
  const [resourceType, setResourceType] = useState<string>()
  const [selectedResource, setSelectedResource] = useState<HubResourceSummary>()
  const [detailVersion, setDetailVersion] = useState<number>()
  const [publishOpen, setPublishOpen] = useState(false)
  const [publishHubId, setPublishHubId] = useState<string>()
  const [grantOpen, setGrantOpen] = useState(false)
  const [publishForm] = Form.useForm<PublishFormValues>()
  const [grantForm] = Form.useForm<GrantFormValues>()
  const [publishType, setPublishType] = useState<string>('SKILL')
  const [publishSourceId, setPublishSourceId] = useState<string>()
  const [publishSourceVersion, setPublishSourceVersion] = useState<number>()

  const directoryScope = activeTab === 'published' ? 'OWNED' : 'DISCOVERABLE'
  const directory = useQuery({
    queryKey: ['hub-resources', fakeUser, directoryScope, keyword, resourceType],
    queryFn: () => listHubResources({
      scope: directoryScope,
      keyword: keyword.trim() || undefined,
      resource_type: resourceType,
      limit: 100,
    }, fakeUser),
    enabled: activeTab === 'discover' || activeTab === 'published',
  })
  const imports = useQuery({
    queryKey: ['hub-imports', fakeUser],
    queryFn: () => listHubImports({ limit: 100 }, fakeUser),
    enabled: activeTab === 'imports',
  })
  const importDirectory = useQuery({
    queryKey: ['hub-resources', fakeUser, 'DISCOVERABLE', 'upgrade'],
    queryFn: () => listHubResources({ limit: 200 }, fakeUser),
    enabled: activeTab === 'imports',
  })
  const selectedVersion = detailVersion ?? selectedResource?.latest_version ?? 1
  const detail = useQuery({
    queryKey: ['hub-resource', fakeUser, selectedResource?.hub_resource_id, selectedVersion],
    queryFn: () => getHubResourceDetail(selectedResource!.hub_resource_id, selectedVersion, fakeUser),
    enabled: Boolean(selectedResource),
  })
  const manifestMode = selectedResource?.is_publisher
    ? 'COPY'
    : detail.data?.available_modes[0]
  const manifest = useQuery({
    queryKey: ['hub-manifest', fakeUser, selectedResource?.hub_resource_id, selectedVersion, manifestMode],
    queryFn: () => getAuthorizedHubManifest(
      selectedResource!.hub_resource_id,
      selectedVersion,
      manifestMode!,
      fakeUser,
    ),
    enabled: Boolean(selectedResource && manifestMode),
  })
  const grants = useQuery({
    queryKey: ['hub-grants', fakeUser, selectedResource?.hub_resource_id, selectedVersion],
    queryFn: () => listHubGrants(selectedResource!.hub_resource_id, selectedVersion, fakeUser),
    enabled: Boolean(selectedResource?.is_publisher && canManage),
  })

  const employeeSourceOptions = useQuery({
    queryKey: ['hub-publish-employees', fakeUser],
    queryFn: () => listDigitalEmployees({ limit: 100 }, fakeUser),
    enabled: publishOpen && publishType === 'DIGITAL_EMPLOYEE',
  })
  const skillSourceOptions = useQuery({
    queryKey: ['hub-publish-skills', fakeUser],
    queryFn: () => listSkills(fakeUser),
    enabled: publishOpen && publishType === 'SKILL',
  })
  const toolSourceOptions = useQuery({
    queryKey: ['hub-publish-tools', fakeUser],
    queryFn: () => listTools(fakeUser),
    enabled: publishOpen && publishType === 'TOOL',
  })
  const employeeDetailForPublish = useQuery({
    queryKey: ['hub-publish-employee', fakeUser, publishSourceId],
    queryFn: () => getDigitalEmployee(publishSourceId!, fakeUser),
    enabled: Boolean(publishOpen && publishType === 'DIGITAL_EMPLOYEE' && publishSourceId),
  })
  const employeeVersionsForPublish = useQuery({
    queryKey: ['hub-publish-employee-versions', fakeUser, publishSourceId],
    queryFn: () => listEmployeeVersions(publishSourceId!, { status: 'PUBLISHED', limit: 100 }, fakeUser),
    enabled: Boolean(publishOpen && publishType === 'DIGITAL_EMPLOYEE' && publishSourceId),
  })

  const invalidateHub = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['hub-resources', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['hub-imports', fakeUser] }),
    queryClient.invalidateQueries({ queryKey: ['hub-grants', fakeUser] }),
  ])

  const importMutation = useMutation({
    mutationFn: (mode: 'COPY' | 'REFERENCE') => importHubResource({
      hub_resource_id: selectedResource!.hub_resource_id,
      hub_version: selectedVersion,
      mode,
    }, fakeUser),
    onSuccess: async (result) => {
      await invalidateHub()
      message.success(`${result.mode} 导入已完成`)
    },
  })

  const upgradeMutation = useMutation({
    mutationFn: (value: { import: HubImport; latest: HubResourceSummary }) =>
      upgradeHubImport(value.import.import_id, value.latest.latest_version, fakeUser),
    onSuccess: async () => {
      await invalidateHub()
      message.success('已创建新的导入代理，请在员工草稿中显式绑定')
    },
  })

  const revokeImportMutation = useMutation({
    mutationFn: (importId: string) => revokeHubImport(importId, fakeUser),
    onSuccess: async () => {
      await invalidateHub()
      message.success('REFERENCE 已撤销')
    },
  })

  const grantMutation = useMutation({
    mutationFn: (values: GrantFormValues) => createHubGrant(
      selectedResource!.hub_resource_id,
      selectedVersion,
      {
        recipient_tenant_id: values.recipient_tenant_id,
        copy_allowed: values.copy_allowed,
        reference_allowed: values.reference_allowed,
        expires_at: values.expires_at?.toISOString() ?? null,
      },
      fakeUser,
    ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['hub-grants', fakeUser, selectedResource?.hub_resource_id] }),
        queryClient.invalidateQueries({ queryKey: ['hub-resources', fakeUser] }),
      ])
      setGrantOpen(false)
      grantForm.resetFields()
      message.success('Grant 已创建')
    },
  })

  const revokeGrantMutation = useMutation({
    mutationFn: (grantId: string) => revokeHubGrant(grantId, fakeUser),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['hub-grants', fakeUser, selectedResource?.hub_resource_id] })
      await queryClient.invalidateQueries({ queryKey: ['hub-resources', fakeUser] })
      message.success('Grant 已撤销')
    },
  })

  const publishMutation = useMutation({
    mutationFn: async (values: PublishFormValues) => {
      const body = {
        resource_type: values.resource_type,
        source_resource_id: values.source_resource_id,
        source_resource_version: values.source_resource_version,
        visibility: values.visibility,
      }
      const draft = publishHubId
        ? await createHubPackageVersion(publishHubId, {
          source_resource_id: body.source_resource_id,
          source_resource_version: body.source_resource_version,
        }, fakeUser)
        : await createHubPackage(body, fakeUser)
      const hubId = String(draft.hub_resource_id)
      const version = Number(draft.version)
      return publishHubPackage(hubId, version, fakeUser)
    },
    onSuccess: async () => {
      await invalidateHub()
      setPublishOpen(false)
      setPublishHubId(undefined)
      publishForm.resetFields()
      message.success('资源版本已发布到资源中心')
    },
  })

  const sourceOptions = useMemo(() => {
    if (publishType === 'DIGITAL_EMPLOYEE') {
      return (employeeSourceOptions.data?.items ?? [])
        .filter((item) => item.status !== 'ARCHIVED')
        .map((item) => ({ value: item.id, label: `${item.name} · ${item.role_name}` }))
    }
    if (publishType === 'SKILL') {
      return (skillSourceOptions.data?.items ?? []).map((item) => ({
        value: item.id,
        label: `${item.name} · v${item.version} · ${item.status}`,
        disabled: item.status !== 'PUBLISHED',
      }))
    }
    return (toolSourceOptions.data?.items ?? []).map((item) => ({
      value: item.id,
      label: `${item.name} · v${item.version} · ${item.status}`,
      disabled: item.status !== 'PUBLISHED',
    }))
  }, [employeeSourceOptions.data?.items, publishType, skillSourceOptions.data?.items, toolSourceOptions.data?.items])

  const currentSourceVersions = useMemo(() => {
    if (!publishSourceId) return []
    if (publishType === 'DIGITAL_EMPLOYEE') {
      return (employeeVersionsForPublish.data?.items ?? []).map((item) => ({
        value: item.version,
        label: `v${item.version} · PUBLISHED`,
      }))
    }
    const resource = publishType === 'SKILL'
      ? skillSourceOptions.data?.items.find((item) => item.id === publishSourceId)
      : toolSourceOptions.data?.items.find((item) => item.id === publishSourceId)
    return resource?.status === 'PUBLISHED'
      ? [{ value: resource.version, label: `v${resource.version} · PUBLISHED` }]
      : []
  }, [employeeVersionsForPublish.data?.items, publishSourceId, publishType, skillSourceOptions.data?.items, toolSourceOptions.data?.items])

  const directoryColumns = useMemo<ColumnsType<HubResourceSummary>>(() => [
    {
      title: '资源',
      key: 'resource',
      render: (_: unknown, item) => (
        <Space direction="vertical" size={2}>
          <Button type="link" className="hub-resource-name" onClick={() => {
            setSelectedResource(item)
            setDetailVersion(item.latest_version)
          }}>{item.name}</Button>
          <Text type="secondary">{item.description}</Text>
        </Space>
      ),
    },
    { title: '类型', dataIndex: 'resource_type', key: 'type', render: (value: string) => resourceTypeLabels[value] ?? value },
    { title: '发布者', dataIndex: 'publisher_name', key: 'publisher' },
    { title: '最新版本', dataIndex: 'latest_version', key: 'version', render: (value: number) => `v${value}` },
    {
      title: '导入方式',
      dataIndex: 'available_modes',
      key: 'modes',
      render: (modes: HubResourceSummary['available_modes']) => modes.length
        ? <Space>{modes.map((mode) => <Tag key={mode} color={mode === 'COPY' ? 'blue' : 'purple'}>{mode}</Tag>)}</Space>
        : <Tag>导入需授权</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, item) => (
        <Space>
          <Button type="link" onClick={() => {
            setSelectedResource(item)
            setDetailVersion(item.latest_version)
          }}>详情</Button>
          {activeTab === 'discover' && canManage && item.available_modes.length > 0 && (
            <Button type="link" onClick={() => {
              setSelectedResource(item)
              setDetailVersion(item.latest_version)
            }}>导入</Button>
          )}
        </Space>
      ),
    },
  ], [activeTab, canManage])

  const selectedGrants = grants.data?.items ?? []
  const importItems = imports.data?.items ?? []
  const sourceLatestById = new Map((importDirectory.data?.items ?? []).map((item) => [item.hub_resource_id, item]))

  return (
    <section className="management-page" aria-label="资源中心">
      <div className="management-page-header">
        <div>
          <Text className="management-eyebrow">租户能力 / 资源中心</Text>
          <Title level={2}>资源中心</Title>
          <Paragraph type="secondary">管理本租户技能与工具，也可发布、发现和导入共享资源。</Paragraph>
        </div>
        {canManage && activeTab === 'published' && (
          <Button type="primary" onClick={() => {
            setPublishHubId(undefined)
            setPublishType('SKILL')
            setPublishSourceId(undefined)
            setPublishSourceVersion(undefined)
            publishForm.resetFields()
            publishForm.setFieldsValue({ resource_type: 'SKILL', visibility: 'PLATFORM' })
            setPublishOpen(true)
          }}>发布资源</Button>
        )}
      </div>

      <Tabs
        className="resource-center-tabs"
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as typeof activeTab)}
        items={[
          { key: 'catalog', label: '技能与工具' },
          { key: 'discover', label: '发现资源' },
          { key: 'published', label: '我发布的' },
          { key: 'imports', label: '我导入的' },
        ]}
      />

      {activeTab === 'catalog' && <ResourceCatalogPanel />}

      {(activeTab === 'discover' || activeTab === 'published') && (
        <Card className="management-card">
          <div className="management-filter-row">
            <Input.Search
              aria-label="搜索共享资源"
              allowClear
              maxLength={128}
              placeholder="搜索名称或描述"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              style={{ width: 320 }}
            />
            <Select
              aria-label="资源类型"
              allowClear
              placeholder="全部资源类型"
              value={resourceType}
              onChange={setResourceType}
              options={[
                { value: 'DIGITAL_EMPLOYEE', label: '数字员工' },
                { value: 'SKILL', label: 'Skill' },
                { value: 'TOOL', label: 'Tool' },
              ]}
              style={{ width: 180 }}
            />
            <Text className="management-stat">{directory.data?.items.length ?? 0} 项资源</Text>
          </div>
          {directory.isLoading && <div className="management-loading">正在加载资源…</div>}
          {directory.isError && <Alert type="error" message={(directory.error as Error).message} />}
          {directory.isSuccess && directory.data.items.length === 0 && (
            <Empty className="management-empty" description={activeTab === 'published' ? '你还没有发布资源' : '暂时没有可发现资源'} />
          )}
          {directory.isSuccess && directory.data.items.length > 0 && (
            <Table<HubResourceSummary> rowKey="hub_resource_id" columns={directoryColumns} dataSource={directory.data.items} pagination={false} />
          )}
        </Card>
      )}

      {activeTab === 'imports' && (
        <Card className="management-card" title="导入到当前租户的资源">
          {imports.isLoading && <div className="management-loading">正在加载导入记录…</div>}
          {imports.isError && <Alert type="error" message={(imports.error as Error).message} />}
          {imports.isSuccess && importItems.length === 0 && <Empty className="management-empty" description="还没有导入资源" />}
          {importItems.map((item) => {
            const latest = sourceLatestById.get(item.hub_resource_id)
            const canUpgrade = Boolean(latest && latest.latest_version > item.hub_version && latest.available_modes.includes(item.mode))
            return (
              <div className="hub-import-row" key={item.import_id}>
                <div>
                  <Space wrap>
                    <Text strong>{latest?.name ?? resourceTypeLabels[item.local_resource_type] ?? item.local_resource_type}</Text>
                    <Tag color={item.mode === 'COPY' ? 'blue' : 'purple'}>{item.mode === 'REFERENCE' ? '只读引用' : '租户副本'}</Tag>
                    <Tag>{item.status}</Tag>
                  </Space>
                  <div className="hub-import-meta">
                    Hub v{item.hub_version} · 本地 {item.local_resource_type} {item.local_resource_id} · 摘要 {item.manifest_sha256.slice(0, 12)}
                  </div>
                </div>
                <Space wrap>
                  {latest && (
                    <Button type="link" onClick={() => {
                      setSelectedResource(latest)
                      setDetailVersion(item.hub_version)
                    }}>查看来源</Button>
                  )}
                  {canUpgrade && canManage && (
                    <Button
                      onClick={() => upgradeMutation.mutate({ import: item, latest: latest! })}
                      loading={upgradeMutation.isPending}
                    >升级到 v{latest!.latest_version}</Button>
                  )}
                  {item.mode === 'REFERENCE' && canManage && (
                    <Popconfirm title="撤销只读引用？" okText="撤销引用" cancelText="取消" onConfirm={() => revokeImportMutation.mutate(item.import_id)}>
                      <Button danger>撤销引用</Button>
                    </Popconfirm>
                  )}
                  {item.mode === 'REFERENCE' && <Text type="secondary">修改请复制到租户</Text>}
                </Space>
              </div>
            )
          })}
          {upgradeMutation.isError && <Alert type="error" message={(upgradeMutation.error as Error).message} />}
          {revokeImportMutation.isError && <Alert type="error" message={(revokeImportMutation.error as Error).message} />}
        </Card>
      )}

      <Drawer
        title={selectedResource?.name}
        open={Boolean(selectedResource)}
        onClose={() => { setSelectedResource(undefined); setDetailVersion(undefined) }}
        width={760}
        destroyOnClose
      >
        {selectedResource && (
          <Space direction="vertical" size={18} className="hub-detail-content">
            {detail.isLoading && <div className="management-loading">正在加载资源详情…</div>}
            {detail.isError && <Alert type="error" message={(detail.error as Error).message} />}
            {detail.data && (
              <>
                <Descriptions bordered size="small" column={2}>
                  <Descriptions.Item label="类型">{resourceTypeLabels[detail.data.resource_type] ?? detail.data.resource_type}</Descriptions.Item>
                  <Descriptions.Item label="发布者">{detail.data.publisher_name}</Descriptions.Item>
                  <Descriptions.Item label="版本">v{detail.data.version}</Descriptions.Item>
                  <Descriptions.Item label="可见范围">{detail.data.visibility}</Descriptions.Item>
                  <Descriptions.Item label="Manifest SHA-256" span={2}>{detail.data.manifest_sha256}</Descriptions.Item>
                  <Descriptions.Item label="依赖数">{detail.data.dependency_count}</Descriptions.Item>
                  <Descriptions.Item label="允许方式">{detail.data.available_modes.join(' / ') || '尚未授权'}</Descriptions.Item>
                </Descriptions>
                <Paragraph>{detail.data.description}</Paragraph>
                {canManage && selectedResource.is_publisher && (
                  <Space wrap>
                    <Button onClick={() => setGrantOpen(true)}>授权租户</Button>
                    <Button onClick={() => {
                      setPublishHubId(selectedResource.hub_resource_id)
                      setPublishType(selectedResource.resource_type)
                      setPublishSourceId(undefined)
                      setPublishSourceVersion(undefined)
                      publishForm.resetFields()
                      publishForm.setFieldsValue({
                        resource_type: selectedResource.resource_type,
                        visibility: 'PLATFORM',
                      })
                      setPublishOpen(true)
                    }}>追加资源版本</Button>
                  </Space>
                )}
                {!selectedResource.is_publisher && canManage && detail.data.available_modes.length > 0 && (
                  <Space>
                    {detail.data.available_modes.includes('COPY') && (
                      <Button type="primary" loading={importMutation.isPending} onClick={() => importMutation.mutate('COPY')}>复制到租户</Button>
                    )}
                    {detail.data.available_modes.includes('REFERENCE') && (
                      <Button loading={importMutation.isPending} onClick={() => importMutation.mutate('REFERENCE')}>引用为只读资源</Button>
                    )}
                  </Space>
                )}
                {!selectedResource.is_publisher && detail.data.available_modes.length === 0 && (
                  <Alert type="info" showIcon message="资源可发现，导入需要发布方 Grant。" />
                )}
              </>
            )}

            {manifestMode && manifest.data && (
              <Card size="small" title={`授权后的 ${manifestMode} 清单`}>
                <Space direction="vertical" size={12} className="hub-detail-content">
                  {manifest.data.manifest.requires_recipient_credentials && (
                    <Alert type="warning" message="该资源依赖接收租户自己的凭据配置。" />
                  )}
                  {manifest.data.manifest.dependencies.length > 0 && (
                    <Table
                      rowKey={(item) => `${item.resource_id}:${item.version}`}
                      size="small"
                      pagination={false}
                      dataSource={manifest.data.manifest.dependencies}
                      columns={[
                        { title: '类型', dataIndex: 'type', key: 'type' },
                        { title: 'Hub 资源 ID', dataIndex: 'resource_id', key: 'id' },
                        { title: '版本', dataIndex: 'version', key: 'version', render: (value: number) => `v${value}` },
                      ]}
                    />
                  )}
                  {Array.isArray(manifest.data.manifest.content.files) && (
                    <div>
                      <Text strong>Skill 文件清单</Text>
                      <List
                        size="small"
                        dataSource={manifest.data.manifest.content.files as Record<string, unknown>[]}
                        renderItem={(item) => (
                          <List.Item>
                            <Text>{String(item.file_path)}</Text>
                            <Text type="secondary">{String(item.size_bytes)} bytes · {String(item.content_sha256).slice(0, 12)}</Text>
                          </List.Item>
                        )}
                      />
                    </div>
                  )}
                </Space>
              </Card>
            )}
            {manifest.isError && <Alert type="error" message={(manifest.error as Error).message} />}

            {selectedResource.is_publisher && canManage && (
              <Card size="small" title={`v${selectedVersion} 接收方 Grant`} extra={<Text type="secondary">只管理当前精确版本</Text>}>
                {grants.isLoading && <div className="management-loading">正在加载授权…</div>}
                {grants.data?.items.map((grant: HubGrant) => (
                  <div className="hub-import-row" key={grant.grant_id}>
                    <div>
                      <Text strong>{grant.recipient_tenant_id}</Text>
                      <div className="hub-import-meta">
                        {grant.copy_allowed ? 'COPY ' : ''}{grant.reference_allowed ? 'REFERENCE ' : ''}· {grant.status}
                        {grant.expires_at ? ` · 到期 ${grant.expires_at}` : ' · 不过期'}
                      </div>
                    </div>
                    {grant.status === 'ACTIVE' && (
                      <Popconfirm title="撤销此 Grant？" okText="确认撤销" cancelText="取消" onConfirm={() => revokeGrantMutation.mutate(grant.grant_id)}>
                        <Button danger size="small">撤销</Button>
                      </Popconfirm>
                    )}
                  </div>
                ))}
                {grants.data?.items.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此版本尚未授权接收租户" />}
                {revokeGrantMutation.isError && <Alert type="error" message={(revokeGrantMutation.error as Error).message} />}
              </Card>
            )}
            {importMutation.isError && <Alert type="error" message={(importMutation.error as Error).message} />}
          </Space>
        )}
      </Drawer>

      <Modal
        title={publishHubId ? '追加资源中心版本' : '发布到资源中心'}
        open={publishOpen}
        onCancel={() => setPublishOpen(false)}
        onOk={() => publishForm.submit()}
        okText="创建并发布"
        cancelText="取消"
        confirmLoading={publishMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={publishForm}
          layout="vertical"
          initialValues={{ resource_type: publishType, visibility: 'PLATFORM' }}
          onValuesChange={(changed) => {
            if (changed.resource_type) {
              setPublishType(changed.resource_type)
              setPublishSourceId(undefined)
              setPublishSourceVersion(undefined)
            }
            if (changed.source_resource_id) {
              setPublishSourceId(changed.source_resource_id)
              setPublishSourceVersion(undefined)
            }
            if (changed.source_resource_version) setPublishSourceVersion(changed.source_resource_version)
          }}
          onFinish={(values) => publishMutation.mutate(values)}
        >
          <Form.Item name="resource_type" label="资源类型" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(publishHubId)}
              options={[
                { value: 'DIGITAL_EMPLOYEE', label: '数字员工' },
                { value: 'SKILL', label: 'Skill' },
                { value: 'TOOL', label: 'Tool' },
              ]}
            />
          </Form.Item>
          <Form.Item name="source_resource_id" label="已发布租户资源" rules={[{ required: true, message: '请选择资源' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              options={sourceOptions}
              placeholder="选择当前租户的已发布资源"
            />
          </Form.Item>
          <Form.Item name="source_resource_version" label="源资源版本" rules={[{ required: true, message: '请选择发布版本' }]}>
            <Select
              disabled={!publishSourceId}
              loading={publishType === 'DIGITAL_EMPLOYEE' && employeeVersionsForPublish.isLoading}
              options={currentSourceVersions}
              placeholder="选择 PUBLISHED 版本"
            />
          </Form.Item>
          {!publishHubId && (
            <Form.Item name="visibility" label="资源可见范围" rules={[{ required: true }]}>
              <Select options={[
                { value: 'PLATFORM', label: '平台可发现（导入仍需授权）' },
                { value: 'RESTRICTED', label: '仅发布租户和授权租户可发现' },
              ]} />
            </Form.Item>
          )}
          {employeeDetailForPublish.data?.latest_published_version === null && publishType === 'DIGITAL_EMPLOYEE' && (
            <Alert type="warning" message="该员工还没有已发布版本。" />
          )}
          {publishMutation.isError && <Alert type="error" message={(publishMutation.error as Error).message} />}
        </Form>
      </Modal>

      <Modal
        title="授权接收租户"
        open={grantOpen}
        onCancel={() => setGrantOpen(false)}
        onOk={() => grantForm.submit()}
        okText="创建 Grant"
        cancelText="取消"
        confirmLoading={grantMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={grantForm}
          layout="vertical"
          initialValues={{ copy_allowed: true, reference_allowed: false }}
          onFinish={(values) => {
            if (!values.copy_allowed && !values.reference_allowed) {
              message.error('至少选择 COPY 或 REFERENCE 一种导入方式')
              return
            }
            grantMutation.mutate(values)
          }}
        >
          <Form.Item name="recipient_tenant_id" label="接收租户 ID" rules={[{ required: true, message: '请输入接收租户 ID' }]}>
            <Input maxLength={26} placeholder="例如：tenant-b" />
          </Form.Item>
          <Form.Item name="copy_allowed" valuePropName="checked">
            <Checkbox>允许 COPY 到租户副本</Checkbox>
          </Form.Item>
          <Form.Item name="reference_allowed" valuePropName="checked">
            <Checkbox>允许 REFERENCE 只读引用</Checkbox>
          </Form.Item>
          <Form.Item name="expires_at" label="过期时间（可选）">
            <DatePicker showTime style={{ width: '100%' }} />
          </Form.Item>
          {grantMutation.isError && <Alert type="error" message={(grantMutation.error as Error).message} />}
        </Form>
      </Modal>
    </section>
  )
}
