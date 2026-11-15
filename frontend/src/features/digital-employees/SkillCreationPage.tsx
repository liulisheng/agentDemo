import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Input, Select, Space, Typography, message } from 'antd'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { createSkill, listTools } from '../../api/client'
import type { SkillSummary } from '../../api/client'
import { useTenantContext } from '../management/TenantContext'

const { Paragraph, Text, Title } = Typography

interface SkillCreateValues {
  name: string
  skill_type: string
  instruction: string
  allowed_tool_ids: string[]
}

/** 创建 Skill 专属 DRAFT；创建后进入 Skill 文件工作区维护和发布。 */
export function SkillCreationPage() {
  const { employeeId = '' } = useParams()
  const { fakeUser, currentIdentity } = useTenantContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [form] = Form.useForm<SkillCreateValues>()
  const canEdit = currentIdentity.roleName === '管理员'
  const tools = useQuery({
    queryKey: ['tools', fakeUser],
    queryFn: () => listTools(fakeUser),
  })

  const createMutation = useMutation({
    mutationFn: (values: SkillCreateValues) => createSkill({
      name: values.name,
      skill_type: values.skill_type,
      instruction: values.instruction,
      allowed_tool_ids: values.allowed_tool_ids ?? [],
    }, fakeUser),
    onSuccess: async (created: SkillSummary) => {
      await queryClient.invalidateQueries({ queryKey: ['skills', fakeUser] })
      message.success(`Skill「${created.name}」DRAFT 已创建`)
      navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}/skills/${encodeURIComponent(created.id)}/versions/${created.version}/files`)
    },
  })

  if (!canEdit) {
    return (
      <Alert
        type="error"
        showIcon
        message="当前身份不能创建 Skill"
        action={<Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}`}>返回员工详情</Link>}
      />
    )
  }

  return (
    <section className="management-page skill-creation-page" aria-label="新建 Skill">
      <div className="management-page-header">
        <div>
          <Link to={`/enterprise/employees/${encodeURIComponent(employeeId)}`}>← 返回员工详情</Link>
          <Text className="management-eyebrow">数字员工 / Skill 资源</Text>
          <Title level={2}>新建 Skill</Title>
          <Paragraph type="secondary">
            先创建 Skill 草稿，再在独立文件工作区编辑文件并发布。发布后回到员工 Skills 页绑定精确版本。
          </Paragraph>
        </div>
      </div>

      <Card className="management-card skill-creation-card" title="Skill 基本信息">
        <Form
          form={form}
          layout="vertical"
          initialValues={{ skill_type: 'instruction', allowed_tool_ids: [] }}
          onFinish={(values) => createMutation.mutate(values)}
        >
          <Form.Item name="name" label="Skill 名称" rules={[{ required: true, message: '请输入 Skill 名称' }]}>
            <Input maxLength={128} placeholder="例如：工单分类规范" />
          </Form.Item>
          <Form.Item name="skill_type" label="Skill 类型" rules={[{ required: true, message: '请选择 Skill 类型' }]}>
            <Select options={[
              { value: 'instruction', label: '指令型 Skill' },
              { value: 'tool-bundle', label: '工具组合 Skill' },
              { value: 'workflow', label: '流程型 Skill' },
            ]} />
          </Form.Item>
          <Form.Item name="instruction" label="初始指令" rules={[{ required: true, message: '请输入初始指令' }]}>
            <Input.TextArea rows={6} maxLength={65535} placeholder="填写 Skill 的初始内容；文件工作区可继续拆分为 SKILL.md 和辅助文件。" />
          </Form.Item>
          <Form.Item name="allowed_tool_ids" label="可选 Tool 依赖">
            <Select
              mode="multiple"
              placeholder={tools.isLoading ? '正在读取已发布 Tool…' : '为 Skill 选择已发布 Tool'}
              loading={tools.isLoading}
              options={(tools.data?.items ?? [])
                .filter((tool) => tool.status === 'PUBLISHED')
                .map((tool) => ({ value: tool.id, label: `${tool.name} · v${tool.version}` }))}
            />
          </Form.Item>
          {tools.isError && <Alert type="warning" showIcon message="Tool 依赖列表暂不可用" description={(tools.error as Error).message} />}
          {createMutation.isError && <Alert className="skill-creation-error" type="error" showIcon message={(createMutation.error as Error).message} />}
          <div className="skill-creation-actions">
            <Button onClick={() => navigate(`/enterprise/employees/${encodeURIComponent(employeeId)}`)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={createMutation.isPending}>
              创建草稿并编辑文件
            </Button>
          </div>
        </Form>
      </Card>
    </section>
  )
}
