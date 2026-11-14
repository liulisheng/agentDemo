export interface DigitalEmployeeSummary {
  id: string
  tenant_id: string
  name: string
  role_name: string
  description: string
  status: string
  published_to_gallery: boolean
  resource_counts: {
    knowledge_base: number
    skill: number
    sop: number
  }
}

interface ListResponse<T> {
  items: T[]
}

export async function listDigitalEmployees(): Promise<DigitalEmployeeSummary[]> {
  // 开发阶段使用 Fake 用户；生产环境由后端认证上下文替代该请求头。
  const fakeUser = import.meta.env.VITE_FAKE_USER ?? 'admin-local'
  const response = await fetch('/api/v1/digital-employees', {
    headers: { 'X-Fake-User': fakeUser },
  })

  if (!response.ok) {
    throw new Error(`digital employees request failed: ${response.status}`)
  }

  const body = (await response.json()) as ListResponse<DigitalEmployeeSummary>
  return body.items
}
