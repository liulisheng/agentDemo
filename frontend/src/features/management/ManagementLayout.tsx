import { Layout, Typography } from 'antd'
import { Link, Outlet, useLocation } from 'react-router-dom'

import { useTenantContext } from './TenantContext'

const { Header, Sider, Content } = Layout
const { Text, Title } = Typography

const navigation = [
  { path: '/enterprise/employees', label: '数字员工' },
  { path: '/enterprise/resources', label: '资源中心' },
  { path: '/enterprise/deployments', label: '部署记录' },
]

export function ManagementLayout() {
  const location = useLocation()
  const { fakeUser, currentIdentity, setFakeUser } = useTenantContext()
  const selectedPath = navigation.find((item) => location.pathname.startsWith(item.path))?.path
    ?? '/enterprise/employees'

  return (
    <Layout className="management-shell">
      <Sider width={232} theme="light" className="management-sider">
        <div className="management-brand">
          <span className="brand-mark">A</span>
          <span>Agent Platform</span>
        </div>
        <div className="management-nav-label">管理控制台</div>
        <nav aria-label="管理导航" className="management-nav">
          {navigation.map((item) => (
            <Link
              key={item.path}
              className={selectedPath === item.path ? 'management-nav-link selected' : 'management-nav-link'}
              aria-current={selectedPath === item.path ? 'page' : undefined}
              to={item.path}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </Sider>
      <Layout>
        <Header className="management-header">
          <div>
            <Text type="secondary">当前租户</Text>
            <Title level={5} className="tenant-title">{currentIdentity.tenantName}</Title>
          </div>
          <div className="tenant-switcher">
            <label htmlFor="tenant-identity-select">登录身份</label>
            <select
              id="tenant-identity-select"
              aria-label="当前身份"
              value={fakeUser}
              onChange={(event) => setFakeUser(event.target.value)}
            >
              {[
                { value: 'admin-local', label: '租户 A · 管理员' },
                { value: 'member-local', label: '租户 A · 成员' },
                { value: 'admin-other', label: '租户 B · 管理员' },
              ].map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        </Header>
        <nav aria-label="管理导航（窄屏）" className="management-mobile-nav">
          {navigation.map((item) => (
            <Link
              key={item.path}
              className={selectedPath === item.path ? 'management-mobile-nav-link selected' : 'management-mobile-nav-link'}
              aria-current={selectedPath === item.path ? 'page' : undefined}
              to={item.path}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <Content className="management-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}

export function ManagementPlaceholder({ title }: { title: string }) {
  return (
    <section className="management-page">
      <Text className="eyebrow">AGENT PLATFORM / 管理</Text>
      <Title level={2}>{title}</Title>
    </section>
  )
}
