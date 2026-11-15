import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Checkbox, Drawer, Empty, List, Radio, Select, Space, Spin, Tabs, Tag, Typography, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'

import {
  getAuthorizedHubManifest,
  getHubResourceDetail,
  importHubResource,
  listHubResources,
  listSkills,
  listTools,
} from '../../api/client'
import type { EmployeeVersionRef, HubResourceSummary, SkillSummary, ToolSummary } from '../../api/client'

const { Text } = Typography

type ResourceKind = 'skill' | 'tool'
type PickerTab = 'tenant' | 'hub'
type ResourceRef = { resource_id: string; version: number }

interface EmployeeResourcePickerDrawerProps {
  open: boolean
  kind: ResourceKind
  employeeName: string
  version: number
  fakeUser: string
  boundRefs: EmployeeVersionRef[]
  saving?: boolean
  onCancel: () => void
  onCreate?: () => void
  onConfirm: (refs: ResourceRef[]) => Promise<void>
}

function toResourceKey(id: string, version: number) {
  return `${id}@${version}`
}

function fromResourceKey(value: string): ResourceRef {
  const separator = value.lastIndexOf('@')
  return { resource_id: value.slice(0, separator), version: Number(value.slice(separator + 1)) }
}

/** 员工详情的类型专属资源选择器；租户资源绑定和 Hub 导入分开确认。 */
export function EmployeeResourcePickerDrawer({
  open,
  kind,
  employeeName,
  version,
  fakeUser,
  boundRefs,
  saving = false,
  onCancel,
  onCreate,
  onConfirm,
}: EmployeeResourcePickerDrawerProps) {
  const [activeTab, setActiveTab] = useState<PickerTab>('tenant')
  const [selectedTenantKeys, setSelectedTenantKeys] = useState<string[]>([])
  const [selectedHubId, setSelectedHubId] = useState<string>()
  const [hubMode, setHubMode] = useState<'COPY' | 'REFERENCE'>('COPY')
  const [submitError, setSubmitError] = useState<string>()
  const resourceName = kind === 'skill' ? 'Skill' : 'Tool'
  const resourcePlural = kind === 'skill' ? 'Skills' : 'Tools'
  const resourceType = kind === 'skill' ? 'SKILL' : 'TOOL'

  useEffect(() => {
    if (open) {
      setActiveTab('tenant')
      setSelectedTenantKeys([])
      setSelectedHubId(undefined)
      setHubMode('COPY')
      setSubmitError(undefined)
    }
  }, [open, kind, version])

  const skills = useQuery({
    queryKey: ['skills', fakeUser],
    queryFn: () => listSkills(fakeUser, true),
    enabled: open && kind === 'skill' && activeTab === 'tenant',
  })
  const tools = useQuery({
    queryKey: ['tools', fakeUser],
    queryFn: () => listTools(fakeUser),
    enabled: open && kind === 'tool' && activeTab === 'tenant',
  })
  const hubResources = useQuery({
    queryKey: ['hub-resources', fakeUser, resourceType, 'employee-picker'],
    queryFn: () => listHubResources({ resource_type: resourceType, limit: 200 }, fakeUser),
    enabled: open && activeTab === 'hub',
  })
  const selectedHub = (hubResources.data?.items ?? []).find((item) => item.hub_resource_id === selectedHubId)
  const hubDetail = useQuery({
    queryKey: ['hub-resource', fakeUser, selectedHub?.hub_resource_id, selectedHub?.latest_version],
    queryFn: () => getHubResourceDetail(selectedHub!.hub_resource_id, selectedHub!.latest_version, fakeUser),
    enabled: open && activeTab === 'hub' && Boolean(selectedHub),
  })
  const hubManifest = useQuery({
    queryKey: ['hub-manifest', fakeUser, selectedHub?.hub_resource_id, selectedHub?.latest_version, hubMode],
    queryFn: () => getAuthorizedHubManifest(
      selectedHub!.hub_resource_id,
      selectedHub!.latest_version,
      hubMode,
      fakeUser,
    ),
    enabled: (
      open
      && activeTab === 'hub'
      && Boolean(selectedHub?.available_modes.includes(hubMode))
    ),
  })

  const currentBoundIds = useMemo(
    () => new Set(boundRefs.map((item) => item.skill_id ?? item.tool_id ?? item.resource_id ?? '')),
    [boundRefs],
  )
  const availableTenantResources = useMemo(() => {
    const items = kind === 'skill' ? skills.data?.items ?? [] : tools.data?.items ?? []
    return items.filter((item) => item.status === 'PUBLISHED')
  }, [kind, skills.data?.items, tools.data?.items])

  const submitTenantBindings = async () => {
    setSubmitError(undefined)
    try {
      await onConfirm(selectedTenantKeys.map(fromResourceKey))
    } catch (error) {
      setSubmitError((error as Error).message)
    }
  }

  const importAndBind = async () => {
    if (!selectedHub) return
    setSubmitError(undefined)
    try {
      const imported = await importHubResource({
        hub_resource_id: selectedHub.hub_resource_id,
        hub_version: selectedHub.latest_version,
        mode: hubMode,
      }, fakeUser)
      await onConfirm([{ resource_id: imported.local_resource_id, version: imported.local_resource_version }])
    } catch (error) {
      setSubmitError((error as Error).message)
    }
  }

  const renderTenantResources = () => {
    const query = kind === 'skill' ? skills : tools
    if (query.isLoading) return <div className="management-loading"><Spin /> 正在读取本租户 {resourceName}…</div>
    if (query.isError) return <Alert type="error" showIcon message={`${resourceName} 列表读取失败`} description={(query.error as Error).message} />
    const resources = availableTenantResources as (SkillSummary | ToolSummary)[]
    const available = resources.filter((item) => !currentBoundIds.has(item.id))
    if (available.length === 0) {
      return <Empty description={`没有可添加的已发布 ${resourceName}；草稿版本需先在资源管理中发布。`} />
    }
    return (
      <Checkbox.Group value={selectedTenantKeys} onChange={(values) => setSelectedTenantKeys(values as string[])}>
        <div className="employee-resource-picker-list">
          {available.map((item) => (
            <div className="employee-resource-picker-option" key={toResourceKey(item.id, item.version)}>
              <Checkbox value={toResourceKey(item.id, item.version)}>
                <span className="employee-resource-picker-option-copy">
                  <Text strong>{item.name}</Text>
                  <Text type="secondary">v{item.version} · PUBLISHED</Text>
                </span>
              </Checkbox>
            </div>
          ))}
        </div>
      </Checkbox.Group>
    )
  }

  const renderHubResources = () => {
    if (hubResources.isLoading) return <div className="management-loading"><Spin /> 正在读取资源中心 {resourceName}…</div>
    if (hubResources.isError) return <Alert type="error" showIcon message="资源中心列表读取失败" description={(hubResources.error as Error).message} />
    if (!hubResources.data?.items.length) return <Empty description={`资源中心暂无可发现的 ${resourceName}`} />
    return (
      <div className="employee-resource-picker-hub">
        <Radio.Group
          value={selectedHubId}
          onChange={(event) => {
            const next = (hubResources.data?.items ?? []).find((item) => item.hub_resource_id === event.target.value)
            setSelectedHubId(event.target.value)
            setHubMode(next?.available_modes[0] ?? 'COPY')
          }}
        >
          <div className="employee-resource-picker-list">
            {hubResources.data.items.map((item: HubResourceSummary) => (
              <div className="employee-resource-picker-option" key={item.hub_resource_id}>
                <Radio value={item.hub_resource_id}>
                  <span className="employee-resource-picker-option-copy">
                    <Text strong>{item.name}</Text>
                    <Text type="secondary">v{item.latest_version} · {item.publisher_name}</Text>
                  </span>
                </Radio>
              </div>
            ))}
          </div>
        </Radio.Group>
        {selectedHub && (
          <Card size="small" className="employee-resource-picker-detail" title={selectedHub.name}>
            {hubDetail.isLoading
              ? <Spin size="small" />
              : hubDetail.isError
                ? <Alert type="error" message={(hubDetail.error as Error).message} />
                : <Space direction="vertical" size={6}>
                  <Text type="secondary">固定版本 v{selectedHub.latest_version} · 依赖 {hubDetail.data?.dependency_count ?? 0} 项</Text>
                  {hubManifest.isLoading && <Spin size="small" />}
                  {hubManifest.isError && <Text type="danger">依赖详情读取失败：{(hubManifest.error as Error).message}</Text>}
                  {(hubManifest.data?.manifest.dependencies.length ?? 0) > 0 && (
                    <List
                      size="small"
                      header="已授权依赖"
                      dataSource={hubManifest.data!.manifest.dependencies}
                      renderItem={(dependency) => (
                        <List.Item>
                          <Text type="secondary">
                            {dependency.type} · {dependency.resource_id} · v{dependency.version} · {dependency.required ? '必需' : '可选'}
                          </Text>
                        </List.Item>
                      )}
                    />
                  )}
                  {selectedHub.available_modes.length > 0 ? (
                    <Select
                      aria-label="资源中心导入方式"
                      value={hubMode}
                      onChange={setHubMode}
                      options={selectedHub.available_modes.map((mode) => ({
                        value: mode,
                        label: mode === 'COPY' ? 'COPY · 创建租户副本' : 'REFERENCE · 只读引用',
                      }))}
                    />
                  ) : (
                    <Text type="secondary">平台可发现，当前租户尚无 COPY 或 REFERENCE 授权</Text>
                  )}
                </Space>}
          </Card>
        )}
      </div>
    )
  }

  const canImportHub = Boolean(selectedHub?.available_modes.includes(hubMode))

  return (
    <Drawer
      title={`添加 ${resourceName} 到 ${employeeName} · v${version}`}
      open={open}
      onClose={onCancel}
      width={640}
      destroyOnClose
      footer={(
        <div className="employee-resource-picker-footer">
          {kind === 'skill' && onCreate ? (
            <Button type="link" onClick={onCreate}>新建 Skill</Button>
          ) : <span />}
          <Space>
            <Button onClick={onCancel}>取消</Button>
            {activeTab === 'tenant' ? (
              <Button type="primary" disabled={!selectedTenantKeys.length} loading={saving} onClick={submitTenantBindings}>
                绑定所选 {resourceName}
              </Button>
            ) : (
              <Button type="primary" disabled={!canImportHub} loading={saving} onClick={importAndBind}>
                导入并绑定 {resourceName}
              </Button>
            )}
          </Space>
        </div>
      )}
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as PickerTab)}
        items={[
          { key: 'tenant', label: `本租户 ${resourcePlural}`, children: renderTenantResources() },
          { key: 'hub', label: '资源中心', children: renderHubResources() },
        ]}
      />
      {activeTab === 'hub' && selectedHub && selectedHub.available_modes.length > 0 && (
        <Alert
          className="employee-resource-picker-hub-note"
          type={hubMode === 'REFERENCE' ? 'info' : 'success'}
          showIcon
          message={hubMode === 'REFERENCE' ? 'REFERENCE 在运行时只读；修改需先 COPY 到本租户。' : 'COPY 会创建本租户副本，后续编辑不影响资源来源。'}
        />
      )}
      {submitError && <Alert className="employee-resource-picker-error" type="error" showIcon message={submitError} />}
    </Drawer>
  )
}
