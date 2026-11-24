import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'

import { RuntimeStrategyPanel } from './RuntimeStrategyPanel'

test('renders the prototype-aligned strategy fields and updates the draft', async () => {
  const user = userEvent.setup()
  const onChange = vi.fn()
  render(
    <RuntimeStrategyPanel
      canEdit
      draft={{ reasoning_mode: 'AUTO', reasoning_effort: 'AUTO', response_verbosity: 'STANDARD' }}
      effective={{ reasoning_mode: 'AUTO', reasoning_effort: 'AUTO', response_verbosity: 'STANDARD' }}
      source="TENANT_DEFAULT"
      engineLabel="DeepSeek Harness · v1"
      routeLabel="本地模型 · gpt-6.2-sol"
      capabilities={{ reasoning: true, reasoning_effort: true, reasoning_budget: true, max_output_tokens: true }}
      onChange={onChange}
    />,
  )

  expect(screen.getByText('当前生效策略')).toBeInTheDocument()
  expect(screen.getByLabelText('思考模式')).toBeInTheDocument()
  expect(screen.getByLabelText('推理强度')).toBeInTheDocument()
  expect(screen.getByText('DeepSeek Harness · v1')).toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText('思考模式'), 'DEEP')
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ reasoning_mode: 'DEEP' }))
})

test('shows capability and compatibility warnings', () => {
  render(
    <RuntimeStrategyPanel
      canEdit={false}
      draft={{ reasoning_mode: 'DEEP' }}
      effective={{ reasoning_mode: 'AUTO' }}
      capabilities={{ reasoning: false, reasoning_effort: false, reasoning_budget: false, max_output_tokens: true }}
      reasoningUnsupported
      incompatible
      onChange={vi.fn()}
    />,
  )

  expect(screen.getByText('当前组合不支持深度思考')).toBeInTheDocument()
  expect(screen.getByText('当前引擎和模型协议不兼容')).toBeInTheDocument()
  expect(screen.getAllByText('不支持').length).toBeGreaterThan(0)
})
