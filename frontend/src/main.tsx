import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import 'antd/dist/reset.css'
import './styles.css'

const queryClient = new QueryClient()

export function Main() {
  // QueryClient 统一管理平台 API 的服务器状态和缓存。
  return (
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('root element is missing')

import('react-dom/client').then(({ createRoot }) => {
  createRoot(root).render(<Main />)
})
