import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { FiUsers, FiUserCheck, FiShield, FiTrash2, FiArrowRight, FiMessageSquare, FiTwitter, FiEye, FiCpu, FiBookOpen } from 'react-icons/fi'
import { MdAdminPanelSettings } from 'react-icons/md'
import McpAdmin from '../components/admin/McpAdmin'
import SkillsAdmin from '../components/admin/SkillsAdmin'
import PermissionsAdmin from '../components/admin/PermissionsAdmin'

const ROLE_LABELS = { user: 'مستخدم', admin: 'مسؤول', developer: 'مطوّر' }
const ROLE_STYLES = {
  user: 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300',
  admin: 'bg-purple-100 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400',
  developer: 'bg-amber-100 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400',
}
const TABS = [
  { id: 'overview', label: 'نظرة عامة', icon: MdAdminPanelSettings },
  { id: 'users', label: 'المستخدمون والصلاحيات', icon: FiUsers },
  { id: 'mcps', label: 'خوادم MCP', icon: FiCpu },
  { id: 'skills', label: 'مهارات النظام', icon: FiBookOpen },
]

const AdminDashboard = ({ user, darkMode }) => {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState(null)
  const [system, setSystem] = useState(null)
  const [users, setUsers] = useState([])
  const [tab, setTab] = useState('overview')
  const [message, setMessage] = useState({ text: '', type: '' })
  const [selectedUser, setSelectedUser] = useState(null)
  const [userDetails, setUserDetails] = useState(null)
  const [detailsLoading, setDetailsLoading] = useState(false)

  useEffect(() => {
    if (!user?.is_admin) {
      navigate('/chat')
      return
    }
    fetchDashboardData()
  }, [user, navigate])

  const showMessage = useCallback((text, type) => {
    setMessage({ text, type })
    setTimeout(() => setMessage({ text: '', type: '' }), 6000)
  }, [])

  // الدور الحقيقي من الخادم (لا من localStorage): الواجهة تُخفي أزرار المطوّر فقط،
  // والفرض الفعلي في الخادم (403).
  const myPerms = system?.me?.permissions || []
  const myRole = system?.me?.role || (user?.is_admin ? 'admin' : 'user')
  const canManageSystem = myPerms.includes('system.mcp.manage')
  const canGrantDeveloper = myPerms.includes('admin.roles.grant_developer')

  const fetchDashboardData = async () => {
    setLoading(true)
    try {
      const token = localStorage.getItem('token')
      const headers = {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }

      const [statsRes, usersRes, systemRes] = await Promise.all([
        axios.get('/api/admin/stats', { headers }),
        axios.get('/api/admin/users', { headers }),
        axios.get('/api/admin/system/overview', { headers })
      ])

      setStats(statsRes.data)
      setUsers(usersRes.data)
      setSystem(systemRes.data)
    } catch (error) {
      showMessage('فشل تحميل البيانات', 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleToggleActive = async (userId, currentStatus) => {
    try {
      const token = localStorage.getItem('token')
      await axios.put(
        `/api/admin/users/${userId}`,
        { is_active: !currentStatus },
        {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          }
        }
      )
      showMessage('تم تحديث حالة المستخدم بنجاح', 'success')
      fetchDashboardData()
    } catch (error) {
      showMessage(error.response?.data?.detail || 'فشل تحديث المستخدم', 'error')
    }
  }

  const handleSetRole = async (userId, role) => {
    try {
      const token = localStorage.getItem('token')
      await axios.put(
        `/api/admin/users/${userId}`,
        { role },
        {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          }
        }
      )
      showMessage('تم تحديث صلاحيات المستخدم بنجاح', 'success')
      fetchDashboardData()
    } catch (error) {
      showMessage(error.response?.data?.detail || 'فشل تحديث الصلاحيات', 'error')
    }
  }

  const handleDeleteUser = async (userId, userEmail) => {
    if (!window.confirm(`هل أنت متأكد من حذف المستخدم ${userEmail}؟`)) {
      return
    }

    try {
      const token = localStorage.getItem('token')
      await axios.delete(
        `/api/admin/users/${userId}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      )
      showMessage('تم حذف المستخدم بنجاح', 'success')
      fetchDashboardData()
    } catch (error) {
      showMessage(error.response?.data?.detail || 'فشل حذف المستخدم', 'error')
    }
  }

  const fetchUserDetails = async (userId) => {
    setDetailsLoading(true)
    try {
      const token = localStorage.getItem('token')
      const response = await axios.get(
        `/api/admin/users/${userId}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      )
      setUserDetails(response.data)
      setSelectedUser(userId)
    } catch (error) {
      showMessage('فشل تحميل تفاصيل المستخدم', 'error')
    } finally {
      setDetailsLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-background-light dark:bg-background-dark flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-text-secondary-light dark:text-text-secondary-dark">جاري التحميل...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background-light dark:bg-background-dark">
      <div className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <MdAdminPanelSettings className="text-primary" size={40} />
            <div>
              <h1 className="text-3xl font-bold text-text-primary-light dark:text-text-primary-dark">
                لوحة التحكم الإدارية
              </h1>
              <p className="text-text-secondary-light dark:text-text-secondary-dark">
                إدارة المستخدمين والصلاحيات وقدرات النظام · دورك:{' '}
                <span className={`px-2 py-0.5 rounded-full text-xs ${ROLE_STYLES[myRole]}`}>{ROLE_LABELS[myRole]}</span>
              </p>
            </div>
          </div>
          <button
            onClick={() => navigate('/chat')}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-card-light dark:bg-card-dark hover:bg-gray-100 dark:hover:bg-gray-700 text-text-primary-light dark:text-text-primary-dark transition-colors"
          >
            <span>العودة للمحادثة</span>
            <FiArrowRight />
          </button>
        </div>

        {message.text && (
          <div className={`mb-6 p-4 rounded-lg ${
            message.type === 'success' 
              ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400' 
              : 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400'
          }`}>
            {message.text}
          </div>
        )}

        <div className="flex flex-wrap gap-2 mb-6 border-b border-border-light dark:border-border-dark">
          {TABS.map(t => (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t.id
                ? 'border-primary text-primary'
                : 'border-transparent text-text-secondary-light dark:text-text-secondary-dark hover:text-text-primary-light dark:hover:text-text-primary-dark'}`}>
              <t.icon size={16} /> {t.label}
            </button>
          ))}
        </div>

        {tab === 'mcps' && <McpAdmin canManage={canManageSystem} notify={showMessage} />}
        {tab === 'skills' && <SkillsAdmin canManage={myPerms.includes('system.skills.manage')} notify={showMessage} />}

        {tab === 'overview' && system && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            {[
              { label: 'الموظفون الأذكياء', value: system.employees, sub: 'في كل حسابات النظام' },
              { label: 'خوادم MCP (system/mcps)', value: system.mcps.builtin + system.mcps.system,
                sub: `${system.mcps.builtin} أصلية · ${system.mcps.system} مرفوعة — كلها مدمجة`, go: 'mcps' },
              { label: 'المهارات (system/skills)', value: system.skills.total,
                sub: `${system.skills.coordinator} للمنسّق · ${system.skills.employee} للموظفين`, go: 'skills' },
            ].map(c => (
              <button key={c.label} type="button" onClick={() => c.go && setTab(c.go)}
                className="text-right bg-card-light dark:bg-card-dark rounded-xl p-6 shadow-sm hover:ring-1 hover:ring-primary/40">
                <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">{c.label}</p>
                <p className="text-3xl font-bold text-text-primary-light dark:text-text-primary-dark">{c.value}</p>
                <p className="text-xs mt-1 text-text-secondary-light dark:text-text-secondary-dark">{c.sub}</p>
              </button>
            ))}
            <div className="md:col-span-3 bg-card-light dark:bg-card-dark rounded-xl p-6 shadow-sm">
              <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-3">المستخدمون حسب الدور</p>
              <div className="flex flex-wrap gap-3">
                {Object.entries(system.users.by_role).map(([r, n]) => (
                  <span key={r} className={`px-3 py-1.5 rounded-full text-sm ${ROLE_STYLES[r]}`}>{ROLE_LABELS[r]}: <b>{n}</b></span>
                ))}
              </div>
            </div>
          </div>
        )}

        {(tab === 'overview' || tab === 'users') && stats && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
            <div className="bg-card-light dark:bg-card-dark rounded-xl p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">
                    إجمالي المستخدمين
                  </p>
                  <p className="text-3xl font-bold text-text-primary-light dark:text-text-primary-dark">
                    {stats.total_users}
                  </p>
                </div>
                <div className="bg-primary/10 p-3 rounded-lg">
                  <FiUsers className="text-primary" size={24} />
                </div>
              </div>
            </div>

            <div className="bg-card-light dark:bg-card-dark rounded-xl p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">
                    المستخدمين النشطين
                  </p>
                  <p className="text-3xl font-bold text-green-600 dark:text-green-400">
                    {stats.active_users}
                  </p>
                </div>
                <div className="bg-green-100 dark:bg-green-900/20 p-3 rounded-lg">
                  <FiUserCheck className="text-green-600 dark:text-green-400" size={24} />
                </div>
              </div>
            </div>

            <div className="bg-card-light dark:bg-card-dark rounded-xl p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">
                    المسؤولين
                  </p>
                  <p className="text-3xl font-bold text-purple-600 dark:text-purple-400">
                    {stats.admin_users}
                  </p>
                </div>
                <div className="bg-purple-100 dark:bg-purple-900/20 p-3 rounded-lg">
                  <FiShield className="text-purple-600 dark:text-purple-400" size={24} />
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === 'users' && (
        <div className="space-y-6">
        <div className="bg-card-light dark:bg-card-dark rounded-xl shadow-sm overflow-hidden">
          <div className="p-6 border-b border-border-light dark:border-border-dark">
            <h2 className="text-xl font-semibold text-text-primary-light dark:text-text-primary-dark">
              إدارة المستخدمين
            </h2>
          </div>
          
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 dark:bg-gray-800">
                <tr>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    المستخدم
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    البريد الإلكتروني
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    الحالة
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    الصلاحيات
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    المحادثات
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    الحسابات
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    تاريخ الإنشاء
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark uppercase tracking-wider">
                    الإجراءات
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-light dark:divide-border-dark">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full bg-primary flex items-center justify-center text-white font-bold overflow-hidden">
                          {u.profile_picture ? (
                            <img src={u.profile_picture} alt={u.name} className="w-full h-full object-cover" />
                          ) : (
                            u.name?.charAt(0) || u.email?.charAt(0) || 'U'
                          )}
                        </div>
                        <span className="text-text-primary-light dark:text-text-primary-dark font-medium">
                          {u.name || 'بدون اسم'}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-text-secondary-light dark:text-text-secondary-dark">
                      {u.email}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <button
                        onClick={() => handleToggleActive(u.id, u.is_active)}
                        disabled={u.id === user.id}
                        className={`px-3 py-1 rounded-full text-xs font-medium ${
                          u.is_active
                            ? 'bg-green-100 dark:bg-green-900/20 text-green-600 dark:text-green-400'
                            : 'bg-red-100 dark:bg-red-900/20 text-red-600 dark:text-red-400'
                        } ${u.id === user.id ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:opacity-80'}`}
                      >
                        {u.is_active ? 'نشط' : 'معطل'}
                      </button>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {/* منح «المطوّر» أو سحبه للمطوّر وحده؛ ولا أحد يغيّر دوره بنفسه (يُفرض في الخادم أيضاً) */}
                      <select
                        value={u.role || (u.is_admin ? 'admin' : 'user')}
                        onChange={(e) => handleSetRole(u.id, e.target.value)}
                        disabled={u.id === user.id || (u.role === 'developer' && !canGrantDeveloper)}
                        className={`px-2 py-1 rounded-full text-xs font-medium border-0 ${ROLE_STYLES[u.role || 'user']} disabled:opacity-60`}
                      >
                        {['user', 'admin', 'developer'].map(r => (
                          <option key={r} value={r} disabled={r === 'developer' && !canGrantDeveloper}>{ROLE_LABELS[r]}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-text-secondary-light dark:text-text-secondary-dark">
                        <FiMessageSquare size={16} />
                        <span className="font-medium">{u.conversations_count || 0}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-text-secondary-light dark:text-text-secondary-dark">
                        <FiTwitter size={16} />
                        <span className="font-medium">{u.social_accounts_count || 0}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-text-secondary-light dark:text-text-secondary-dark text-sm">
                      {new Date(u.created_at).toLocaleDateString('ar-SA')}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => fetchUserDetails(u.id)}
                          className="text-primary hover:text-primary/80"
                          title="عرض التفاصيل"
                        >
                          <FiEye size={18} />
                        </button>
                        <button
                          onClick={() => handleDeleteUser(u.id, u.email)}
                          disabled={u.id === user.id}
                          className={`text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300 ${
                            u.id === user.id ? 'opacity-50 cursor-not-allowed' : ''
                          }`}
                          title="حذف المستخدم"
                        >
                          <FiTrash2 size={18} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <PermissionsAdmin notify={showMessage} />
        </div>
        )}

        {selectedUser && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setSelectedUser(null)}>
            <div className="bg-card-light dark:bg-card-dark rounded-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <div className="p-6 border-b border-border-light dark:border-border-dark flex items-center justify-between">
                <h2 className="text-xl font-semibold text-text-primary-light dark:text-text-primary-dark">
                  تفاصيل المستخدم
                </h2>
                <button
                  onClick={() => setSelectedUser(null)}
                  className="text-text-secondary-light dark:text-text-secondary-dark hover:text-text-primary-light dark:hover:text-text-primary-dark"
                >
                  ✕
                </button>
              </div>

              {detailsLoading ? (
                <div className="p-12 text-center">
                  <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
                  <p className="text-text-secondary-light dark:text-text-secondary-dark">جاري التحميل...</p>
                </div>
              ) : userDetails && (
                <div className="p-6 space-y-6">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">الاسم</p>
                      <p className="text-text-primary-light dark:text-text-primary-dark font-medium">{userDetails.name || 'بدون اسم'}</p>
                    </div>
                    <div>
                      <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">البريد الإلكتروني</p>
                      <p className="text-text-primary-light dark:text-text-primary-dark font-medium">{userDetails.email}</p>
                    </div>
                    <div>
                      <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">إجمالي الرسائل</p>
                      <p className="text-text-primary-light dark:text-text-primary-dark font-medium">{userDetails.total_messages}</p>
                    </div>
                    <div>
                      <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm mb-1">تاريخ التسجيل</p>
                      <p className="text-text-primary-light dark:text-text-primary-dark font-medium">{new Date(userDetails.created_at).toLocaleDateString('ar-SA')}</p>
                    </div>
                  </div>

                  <div>
                    <h3 className="text-lg font-semibold text-text-primary-light dark:text-text-primary-dark mb-3 flex items-center gap-2">
                      <FiTwitter /> الحسابات الاجتماعية ({userDetails.social_accounts.length})
                    </h3>
                    {userDetails.social_accounts.length > 0 ? (
                      <div className="space-y-2">
                        {userDetails.social_accounts.map((acc) => (
                          <div key={acc.id} className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 flex items-center justify-between">
                            <div>
                              <p className="text-text-primary-light dark:text-text-primary-dark font-medium">
                                {acc.username}
                              </p>
                              <p className="text-text-secondary-light dark:text-text-secondary-dark text-sm">
                                المنصة: {acc.platform === 'x' ? 'X (Twitter)' : acc.platform}
                              </p>
                            </div>
                            <div className="text-left">
                              <span className={`px-3 py-1 rounded-full text-xs font-medium ${
                                acc.status === 'active'
                                  ? 'bg-green-100 dark:bg-green-900/20 text-green-600 dark:text-green-400'
                                  : 'bg-red-100 dark:bg-red-900/20 text-red-600 dark:text-red-400'
                              }`}>
                                {acc.status === 'active' ? 'نشط' : acc.status}
                              </span>
                              {acc.last_used && (
                                <p className="text-text-secondary-light dark:text-text-secondary-dark text-xs mt-1">
                                  آخر استخدام: {new Date(acc.last_used).toLocaleDateString('ar-SA')}
                                </p>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-text-secondary-light dark:text-text-secondary-dark text-center py-4">لا توجد حسابات</p>
                    )}
                  </div>

                  <div>
                    <h3 className="text-lg font-semibold text-text-primary-light dark:text-text-primary-dark mb-3 flex items-center gap-2">
                      <FiMessageSquare /> المحادثات ({userDetails.conversations.length})
                    </h3>
                    {userDetails.conversations.length > 0 ? (
                      <div className="space-y-2 max-h-64 overflow-y-auto">
                        {userDetails.conversations.map((conv) => (
                          <div key={conv.id} className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
                            <div className="flex items-center justify-between mb-2">
                              <p className="text-text-primary-light dark:text-text-primary-dark font-medium">
                                {conv.title || `محادثة #${conv.id}`}
                              </p>
                              <span className="text-text-secondary-light dark:text-text-secondary-dark text-sm">
                                {conv.messages_count} رسالة
                              </span>
                            </div>
                            <p className="text-text-secondary-light dark:text-text-secondary-dark text-xs">
                              آخر تحديث: {new Date(conv.updated_at).toLocaleDateString('ar-SA')}
                            </p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-text-secondary-light dark:text-text-secondary-dark text-center py-4">لا توجد محادثات</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default AdminDashboard
