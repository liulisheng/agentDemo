import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Modal, Space, Spin, Tag, Typography, message } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  getDeploymentRuntimeConfig,
  getEffectiveEngineProfileDefault,
  getEffectiveModelRouteDefault,
  listEngineProviders,
  listExecutionProfileVersions,
  listExecutionProfiles,
  listSelectableModelRoutes,
  saveDeploymentRuntimeConfig,
} from '../../api/client'
import type { ExecutionProfileSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Text } = Typography

function profileKey(profile: ExecutionProfileSummary) {
  return `${profile.id}#${profile.version}`
}

function decodeProfileKey(value?: string) {
  if (!value) return { id: null, version: null }
  const index = value.lastIndexOf('#')
  return index < 0
    ? { id: null, version: null }
    : { id: value.slice(0, index), version: Number(value.slice(index + 1)) }
}

export interface DeploymentRuntimeConfigurationDialogProps {
  open: boolean
  deploymentId?: string
  employeeName?: string
  canEdit: boolean
  onClose: () => void
  onSaved: () => void
}

export function DeploymentRuntimeConfigurationDialog({
  open,
  deploymentId,
  employeeName,
  canEdit,
  onClose,
  onSaved,
}: DeploymentRuntimeConfigurationDialogProps) {
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<'INHERIT' | 'OVERRIDE'>('INHERIT')
  const [profileValue, setProfileValue] = useState<string>()
  const [routeValue, setRouteValue] = useState<string>()
  const [formError, setFormError] = useState<string | null>(null)

  const configQuery = useQuery({
    queryKey: ['deployment-runtime-config', fakeUser, deploymentId],
    queryFn: () => getDeploymentRuntimeConfig(deploymentId!, fakeUser),
    enabled: open && Boolean(deploymentId),
  })
  const profilesQuery = useQuery({
    queryKey: ['execution-profiles', fakeUser],
    queryFn: () => listExecutionProfiles(fakeUser),
    enabled: open,
  })
  const profileRows = profilesQuery.data?.items ?? []
  const profileVersionsQuery = useQuery({
    queryKey: ['execution-profile-versions', fakeUser, profileRows.map((item) => item.id).join(',')],
    queryFn: async () => Promise.all(profileRows.map((item) => listExecutionProfileVersions(item.id, fakeUser))),
    enabled: open && profileRows.length > 0,
  })
  const routesQuery = useQuery({
    queryKey: ['selectable-model-routes', fakeUser],
    queryFn: () => listSelectableModelRoutes(fakeUser),
    enabled: open,
  })
  const providersQuery = useQuery({
    queryKey: ['engine-providers', fakeUser],
    queryFn: () => listEngineProviders(fakeUser),
    enabled: open,
  })
  const effectiveEngineQuery = useQuery({
    queryKey: ['runtime-default-engine-effective', fakeUser],
    queryFn: () => getEffectiveEngineProfileDefault(fakeUser),
    enabled: open,
  })
  const effectiveRouteQuery = useQuery({
    queryKey: ['runtime-default-route-effective', fakeUser],
    queryFn: () => getEffectiveModelRouteDefault(fakeUser),
    enabled: open,
  })

  const profiles = useMemo(
    () => profileVersionsQuery.data?.flatMap((page) => page.items) ?? [],
    [profileVersionsQuery.data],
  )
  const profileByKey = useMemo(() => new Map(profiles.map((profile) => [profileKey(profile), profile])), [profiles])
  const profileOptions = profiles
    .filter((profile) => profile.status === 'PUBLISHED' && profile.engine_available)
    .map((profile) => ({
      value: profileKey(profile),
      label: `${profile.engine_name} · ${profile.id} · v${profile.version}`,
    }))
  const routes = routesQuery.data?.items ?? []
  const routeOptions = routes
    .filter((route) => route.status === 'ACTIVE')
    .map((route) => ({ value: route.route_id, label: `${route.alias} · ${route.profile.model_name}` }))

  useEffect(() => {
    const revision = configQuery.data?.revision
    if (!revision) {
      setMode('INHERIT')
      setProfileValue(undefined)
      setRouteValue(undefined)
      return
    }
    setMode(revision.mode)
    setProfileValue(
      revision.execution_profile_id && revision.execution_profile_version
        ? `${revision.execution_profile_id}#${revision.execution_profile_version}`
        : undefined,
    )
    setRouteValue(revision.model_route_id ?? undefined)
  }, [configQuery.data])

  useEffect(() => {
    setFormError(null)
  }, [open, deploymentId])

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!deploymentId || !configQuery.data) throw new Error('运行配置尚未读取完成')
      if (mode === 'OVERRIDE' && !profileValue && !routeValue) {
        throw new Error('请选择一个员工覆盖项；未选择的维度会继承默认。')
      }
      const useProfileOverride = mode === 'OVERRIDE' && Boolean(profileValue)
      const useModelOverride = mode === 'OVERRIDE' && Boolean(routeValue)
      const profile = useProfileOverride ? decodeProfileKey(profileValue) : { id: null, version: null }
      if (useProfileOverride && (!profile.id || !profile.version)) throw new Error('请选择精确的 ExecutionProfile 版本。')
      if (useModelOverride && !routeValue) throw new Error('请选择 ModelRoute。')
      return saveDeploymentRuntimeConfig(deploymentId, {
        expected_config_version: configQuery.data.runtime_config_version,
        mode,
        execution_profile_id: profile.id,
        execution_profile_version: profile.version,
        model_route_id: useModelOverride ? routeValue : null,
      }, fakeUser)
    },
    onSuccess: async () => {
      setFormError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['deployment-runtime-config', fakeUser, deploymentId] }),
        queryClient.invalidateQueries({ queryKey: ['workbench-employees', fakeUser] }),
        queryClient.invalidateQueries({ queryKey: ['deployments', fakeUser] }),
      ])
      message.success('员工运行配置已保存；新 Session 会使用该修订。')
      onSaved()
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : '运行配置保存失败'),
  })

  const loading = [
    configQuery.isPending,
    profilesQuery.isPending,
    routesQuery.isPending,
    providersQuery.isPending,
    effectiveEngineQuery.isPending,
    effectiveRouteQuery.isPending,
    profileRows.length > 0 && profileVersionsQuery.isPending,
  ].some(Boolean)
  const queryError = [
    configQuery.error,
    profilesQuery.error,
    profileVersionsQuery.error,
    routesQuery.error,
    providersQuery.error,
    effectiveEngineQuery.error,
    effectiveRouteQuery.error,
  ].find(Boolean)

  const selectedProfile = profileValue ? profileByKey.get(profileValue) : undefined
  const selectedRoute = routes.find((route) => route.route_id === routeValue)
  const effectiveProfile = effectiveEngineQuery.data?.profile
  const effectiveRoute = effectiveRouteQuery.data?.route
  const resolvedProfile = selectedProfile ?? profiles.find((profile) => profile.id === effectiveProfile?.profile_id && profile.version === effectiveProfile.version)
  const resolvedRoute = selectedRoute ?? routes.find((route) => route.route_id === effectiveRoute?.route_id)
  const revision = configQuery.data?.revision
  const legacyApprovalName = revision?.approval_policy.name
  const selectedProfileUnsupported = selectedProfile?.runtime_policy_supported === false
  const hasUnsupportedLegacyOverrides = Boolean(
    revision && (
      revision.workspace_profile_id || revision.sandbox_profile_id
      || (Array.isArray(revision.approval_policy.require_approval_for)
        && revision.approval_policy.require_approval_for.length > 0)
      || (typeof legacyApprovalName === 'string' && legacyApprovalName !== 'none')
    ),
  )
  const provider = providersQuery.data?.items.find((item) => item.engine_name === resolvedProfile?.engine_name)
  const incompatible = Boolean(
    resolvedProfile && resolvedRoute && provider
    && !provider.supported_model_protocols.includes(resolvedRoute.profile.protocol ?? ''),
  )
  const noEffectiveProfile = !resolvedProfile
  const unavailableClaude = providersQuery.data?.items.some((item) => item.engine_name === 'claude_code') !== true

  return (
    <Modal
      className="deployment-runtime-config-modal"
      title={<span>运行配置{employeeName ? ` · ${employeeName}` : ''}</span>}
      open={open}
      onCancel={onClose}
      width={470}
      destroyOnHidden
      footer={(
        <Space>
          <Button onClick={onClose}>关闭</Button>
          {canEdit && <Button type="primary" loading={saveMutation.isPending} disabled={loading || Boolean(queryError)} onClick={() => saveMutation.mutate()}>保存配置</Button>}
        </Space>
      )}
    >
      {loading ? (
        <div className="management-loading"><Spin /><span>正在读取员工运行配置</span></div>
      ) : queryError ? (
        <Alert type="error" showIcon message="无法读取运行配置" description={(queryError as Error).message} action={<Button onClick={() => {
          void Promise.all([
            configQuery.refetch(), profilesQuery.refetch(), profileVersionsQuery.refetch(),
            routesQuery.refetch(), providersQuery.refetch(), effectiveEngineQuery.refetch(),
            effectiveRouteQuery.refetch(),
          ])
        }}>重试</Button>} />
      ) : (
        <div className="deployment-runtime-config-content">
          {!canEdit && <Alert type="info" showIcon message="只读运行配置" description="当前身份可以查看配置来源；修改请联系租户管理员。" />}
          <div className="deployment-runtime-dialog-context">
            <Tag>{currentIdentity.tenantName}</Tag>
            <Tag color={configQuery.data?.deployment_status === 'ACTIVE' ? 'success' : 'default'}>
              Deployment · {configQuery.data?.deployment_status === 'PENDING_CONFIGURATION' ? '待配置' : configQuery.data?.deployment_status === 'DISABLED' ? '已停用' : '活动'}
            </Tag>
          </div>
          {configQuery.data?.runtime_config_version !== null && configQuery.data?.runtime_config_version !== undefined && (
            <div className="deployment-runtime-current-revision"><Tag color="blue">配置修订 v{configQuery.data.runtime_config_version}</Tag><Text type="secondary">保存只影响新建的 Session；当前对话中的 Session 继续使用原快照。</Text></div>
          )}
          {hasUnsupportedLegacyOverrides && (
            <Alert type="warning" showIcon message="当前修订包含未接入 Worker 的环境扩展字段" description="Workspace、Sandbox 或附加审批策略不会被本阶段 Runtime 消费；保存本表单会在下一修订中清除这些字段。" />
          )}
          {selectedProfileUnsupported && (
            <Alert type="warning" showIcon message="当前 Profile 含未接入 Worker 的环境策略" description="请改选一个只配置引擎的 Profile，或先在“模型管理 · 默认运行环境”创建同 ID 新版本并清除历史字段。" />
          )}

          <fieldset disabled={!canEdit} className="deployment-runtime-fieldset">
            <legend>此员工 Deployment 的配置方式</legend>
            <Text type="secondary">ExecutionProfile 与 ModelRoute 分开选择；更换引擎不会替换模型。</Text>
            <div className="deployment-runtime-mode-switch" role="group" aria-label="运行配置模式">
              <button
                type="button"
                aria-pressed={mode === 'INHERIT'}
                onClick={() => {
                  setMode('INHERIT')
                  setProfileValue(undefined)
                  setRouteValue(undefined)
                }}
              >全部继承默认</button>
              <button
                type="button"
                aria-pressed={mode === 'OVERRIDE'}
                onClick={() => setMode('OVERRIDE')}
              >设置员工覆盖</button>
            </div>
            {mode === 'OVERRIDE' && (
              <div className="deployment-runtime-override-fields">
                <div className="deployment-runtime-field">
                  <label htmlFor="employee-runtime-profile">执行引擎（ExecutionProfile）</label>
                  <select
                    id="employee-runtime-profile"
                    className="deployment-runtime-select"
                    aria-label="执行引擎（ExecutionProfile）"
                    value={profileValue ?? ''}
                    onChange={(event) => setProfileValue(event.target.value || undefined)}
                  >
                    <option value="">继承默认引擎</option>
                    {profileOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                <div className="deployment-runtime-field">
                  <label htmlFor="employee-runtime-route">模型（ModelRoute）</label>
                  <select
                    id="employee-runtime-route"
                    className="deployment-runtime-select"
                    aria-label="模型（ModelRoute）"
                    value={routeValue ?? ''}
                    onChange={(event) => setRouteValue(event.target.value || undefined)}
                  >
                    <option value="">继承默认模型</option>
                    {routeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </div>
                {noEffectiveProfile && !selectedProfile && (
                  <Alert type="warning" showIcon message="当前没有可继承的 EngineProfile 默认" description={<span>仍需先配置默认引擎或在上方选择员工专属 Profile。可前往 <Link to="/enterprise/models?tab=runtime-defaults">模型管理 · 默认运行环境</Link> 设置。</span>} />
                )}
              </div>
            )}
          </fieldset>

          {mode === 'INHERIT' && (
            <div className="deployment-runtime-inherit-preview">
              <strong>{effectiveProfile ? `${effectiveProfile.engine_name} · Profile v${effectiveProfile.version}` : '尚未设置默认引擎'}</strong>
              <span>引擎来源：{effectiveEngineQuery.data?.source ?? '无来源'}</span>
              <p>模型来源：{effectiveRoute ? `${effectiveRoute.alias} · ${effectiveRoute.model_name}` : '尚未设置'} · {effectiveRouteQuery.data?.source ?? '无来源'}</p>
              <span>优先级：Deployment 字段 → 个人默认 → 租户默认 → 平台/系统</span>
              {noEffectiveProfile && (
                <Alert type="warning" showIcon message="当前没有可继承的 EngineProfile 默认" description={<span>保存继承后仍不能创建 Session。请先去 <Link to="/enterprise/models?tab=runtime-defaults">模型管理 · 默认运行环境</Link> 配置引擎默认。</span>} />
              )}
            </div>
          )}
          {incompatible && <Alert type="error" showIcon message="当前引擎和模型协议不兼容" description="保存不会替换模型；此组合创建 Session 时会被拒绝，请独立更换其中一项。" />}

          {unavailableClaude && <Text className="deployment-runtime-provider-note" type="secondary">Claude Code 当前未注册适配器，因此不会出现在可选引擎中。</Text>}
          {formError && <Alert type="error" showIcon message="运行配置未保存" description={formError} />}
          {saveMutation.isError && !formError && <Alert type="error" showIcon message="运行配置保存失败" description={(saveMutation.error as Error).message} />}
          <div className="deployment-runtime-footer-note"><Text type="secondary">Run 会固定自己的 Engine、ModelRoute 与两个来源。已建立的对话不随本次修改切换运行环境。</Text></div>
        </div>
      )}
    </Modal>
  )
}
