import React from 'react'
import { render, screen } from '@testing-library/react'
import { test, expect } from 'vitest'

import { AgentDashboard, ChatWorkspace, SkillList } from './PlatformPages'

test('renders project-owned dashboard, skill and chat contracts', () => {
  render(<><AgentDashboard /><SkillList /><ChatWorkspace /></>)
  expect(screen.getByText('数字员工工作台')).toBeInTheDocument()
  expect(screen.getByText('Skill 列表')).toBeInTheDocument()
  expect(screen.getByText('对话工作区')).toBeInTheDocument()
})
