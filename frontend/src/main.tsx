import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import { App } from './App'
import 'antd/dist/reset.css'
import './styles.css'

const queryClient = new QueryClient()

export function Main() {
  // QueryClient 统一管理平台 API 的服务器状态和缓存。
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider theme={{
        token: {
          colorPrimary: '#245C67',
          colorText: '#23323D',
          colorTextSecondary: '#687780',
          colorBorder: '#DFE7E8',
          colorBgLayout: '#F3F6F7',
          borderRadius: 8,
          fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif',
          controlHeight: 38,
        },
      }}>
        <App />
      </ConfigProvider>
    </QueryClientProvider>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('root element is missing')

import('react-dom/client').then(({ createRoot }) => {
  createRoot(root).render(<Main />)
})
