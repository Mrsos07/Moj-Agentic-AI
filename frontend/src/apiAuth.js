// توثيق مركزي لكل طلبات الواجهة إلى /api: كان كل نداء axios (63 نداءً) يمرّر التوكن
// يدوياً أو ينساه، والخادم صار يرفض كل نقطة محمية بلا توكن. هنا مرة واحدة للجميع.
import axios from 'axios'

const isApi = (url = '') => url.startsWith('/api') || url.includes('/api/')

axios.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token && isApi(config.url) && !config.headers?.Authorization) {
    config.headers = { ...(config.headers || {}), Authorization: `Bearer ${token}` }
  }
  return config
})

// توكن منتهٍ/مُبطَل: أعِد المستخدم لتسجيل الدخول بدل أخطاء صامتة في كل مكوّن
axios.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error?.response?.status
    const url = error?.config?.url || ''
    if (status === 401 && isApi(url) && !url.includes('/api/auth/login')
        && localStorage.getItem('token') && window.location.pathname !== '/login') {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.assign('/login')
    }
    return Promise.reject(error)
  },
)
