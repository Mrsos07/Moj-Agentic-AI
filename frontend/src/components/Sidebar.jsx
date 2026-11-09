import { useState, useEffect } from 'react'
import { LayoutDashboard, Settings, User, LogOut, Trash2, MessageSquare, MessageSquarePlus, ChevronUp, History } from 'lucide-react'
import GlassIcon from './ui/GlassIcon'
import { useNavigate, useLocation } from 'react-router-dom'
import axios from 'axios'
import logoLight from '../assets/logos/logo-light.png'
import logoDark from '../assets/logos/logo-dark.png'

const Sidebar = ({ darkMode, setDarkMode, user, onLogout, onLoadConversation, onNewChat }) => {
  const navigate = useNavigate()
  // المعالجة الزجاجية خاصة بشاشة المحادثة فقط؛ بقية المسارات تحتفظ بالمظهر السابق
  const glass = useLocation().pathname === '/chat'
  const [showUserMenu, setShowUserMenu] = useState(false)
  const [conversations, setConversations] = useState([])
  const [loading, setLoading] = useState(false)

  const handleNewChatClick = () => {
    if (onNewChat) return onNewChat()

    const newSessionId = `session_${Date.now()}`
    localStorage.setItem('session_id', newSessionId)
    window.dispatchEvent(new CustomEvent('mwj:new_chat', { detail: { session_id: newSessionId } }))
    navigate('/chat')
  }

  const handleLogout = () => {
    if (window.confirm('هل أنت متأكد من تسجيل الخروج؟')) {
      onLogout()
      navigate('/login')
    }
  }

  useEffect(() => {
    if (user) {
      fetchConversations()
    }
  }, [user])

  useEffect(() => {
    const handler = () => {
      if (user) fetchConversations()
    }
    window.addEventListener('mwj:conversation_updated', handler)
    return () => window.removeEventListener('mwj:conversation_updated', handler)
  }, [user])

  const fetchConversations = async () => {
    setLoading(true)
    try {
      const token = localStorage.getItem('token')
      if (!token) {
        setConversations([])
        return
      }
      const response = await axios.get(
        '/api/conversations/',
        {
          headers: {
            'Authorization': `Bearer ${token}`
          },
          timeout: 8000
        }
      )
      
      setConversations(Array.isArray(response.data) ? response.data : [])
    } catch (error) {
      const status = error?.response?.status
      const detail = error?.response?.data?.detail
      console.error('Failed to fetch conversations:', {
        status,
        detail,
        message: error?.message,
        url: error?.config?.url
      })
      if (status === 401 || status === 403) {
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        navigate('/login')
      }
      setConversations([])
    } finally {
      setLoading(false)
    }
  }

  const handleLoadConversation = async (conversationId) => {
    if (onLoadConversation) {
      await onLoadConversation(conversationId)
      return
    }

    window.dispatchEvent(new CustomEvent('mwj:load_conversation', { detail: { conversationId } }))
    navigate('/chat')
  }

  const handleDeleteConversation = async (conversationId) => {
    if (!window.confirm('هل تريد حذف هذه المحادثة؟')) return
    try {
      const token = localStorage.getItem('token')
      await axios.delete(`/api/conversations/${conversationId}`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      })
      await fetchConversations()
      window.dispatchEvent(new CustomEvent('mwj:conversation_updated'))
    } catch (error) {
      console.error('Failed to delete conversation:', error)
      alert('فشل حذف المحادثة')
    }
  }
  return (
    <aside className={`hidden md:flex flex-col w-[260px] h-full shrink-0 transition-all duration-300 ${glass ? 'moj-glass border-0 border-e border-e-white/40 dark:border-e-white/10' : 'bg-sidebar-light dark:bg-sidebar-dark'}`}>
      <div className="flex flex-col h-full p-4 gap-4">
        {/* فاصل خفيف في الأعلى */}
        <div className="border-b border-border-light dark:border-border-dark pb-4">
          <div className="flex justify-center px-3 py-4">
            <button
              type="button"
              onClick={() => navigate('/')}
              className="rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
              aria-label="الذهاب إلى الصفحة الرئيسية"
            >
              <img 
                src={darkMode ? logoLight : logoDark} 
                alt="شعار موج" 
                className="h-14 w-auto object-contain"
                draggable={false}
              />
            </button>
          </div>
        </div>

        <button
          onClick={handleNewChatClick}
          className={`flex w-full items-center gap-3 h-11 px-4 rounded-xl text-white transition-colors duration-150 ${glass ? 'moj-primary moj-hover' : 'bg-primary hover:bg-navy-light shadow-md ring-1 ring-primary/10'}`}
        >
          <GlassIcon size="sm" className="text-white"><MessageSquarePlus size={16} /></GlassIcon>
          <span className="text-sm font-semibold text-white">محادثة جديدة</span>
        </button>

        {/* قسم المحادثات السابقة */}
        <div className="flex flex-col gap-2">
          <h3 className="flex items-center gap-1.5 text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark px-3 mt-2"><History size={14} aria-hidden /> المحادثات السابقة</h3>
          
          {loading ? (
            <div className="flex items-center justify-center py-4">
              <div className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
            </div>
          ) : conversations.length > 0 ? (
            <div className="flex flex-col gap-1">
              {conversations.map((conv) => (
                <div
                  key={conv.id}
                  className={`group flex items-center gap-2 h-11 px-2 rounded-xl transition-colors ${glass ? 'hover:bg-white/50 dark:hover:bg-white/5' : 'hover:bg-gray-50 dark:hover:bg-card-dark'}`}
                >
                  <button
                    onClick={() => handleLoadConversation(conv.id)}
                    className={`flex items-center gap-2.5 flex-1 min-w-0 text-start ${glass ? 'moj-hover rounded-md' : ''}`}
                  >
                    <MessageSquare className="text-text-secondary-light dark:text-text-secondary-dark group-hover:text-primary shrink-0" size={16} />
                    <div className="flex flex-col min-w-0 flex-1">
                      <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark truncate">
                        {conv.title || 'محادثة بدون عنوان'}
                      </span>
                      <span className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark">
                        {new Date(conv.updated_at).toLocaleDateString('ar-SA')}
                      </span>
                    </div>
                  </button>

                  <button
                    onClick={() => handleDeleteConversation(conv.id)}
                    className={`p-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 ${glass ? 'moj-hover' : ''}`}
                    title="حذف"
                    aria-label="حذف المحادثة"
                  >
                    <Trash2 className="text-text-secondary-light dark:text-text-secondary-dark" size={16} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark px-3 py-2">لا توجد محادثات سابقة</p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto pe-2"></div>

        <div className="mt-auto border-t border-border-light dark:border-border-dark pt-4 flex flex-col gap-1">
          <div className="relative">
            {showUserMenu && (
              <div className={`absolute bottom-full inset-x-0 mb-2 rounded-2xl overflow-hidden ${glass ? 'moj-glass-strong' : 'bg-card-light dark:bg-card-dark shadow-lg border border-border-light dark:border-border-dark'}`}>
                {user?.is_admin && (
                  <button
                    onClick={() => {
                      setShowUserMenu(false)
                      navigate('/admin')
                    }}
                    className={`w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors text-start border-b border-border-light dark:border-border-dark ${glass ? 'moj-hover' : ''}`}
                  >
                    <LayoutDashboard className="text-purple-600 dark:text-purple-400" size={18} />
                    <span className="text-sm font-medium text-purple-600 dark:text-purple-400">لوحة التحكم الإدارية</span>
                  </button>
                )}
                <button
                  onClick={() => {
                    setShowUserMenu(false)
                    navigate('/profile')
                  }}
                  className={`w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors text-start ${glass ? 'moj-hover' : ''}`}
                >
                  <Settings className="text-text-secondary-light dark:text-text-secondary-dark" size={18} />
                  <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">الإعدادات</span>
                </button>
                <button
                  onClick={() => {
                    setShowUserMenu(false)
                  }}
                  className={`w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors text-start ${glass ? 'moj-hover' : ''}`}
                >
                  <User className="text-text-secondary-light dark:text-text-secondary-dark" size={18} />
                  <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">كاتب المحتوى</span>
                </button>
                <button
                  onClick={() => {
                    setShowUserMenu(false)
                    handleLogout()
                  }}
                  className={`w-full flex items-center gap-3 px-4 py-3 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors text-start ${glass ? 'moj-hover' : ''}`}
                >
                  <LogOut className="text-text-secondary-light dark:text-text-secondary-dark group-hover:text-red-600 dark:group-hover:text-red-400" size={18} />
                  <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark group-hover:text-red-600 dark:group-hover:text-red-400">تسجيل الخروج</span>
                </button>
              </div>
            )}
            
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-card-dark transition-colors cursor-pointer ${glass ? 'moj-hover' : ''}`}
            >
              <div className="bg-primary rounded-full size-9 shrink-0 flex items-center justify-center text-white font-bold text-sm overflow-hidden">
                {user?.profile_picture ? (
                  <img src={user.profile_picture} alt="Profile" className="w-full h-full object-cover" />
                ) : (
                  user?.name?.charAt(0) || user?.email?.charAt(0) || 'U'
                )}
              </div>
              <div className="flex flex-col min-w-0 flex-1">
                <span className="text-sm font-semibold text-text-primary-light dark:text-text-primary-dark truncate">
                  {user?.name || user?.email || 'المستخدم'}
                </span>
              </div>
              <ChevronUp
                className={`text-text-secondary-light dark:text-text-secondary-dark transition-transform ${showUserMenu ? '' : 'rotate-180'}`}
                size={16}
              />
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}

export default Sidebar
