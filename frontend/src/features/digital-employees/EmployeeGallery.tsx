import { useQuery } from '@tanstack/react-query'
import { Avatar, Card, Layout, Menu, Space, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'

import { listDigitalEmployees } from '../../api/client'

const { Header, Sider, Content } = Layout
const { Title, Text, Paragraph } = Typography

const menuItems: MenuProps['items'] = [
  { key: 'gallery', label: '开放广场平台' },
  { key: 'employees', label: '我的数字员工' },
  { key: 'teams', label: '我的团队' },
  { key: 'channels', label: '渠道接入' },
  { key: 'models', label: '模型配置' },
  { key: 'settings', label: '运行设置' },
]

export function EmployeeGallery() {
  // 页面只负责展示平台 API 返回的摘要，发布和租户规则由后端控制面处理。
  const employees = useQuery({
    queryKey: ['digital-employees'],
    queryFn: listDigitalEmployees,
  })

  return (
    <Layout className="app-shell">
      <Sider width={220} theme="light" className="app-sider">
        <div className="brand">Agent Platform</div>
        <Menu mode="inline" selectedKeys={['gallery']} items={menuItems} />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Title level={3}>开放广场平台</Title>
          <Text type="secondary">数字员工和可复用能力</Text>
        </Header>
        <Content className="app-content">
          <Space direction="vertical" size={24} className="content-stack">
            <div>
              <Title level={2}>数字员工广场</Title>
              <Paragraph type="secondary">
                选择已发布的数字员工，查看其能力并开始对话。
              </Paragraph>
            </div>
            {employees.isLoading && <Text>正在加载数字员工…</Text>}
            {employees.isError && (
              <Text type="danger">数字员工加载失败，请稍后重试。</Text>
            )}
            <div className="employee-grid">
              {employees.data?.map((employee) => (
                <Card
                  key={employee.id}
                  className="employee-card"
                  title={
                    <Space>
                      <Avatar>{employee.name.slice(0, 1)}</Avatar>
                      <span>{employee.name}</span>
                    </Space>
                  }
                  extra={<Tag color="green">{employee.status === 'ACTIVE' ? '在线' : employee.status}</Tag>}
                >
                  <Text type="secondary">{employee.role_name}</Text>
                  <Paragraph ellipsis={{ rows: 2 }}>{employee.description}</Paragraph>
                  <Space wrap>
                    <Tag>{employee.resource_counts.knowledge_base} 资料</Tag>
                    <Tag>{employee.resource_counts.skill} Skill</Tag>
                    <Tag>{employee.resource_counts.sop} SOP</Tag>
                  </Space>
                </Card>
              ))}
            </div>
          </Space>
        </Content>
      </Layout>
    </Layout>
  )
}
