import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { expect, test } from 'vitest'

import { EmployeeGallery } from './EmployeeGallery'

test('legacy gallery URL redirects to Resource Center', async () => {
  render(
    <MemoryRouter initialEntries={['/workspace/gallery']}>
      <Routes>
        <Route path="/workspace/gallery" element={<EmployeeGallery />} />
        <Route path="/enterprise/resources" element={<h1>资源中心</h1>} />
      </Routes>
    </MemoryRouter>,
  )

  expect(await screen.findByRole('heading', { name: '资源中心' })).toBeInTheDocument()
  expect(screen.queryByText('开放广场平台')).not.toBeInTheDocument()
})
