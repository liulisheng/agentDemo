import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  createExecutionProfile,
  forkExecutionProfileVersion,
  getEffectiveEngineProfileDefault,
  getEffectiveModelRouteDefault,
  getEffectiveRuntimeStrategyDefault,
  getEngineProfileDefault,
  getModelRouteDefault,
  getRuntimeStrategyDefault,
  listEngineProviders,
  listExecutionProfileVersions,
  listExecutionProfiles,
  listSelectableModelRoutes,
  publishExecutionProfileVersion,
  setEngineProfileDefault,
  setModelRouteDefault,
  setRuntimeStrategyDefault,
  updateExecutionProfileDraft,
} from '../../api/client'
import type {
  EngineProviderSummary,
  ExecutionProfileSummary,
  RuntimeDefaultBinding,
  RuntimeOptions,
} from '../../api/client'
import { useTenantContext } from '../management/TenantContext'
import { RuntimeStrategyPanel } from '../platform/RuntimeStrategyPanel'

const { Paragraph, Text } = Typography

function profileKey(profile: ExecutionProfileSummary) {
  return `${profile.id}#${profile.version ?? 0}`
}

function parseProfileKey(value?: string) {
  if (!value) return { profile_id: null, profile_version: null }
  const separator = value.lastIndexOf('#')
  if (separator < 0) return { profile_id: null, profile_version: null }
  return {
    profile_id: value.slice(0, separator),
    profile_version: Number(value.slice(separator + 1)),
  }
}

function bindingProfileKey(binding: RuntimeDefaultBinding | null | undefined) {
  return binding?.profile_id && binding.profile_version
    ? `${binding.profile_id}#${binding.profile_version}`
    : undefined
}

function providerLabel(provider: EngineProviderSummary) {
  return `${provider.display_name} · ${provider.execution_mode === 'SIMULATION' ? '演示模式' : '已启用'}`
}

export function DefaultRuntimeEnvironmentTab() {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const isAdmin = currentIdentity.roleName === '管理员'
  const [defaultScope, setDefaultScope] = useState<'user' | 'tenant'>('user')
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null)
  const [userProfileValue, setUserProfileValue] = useState<string>()
  const [tenantProfileValue, setTenantProfileValue] = useState<string>()
  const [userRouteValue, setUserRouteValue] = useState<string>()
  const [tenantRouteValue, setTenantRouteValue] = useState<string>()
  const [userRuntimeOptions, setUserRuntimeOptions] = useState<RuntimeOptions>({})
  const [tenantRuntimeOptions, setTenantRuntimeOptions] = useState<RuntimeOptions>({})
  const [profileModal, setProfileModal] = useState<
    | { mode: 'create' }
    | { mode: 'edit'; profile: ExecutionProfileSummary }
    | null
  >(null)
  const [profileForm] = Form.useForm()

  const providerQuery = useQuery({
    queryKey: ['engine-providers', fakeUser],
    queryFn: () => listEngineProviders(fakeUser),
  })
  const baseProfilesQuery = useQuery({
    queryKey: ['execution-profiles', fakeUser],
    queryFn: () => listExecutionProfiles(fakeUser),
  })
  const profileRows = baseProfilesQuery.data?.items ?? []
  const profileVersionsQuery = useQuery({
    queryKey: ['execution-profile-versions', fakeUser, profileRows.map((item) => item.id).join(',')],
    queryFn: async () => Promise.all(profileRows.map((item) => listExecutionProfileVersions(item.id, fakeUser))),
    enabled: profileRows.length > 0,
  })
  const routesQuery = useQuery({
    queryKey: ['selectable-model-routes', fakeUser],
    queryFn: () => listSelectableModelRoutes(fakeUser),
  })
  const effectiveEngineQuery = useQuery({
    queryKey: ['runtime-default-engine-effective', fakeUser],
    queryFn: () => getEffectiveEngineProfileDefault(fakeUser),
  })
  const effectiveRouteQuery = useQuery({
    queryKey: ['runtime-default-route-effective', fakeUser],
    queryFn: () => getEffectiveModelRouteDefault(fakeUser),
  })
  const userEngineQuery = useQuery({
    queryKey: ['runtime-default-engine', fakeUser, 'user'],
    queryFn: () => getEngineProfileDefault('user', fakeUser),
  })
  const tenantEngineQuery = useQuery({
    queryKey: ['runtime-default-engine', fakeUser, 'tenant'],
    queryFn: () => getEngineProfileDefault('tenant', fakeUser),
  })
  const userRouteQuery = useQuery({
    queryKey: ['runtime-default-route', fakeUser, 'user'],
    queryFn: () => getModelRouteDefault('user', fakeUser),
  })
  const tenantRouteQuery = useQuery({
    queryKey: ['runtime-default-route', fakeUser, 'tenant'],
    queryFn: () => getModelRouteDefault('tenant', fakeUser),
  })
  const effectiveStrategyQuery = useQuery({
    queryKey: ['runtime-default-strategy-effective', fakeUser],
    queryFn: () => getEffectiveRuntimeStrategyDefault(fakeUser),
  })
  const userStrategyQuery = useQuery({
    queryKey: ['runtime-default-strategy', fakeUser, 'user'],
    queryFn: () => getRuntimeStrategyDefault('user', fakeUser),
  })
  const tenantStrategyQuery = useQuery({
    queryKey: ['runtime-default-strategy', fakeUser, 'tenant'],
    queryFn: () => getRuntimeStrategyDefault('tenant', fakeUser),
  })

  const allProfiles = useMemo(
    () => profileVersionsQuery.data?.flatMap((page) => page.items) ?? [],
    [profileVersionsQuery.data],
  )
  const profileByVersion = useMemo(
    () => new Map(allProfiles.map((item) => [profileKey(item), item])),
    [allProfiles],
  )
  const latestById = useMemo(
    () => new Map(profileRows.map((item) => [item.id, item])),
    [profileRows],
  )
  const profileOptions = allProfiles
    .filter((item) => item.status === 'PUBLISHED' && item.engine_available && item.runtime_policy_supported !== false && item.version !== undefined)
    .map((item) => ({
      value: profileKey(item),
      label: `${item.engine_name} · ${item.id} · v${item.version}`,
    }))
  const routeRows = routesQuery.data?.items ?? []
  const routeOptions = routeRows
    .filter((item) => item.status === 'ACTIVE')
    .map((item) => ({ value: item.route_id, label: `${item.alias} · ${item.profile.model_name}` }))
  const providers = providerQuery.data?.items ?? []
  const providerOptions = providers.map((item) => ({ value: item.engine_name, label: providerLabel(item) }))
  const pageLoading = [
    providerQuery.isPending,
    baseProfilesQuery.isPending,
    routesQuery.isPending,
    userEngineQuery.isPending,
    tenantEngineQuery.isPending,
    userRouteQuery.isPending,
    tenantRouteQuery.isPending,
    effectiveEngineQuery.isPending,
    effectiveRouteQuery.isPending,
    effectiveStrategyQuery.isPending,
    userStrategyQuery.isPending,
    tenantStrategyQuery.isPending,
  ].some(Boolean)
  const pageError = [
    providerQuery.error,
    baseProfilesQuery.error,
    profileVersionsQuery.error,
    routesQuery.error,
    userEngineQuery.error,
    tenantEngineQuery.error,
    userRouteQuery.error,
    tenantRouteQuery.error,
    effectiveEngineQuery.error,
    effectiveRouteQuery.error,
    effectiveStrategyQuery.error,
    userStrategyQuery.error,
    tenantStrategyQuery.error,
  ].find(Boolean)

  useEffect(() => setUserProfileValue(bindingProfileKey(userEngineQuery.data?.binding)), [userEngineQuery.data])
  useEffect(() => setTenantProfileValue(bindingProfileKey(tenantEngineQuery.data?.binding)), [tenantEngineQuery.data])
  useEffect(() => setUserRouteValue(userRouteQuery.data?.binding?.route_id), [userRouteQuery.data])
  useEffect(() => setTenantRouteValue(tenantRouteQuery.data?.binding?.route_id), [tenantRouteQuery.data])
  useEffect(() => setUserRuntimeOptions(userStrategyQuery.data?.binding?.runtime_options ?? {}), [userStrategyQuery.data])
  useEffect(() => setTenantRuntimeOptions(tenantStrategyQuery.data?.binding?.runtime_options ?? {}), [tenantStrategyQuery.data])
  useEffect(() => {
    if (!isAdmin && defaultScope === 'tenant') setDefaultScope('user')
  }, [defaultScope, isAdmin])

  const invalidateDefaults = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['runtime-default-engine', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['runtime-default-route', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['runtime-default-engine-effective', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['runtime-default-route-effective', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['runtime-default-strategy', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['runtime-default-strategy-effective', fakeUser] }),
    ])
  }
  const invalidateProfiles = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['execution-profiles', fakeUser] }),
      queryClient.invalidateQueries({ queryKey: ['execution-profile-versions', fakeUser] }),
    ])
  }
  const retryRuntimeDirectory = () => {
    void Promise.all([
      providerQuery.refetch(),
      baseProfilesQuery.refetch(),
      profileVersionsQuery.refetch(),
      routesQuery.refetch(),
      effectiveEngineQuery.refetch(),
      effectiveRouteQuery.refetch(),
      effectiveStrategyQuery.refetch(),
      userEngineQuery.refetch(),
      tenantEngineQuery.refetch(),
      userRouteQuery.refetch(),
      tenantRouteQuery.refetch(),
      userStrategyQuery.refetch(),
      tenantStrategyQuery.refetch(),
    ])
  }

  const saveEngineMutation = useMutation({
    mutationFn: async ({ scope, value }: { scope: 'user' | 'tenant'; value?: string }) =>
      setEngineProfileDefault(scope, parseProfileKey(value), fakeUser),
    onSuccess: async (_value, action) => {
      await invalidateDefaults()
      const feedback = action.scope === 'user' ? '个人引擎默认已保存' : '租户引擎默认已保存'
      setSaveFeedback(feedback)
      message.success(feedback)
    },
    onError: (error) => setSaveFeedback(error instanceof Error ? error.message : '引擎默认保存失败'),
  })
  const saveRouteMutation = useMutation({
    mutationFn: ({ scope, value }: { scope: 'user' | 'tenant'; value?: string }) =>
      setModelRouteDefault(scope, value || null, fakeUser),
    onSuccess: async (_value, action) => {
      await invalidateDefaults()
      const feedback = action.scope === 'user' ? '个人模型默认已保存' : '租户模型默认已保存'
      setSaveFeedback(feedback)
      message.success(feedback)
    },
    onError: (error) => setSaveFeedback(error instanceof Error ? error.message : '模型默认保存失败'),
  })
  const saveStrategyMutation = useMutation({
    mutationFn: ({ scope, value }: { scope: 'user' | 'tenant'; value: RuntimeOptions }) =>
      setRuntimeStrategyDefault(scope, value, fakeUser),
    onSuccess: async (_value, action) => {
      await invalidateDefaults()
      const feedback = action.scope === 'user' ? '个人回答策略默认已保存' : '租户回答策略默认已保存'
      setSaveFeedback(feedback)
      message.success(feedback)
    },
    onError: (error) => setSaveFeedback(error instanceof Error ? error.message : '回答策略默认保存失败'),
  })
  const createProfileMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => createExecutionProfile({
      engine_name: String(values.engine_name),
      approval_policy: 'none',
    }, fakeUser),
    onSuccess: async () => {
      setProfileModal(null)
      profileForm.resetFields()
      await invalidateProfiles()
      message.success('ExecutionProfile 已创建并发布')
    },
  })
  const forkProfileMutation = useMutation({
    mutationFn: (profile: ExecutionProfileSummary) => {
      if (profile.version === undefined) throw new Error('Profile 缺少精确版本号')
      return forkExecutionProfileVersion(profile.id, profile.version, fakeUser)
    },
    onSuccess: async (draft) => {
      await invalidateProfiles()
      setProfileModal({ mode: 'edit', profile: draft })
      profileForm.setFieldsValue({
        engine_name: draft.engine_name,
        approval_policy: draft.approval_policy ?? 'none',
        workspace_profile_id: draft.workspace_profile_id ?? undefined,
        workspace_profile_version: draft.workspace_profile_version ?? 1,
        sandbox_profile_id: draft.sandbox_profile_id ?? undefined,
        sandbox_profile_version: draft.sandbox_profile_version ?? 1,
      })
      message.info(`已创建 ${draft.id} 的 v${draft.version} 草稿；保存并发布后再显式切换默认版本。`)
    },
  })
  const updateProfileMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => {
      if (!profileModal || profileModal.mode !== 'edit') throw new Error('没有可编辑的草稿')
      const { profile } = profileModal
      if (profile.version === undefined) throw new Error('Profile 草稿缺少精确版本号')
      return updateExecutionProfileDraft(profile.id, profile.version, {
        engine_name: String(values.engine_name),
        approval_policy: 'none',
        workspace_profile_id: null,
        workspace_profile_version: null,
        sandbox_profile_id: null,
        sandbox_profile_version: null,
      }, fakeUser)
    },
    onSuccess: async (draft) => {
      await invalidateProfiles()
      setProfileModal({ mode: 'edit', profile: draft })
      message.success('ExecutionProfile 草稿已保存')
    },
  })
  const publishProfileMutation = useMutation({
    mutationFn: (profile: ExecutionProfileSummary) => {
      if (profile.version === undefined) throw new Error('Profile 缺少精确版本号')
      return publishExecutionProfileVersion(profile.id, profile.version, fakeUser)
    },
    onSuccess: async () => {
      setProfileModal(null)
      await invalidateProfiles()
      message.success('ExecutionProfile 新版本已发布；默认绑定保持原版本')
    },
  })

  const profileColumns: ColumnsType<ExecutionProfileSummary> = [
    {
      title: 'ExecutionProfile',
      key: 'profile',
      render: (_value, item) => (
        <span className="model-route-name">
          <strong>{item.engine_name}</strong>
          <code>{item.id}</code>
        </span>
      ),
    },
    { title: '版本', dataIndex: 'version', width: 100, render: (value: number | undefined) => value ? `v${value}` : '—' },
    {
      title: '状态', dataIndex: 'status', width: 120,
      render: (status: string, item) => (
        <Space size={4}>
          <Tag color={status === 'PUBLISHED' ? 'success' : 'gold'}>{status === 'PUBLISHED' ? '已发布' : '草稿'}</Tag>
          {item.engine_execution_mode === 'SIMULATION' && <Tag color="blue">演示</Tag>}
          {!item.engine_available && <Tag color="error">引擎未启用</Tag>}
          {item.runtime_policy_supported === false && <Tag color="warning">环境策略待接入</Tag>}
        </Space>
      ),
    },
    {
      title: '环境策略', key: 'policy',
      render: (_value, item) => (
        <Text type="secondary">
          {item.workspace_profile_id ? `Workspace ${item.workspace_profile_id} · ` : ''}
          {item.sandbox_profile_id ? `Sandbox ${item.sandbox_profile_id} · ` : ''}
          审批 {item.approval_policy ?? 'none'}
        </Text>
      ),
    },
    {
      title: '操作', key: 'action', width: 190,
      render: (_value, item) => {
        const latest = latestById.get(item.id)
        const isLatest = item.version !== undefined && latest?.version === item.version
        return isAdmin && isLatest ? (
          <Space size={4}>
            {item.status === 'PUBLISHED' ? (
              <Button type="link" onClick={() => forkProfileMutation.mutate(item)} loading={forkProfileMutation.isPending}>
                创建新版本
              </Button>
            ) : (
              <>
                <Button type="link" onClick={() => {
                  setProfileModal({ mode: 'edit', profile: item })
                  profileForm.setFieldsValue({
                    engine_name: item.engine_name,
                    approval_policy: item.approval_policy ?? 'none',
                    workspace_profile_id: item.workspace_profile_id ?? undefined,
                    workspace_profile_version: item.workspace_profile_version ?? 1,
                    sandbox_profile_id: item.sandbox_profile_id ?? undefined,
                    sandbox_profile_version: item.sandbox_profile_version ?? 1,
                  })
                }}>
                  编辑草稿
                </Button>
                <Button type="link" onClick={() => publishProfileMutation.mutate(item)} loading={publishProfileMutation.isPending}>
                  发布
                </Button>
              </>
            )}
          </Space>
        ) : null
      },
    },
  ]

  const effectiveEngine = effectiveEngineQuery.data
  const effectiveRoute = effectiveRouteQuery.data
  const profileLabel = (id?: string | null, version?: number | null) => {
    if (!id || !version) return '未设置'
    const item = profileByVersion.get(`${id}#${version}`)
    return item ? `${item.engine_name} · ${id} · v${version}` : 'Profile 不可用'
  }
  const routeLabel = (routeId?: string | null) => {
    if (!routeId) return '未设置'
    const route = routeRows.find((item) => item.route_id === routeId)
    return route ? `${route.alias} · ${route.profile.model_name}` : '路由不可用'
  }
  const selectedProfileValue = defaultScope === 'user' ? userProfileValue : tenantProfileValue
  const selectedRouteValue = defaultScope === 'user' ? userRouteValue : tenantRouteValue
  const selectedRuntimeOptions = defaultScope === 'user' ? userRuntimeOptions : tenantRuntimeOptions
  const setSelectedRuntimeOptions = (value: RuntimeOptions) => {
    if (defaultScope === 'user') setUserRuntimeOptions(value)
    else setTenantRuntimeOptions(value)
  }
  const setSelectedProfileValue = (value?: string) => {
    if (defaultScope === 'user') setUserProfileValue(value)
    else setTenantProfileValue(value)
  }
  const setSelectedRouteValue = (value?: string) => {
    if (defaultScope === 'user') setUserRouteValue(value)
    else setTenantRouteValue(value)
  }
  const effectiveProvider = providers.find(
    (provider) => provider.engine_name === effectiveEngine?.profile?.engine_name,
  )
  const incompatibleEffectiveDefaults = Boolean(
    effectiveEngine?.profile && effectiveRoute?.route
    && !effectiveProvider?.supported_model_protocols.includes(effectiveRoute.route.protocol),
  )

  return (
    <div className="default-runtime-environment">
      {pageLoading ? (
        <div className="management-loading"><Spin /><span>正在读取运行环境</span></div>
      ) : pageError ? (
        <Alert type="error" showIcon message="无法读取默认运行环境" description={(pageError as Error).message} action={<Button onClick={retryRuntimeDirectory}>重试</Button>} />
      ) : (
        <div className="runtime-default-layout">
          <Card
            className="management-card runtime-default-form-card"
            title={<span>默认运行环境 <Text type="secondary">仅影响后续新 Session</Text></span>}
          >
            <Paragraph className="runtime-default-explainer">
              ExecutionProfile 只决定执行引擎与策略，ModelRoute 单独选择。更换引擎不会替换模型；Session 创建前校验兼容性。
            </Paragraph>
            <Form layout="vertical" className="runtime-default-form">
              <Form.Item label="默认作用域">
                <Select
                  aria-label="默认作用域"
                  value={defaultScope}
                  options={isAdmin
                    ? [
                      { value: 'user', label: '个人默认（当前用户）' },
                      { value: 'tenant', label: '租户默认（成员回退值）' },
                    ]
                    : [{ value: 'user', label: '个人默认（当前用户）' }]}
                  onChange={(value: 'user' | 'tenant') => {
                    setDefaultScope(value)
                    setSaveFeedback(null)
                  }}
                />
              </Form.Item>
              <Form.Item label="默认执行引擎（ExecutionProfile）">
                <Select
                  aria-label="默认执行引擎（ExecutionProfile）"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="不设置（继承下一作用域）"
                  value={selectedProfileValue}
                  options={profileOptions}
                  onChange={setSelectedProfileValue}
                />
              </Form.Item>
              <Form.Item label="默认模型（ModelRoute）">
                <Select
                  aria-label="默认模型（ModelRoute）"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="不设置（继承下一作用域）"
                  value={selectedRouteValue}
                  options={routeOptions}
                  onChange={setSelectedRouteValue}
                />
              </Form.Item>
              <RuntimeStrategyPanel
                canEdit={defaultScope === 'user' || isAdmin}
                draft={selectedRuntimeOptions}
                effective={effectiveStrategyQuery.data?.runtime_options ?? {}}
                source={effectiveStrategyQuery.data?.source}
                engineLabel={effectiveEngine?.profile ? `${effectiveEngine.profile.engine_name} · v${effectiveEngine.profile.version}` : '未设置'}
                routeLabel={effectiveRoute?.route ? `${effectiveRoute.route.alias} · ${effectiveRoute.route.model_name}` : '未设置'}
                capabilities={effectiveProvider?.capabilities}
                reasoningUnsupported={selectedRuntimeOptions.reasoning_mode === 'DEEP' && effectiveProvider?.capabilities.reasoning === false}
                incompatible={incompatibleEffectiveDefaults}
                onChange={setSelectedRuntimeOptions}
              />
            </Form>
            <div className="runtime-default-priority-note" aria-label="默认解析优先级">
              <span>优先级</span><strong>Deployment 覆盖 → 个人默认 → 租户默认 → 平台/系统</strong>
            </div>
            <div className="runtime-default-effective-strip" aria-label="当前身份解析结果">
              <span>当前身份解析</span>
              <span>引擎：{effectiveEngine?.profile ? `${effectiveEngine.profile.engine_name} · v${effectiveEngine.profile.version}` : '未设置'} <Tag>{effectiveEngine?.source ?? '无来源'}</Tag></span>
              <span>模型：{effectiveRoute?.route ? `${effectiveRoute.route.alias} · ${effectiveRoute.route.model_name}` : '未设置'} <Tag>{effectiveRoute?.source ?? '无来源'}</Tag></span>
              <span>回答策略：{effectiveStrategyQuery.data?.runtime_options?.reasoning_mode ?? 'AUTO'} <Tag>{effectiveStrategyQuery.data?.source ?? '系统默认'}</Tag></span>
            </div>
            {incompatibleEffectiveDefaults && (
              <Alert className="runtime-default-warning" type="warning" showIcon message="当前引擎和模型路由协议不兼容" description="系统不会自动替换模型；新建 Session 前需要调整其中一项。" />
            )}
            {effectiveEngine?.profile && effectiveEngine.profile.runtime_policy_supported === false && (
              <Alert className="runtime-default-warning" type="warning" showIcon message="当前默认 Profile 含有尚未接入 Worker 的环境字段" description={`暂不支持：${effectiveEngine.profile.unsupported_runtime_fields?.join('、') ?? '运行策略'}。请创建不含这些字段的新版本并显式改绑。`} />
            )}
            {providers.some((provider) => provider.execution_mode === 'SIMULATION') && (
              <Text className="runtime-default-simulation-note" type="secondary">标有“演示”的引擎使用模拟 Runtime，不会调用真实模型服务。</Text>
            )}
            {!profileOptions.length && <Text className="runtime-default-empty-hint" role="status">没有可用的已发布引擎 Profile，请先创建并发布一个方案。</Text>}
            {!routeOptions.length && <Text className="runtime-default-empty-hint" role="status">没有可用模型路由，请先在 <Link to="/enterprise/models?tab=routes">模型路由</Link> 中创建并启用路由。</Text>}
            {!isAdmin && (
              <Text className="runtime-default-member-note" type="secondary">
                租户默认只读：{profileLabel(tenantEngineQuery.data?.binding?.profile_id, tenantEngineQuery.data?.binding?.profile_version)} · {routeLabel(tenantRouteQuery.data?.binding?.route_id)}
              </Text>
            )}
            {saveFeedback && <div className="runtime-default-feedback" role="status">{saveFeedback}</div>}
            {(saveEngineMutation.isError || saveRouteMutation.isError || saveStrategyMutation.isError) && (
              <Alert className="runtime-default-action-error" type="error" showIcon message="默认配置保存失败" description={((saveEngineMutation.error ?? saveRouteMutation.error ?? saveStrategyMutation.error) as Error).message} />
            )}
            <div className="runtime-default-save-actions">
              <Text type="secondary">{defaultScope === 'user' ? `仅影响 ${currentIdentity.label} 新建的 Session` : '没有个人默认的成员会继承此值'}</Text>
              <Space>
                <Button
                  loading={saveEngineMutation.isPending}
                  disabled={defaultScope === 'tenant' && !isAdmin}
                  onClick={() => saveEngineMutation.mutate({ scope: defaultScope, value: selectedProfileValue })}
                >保存引擎默认</Button>
                <Button
                  type="primary"
                  loading={saveRouteMutation.isPending}
                  disabled={defaultScope === 'tenant' && !isAdmin}
                  onClick={() => saveRouteMutation.mutate({ scope: defaultScope, value: selectedRouteValue })}
                >保存模型默认</Button>
                <Button
                  loading={saveStrategyMutation.isPending}
                  disabled={defaultScope === 'tenant' && !isAdmin}
                  onClick={() => saveStrategyMutation.mutate({ scope: defaultScope, value: selectedRuntimeOptions })}
                >保存回答策略</Button>
              </Space>
            </div>
          </Card>

          <Card
            className="management-card execution-profile-catalog-card"
            title="ExecutionProfile 方案"
            extra={isAdmin && <Button type="primary" onClick={() => { profileForm.resetFields(); profileForm.setFieldsValue({ approval_policy: 'none' }); setProfileModal({ mode: 'create' }) }}>新建 Profile</Button>}
          >
            <Alert
              className="execution-profile-version-note"
              type="info"
              showIcon
              message="Profile 决定引擎与运行策略；模型在上方单独选择。"
              description="发布版本保持不变。编辑已发布方案时，先在同一 Profile ID 下创建下一版草稿；发布后默认绑定仍指向原版本，需显式切换。"
            />
            {profileRows.length > 0 && profileVersionsQuery.isPending ? (
              <div className="management-loading"><Spin /><span>正在读取 Profile 版本</span></div>
            ) : profileVersionsQuery.isError ? (
              <Alert type="error" showIcon message="无法读取 Profile 版本" description={(profileVersionsQuery.error as Error).message} action={<Button onClick={() => void profileVersionsQuery.refetch()}>重试</Button>} />
            ) : allProfiles.length === 0 ? (
              <Empty className="management-empty" description="还没有 ExecutionProfile。创建一个只配置引擎和运行策略的 Profile，再分别设置模型路由默认。">
                {isAdmin && <Button type="primary" onClick={() => { profileForm.resetFields(); setProfileModal({ mode: 'create' }) }}>创建第一个 Profile</Button>}
              </Empty>
            ) : (
              <Table
                rowKey={(item) => profileKey(item)}
                size="small"
                pagination={{ pageSize: 8, showSizeChanger: false }}
                dataSource={[...allProfiles].sort((a, b) => a.id.localeCompare(b.id) || (b.version ?? 0) - (a.version ?? 0))}
                columns={profileColumns}
                scroll={{ x: 820 }}
              />
            )}
            {providerQuery.data?.items.some((item) => item.engine_name === 'claude_code') !== true && (
              <Text className="runtime-unavailable-provider-note" type="secondary">Claude Code 当前未注册适配器，因此不会作为可选引擎出现。</Text>
            )}
          </Card>
        </div>
      )}

      <Modal
        title={profileModal?.mode === 'create' ? '新建 ExecutionProfile' : '编辑 Profile 草稿'}
        open={Boolean(profileModal)}
        onCancel={() => setProfileModal(null)}
        destroyOnHidden
        footer={profileModal?.mode === 'create' ? (
          <Space>
            <Button onClick={() => setProfileModal(null)}>取消</Button>
            <Button type="primary" loading={createProfileMutation.isPending} onClick={() => void profileForm.submit()}>创建并发布</Button>
          </Space>
        ) : (
          <Space>
            <Button onClick={() => setProfileModal(null)}>关闭</Button>
            <Button loading={updateProfileMutation.isPending} onClick={() => void profileForm.submit()}>保存草稿</Button>
            <Button type="primary" loading={publishProfileMutation.isPending} onClick={() => {
              const profile = profileModal?.mode === 'edit' ? profileModal.profile : null
              if (profile) publishProfileMutation.mutate(profile)
            }}>发布此版本</Button>
          </Space>
        )}
      >
        {profileModal?.mode === 'edit' && <Alert className="profile-draft-banner" type="info" showIcon message={`${profileModal.profile.id} · v${profileModal.profile.version} 草稿`} description="发布前仍可修改；发布不会自动切换个人或租户默认。旧 Workspace/Sandbox/审批字段需要先清除。" />}
        <Form
          form={profileForm}
          layout="vertical"
          onFinish={(values) => {
            if (profileModal?.mode === 'create') createProfileMutation.mutate(values)
            else updateProfileMutation.mutate(values)
          }}
        >
          <Form.Item name="engine_name" label="EngineProvider" rules={[{ required: true, message: '请选择已注册的引擎' }]}>
            <Select options={providerOptions} placeholder="选择本环境已注册的引擎" />
          </Form.Item>
          <Alert type="info" showIcon message="本期 Profile 只配置引擎" description="Worker 接通前，Workspace、Sandbox 与附加审批策略不会开放为可选配置。Profile 不保存模型 ID、ModelRoute 或凭据；请在默认运行环境或 Deployment 中独立选择 ModelRoute。" />
          {(createProfileMutation.isError || updateProfileMutation.isError) && (
            <Alert className="profile-edit-error" type="error" showIcon message="保存失败" description={((createProfileMutation.error ?? updateProfileMutation.error) as Error).message} />
          )}
        </Form>
      </Modal>
    </div>
  )
}
