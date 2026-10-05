// ترويسة التوثيق لطلبات الـ API المحمية (نفس النمط المستخدم في بقية المكوّنات)
const authHeaders = () => {
  const token = localStorage.getItem('token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export default authHeaders
