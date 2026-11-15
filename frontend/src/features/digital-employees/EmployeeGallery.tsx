import { Navigate } from 'react-router-dom'

/** 兼容旧 Gallery 地址，将旧开放广场入口重定向到资源中心。 */
export function EmployeeGallery() {
  return <Navigate to="/enterprise/resources" replace />
}
