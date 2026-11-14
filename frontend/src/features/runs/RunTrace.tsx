import { useQuery } from '@tanstack/react-query'
import { Card, Empty, Space, Tag, Timeline, Typography } from 'antd'
import { useParams } from 'react-router-dom'

import { getRunTrace } from '../../api/client'

const { Paragraph, Text, Title } = Typography

const spanLabels: Record<string, string> = {
  session: 'Session',
  run: 'Run',
  skill: 'Skill',
  tool: 'Tool',
  runtime: 'Runtime',
  model: 'Model',
  result: 'Result',
}

export function RunTracePage() {
  const { runId } = useParams<{ runId: string }>()
  const trace = useQuery({
    queryKey: ['run-trace', runId],
    queryFn: () => getRunTrace(runId ?? ''),
    enabled: Boolean(runId),
  })

  if (trace.isLoading) {
    return <Text>正在加载 Run Trace…</Text>
  }
  if (trace.isError || !trace.data) {
    return <Text type="danger">Run Trace 加载失败，请检查运行 ID。</Text>
  }

  const { run, spans, usage } = trace.data
  return (
    <Space direction="vertical" size={20} style={{ display: 'flex', padding: 24 }}>
      <div>
        <Title level={2}>Run Trace</Title>
        <Text type="secondary">查看 Fake Runtime 执行快照和标准化事件。</Text>
      </div>
      <Card title="运行结果" extra={<Tag color={run.status === 'succeeded' ? 'green' : 'red'}>{run.status}</Tag>}>
        <Paragraph>{run.result || '没有可见结果。'}</Paragraph>
        <Space wrap>
          <Tag>员工版本 {run.snapshot.employee_version}</Tag>
          <Tag>{run.snapshot.skill_ids.length} Skill</Tag>
          <Tag>{run.snapshot.tool_ids.length} Tool</Tag>
          <Tag>{usage.total_tokens} tokens</Tag>
          <Tag>{usage.estimated ? '估算用量' : '精确用量'}</Tag>
        </Space>
      </Card>
      <Card title="执行时间线">
        {spans.length === 0 ? (
          <Empty description="暂无 Trace Span" />
        ) : (
          <Timeline
            items={spans.map((span) => ({
              key: span.id,
              children: (
                <Space>
                  <Text strong>{spanLabels[span.span_type] ?? span.span_type}</Text>
                  <Tag>{span.status}</Tag>
                  <Text type="secondary">#{span.sequence}</Text>
                </Space>
              ),
            }))}
          />
        )}
      </Card>
    </Space>
  )
}
