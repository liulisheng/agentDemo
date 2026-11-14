import { Card, Space, Typography } from 'antd'

const { Paragraph, Title } = Typography

export function AgentDashboard() {
  return (
    <Card>
      <Title level={2}>数字员工工作台</Title>
      <Paragraph>查看员工档案、工作记录、记忆、Skill、Tool 和运行入口。</Paragraph>
    </Card>
  )
}

export function SkillList() {
  return (
    <Card>
      <Title level={2}>Skill 列表</Title>
      <Paragraph>按数字员工版本查看已发布 Skill 和工具绑定。</Paragraph>
    </Card>
  )
}

export function ChatWorkspace() {
  return (
    <Space direction="vertical">
      <Title level={2}>对话工作区</Title>
      <Paragraph>创建 Session、发送消息并打开 Run Trace。</Paragraph>
    </Space>
  )
}
