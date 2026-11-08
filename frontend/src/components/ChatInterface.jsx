import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { FiMenu, FiSun, FiMoon } from 'react-icons/fi'
import { MdManageAccounts, MdPushPin, MdClose, MdSmartToy } from 'react-icons/md'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import AccountsDrawer from './AccountsDrawer'
import AgentsDrawer from './AgentsDrawer'
import TeamBar from './TeamBar'
import ChatModelPicker from './ChatModelPicker'
import authHeaders from '../authHeaders'
import usePinnedAccount from '../hooks/usePinnedAccount'
import logoLight from '../assets/logos/logo-light.png'
import logoDark from '../assets/logos/logo-dark.png'

// نتائج أوامر المنسّق تصل في محادثة الموظف (emp_<handle>)؛ نعرضها أيضاً في
// شات المنسّق خلال هذه المدة بعد الإسناد، وإلا بدا الأمر وكأنه لم يُنفّذ.
const AWAIT_RESULTS_MS = 30 * 60 * 1000

const coordinatorWelcome = () => ({
  id: Date.now(),
  type: 'assistant',
  content:
    '👋 أهلاً! أنا **موج — المنسّق العام** لفريقك من الموظفين الأذكياء.\n\n' +
    'أرى كل موظفيك وأوجّههم نيابةً عنك. جرّب مثلاً:\n\n' +
    '• «وش حالة فريقي اليوم؟»\n' +
    '• «خل كل الموظفين ينشرون عن اليوم الوطني كل واحد حسب تخصصه»\n' +
    '• «@اليوزر روح للتايم لاين وردّ على تغريدة تناسب تخصصك»\n' +
    '• «خل عماد يشتغل كل 45 دقيقة وركّز اهتماماته على الاقتصاد»\n\n' +
    'اذكر موظفاً بـ **@** لتخاطبه مباشرة، أو افتح محادثته الخاصة من شريط الفريق.',
  timestamp: new Date().toISOString()
})

const ChatInterface = ({ darkMode, setDarkMode, user }) => {
  const logo = darkMode ? logoLight : logoDark
  const [messages, setMessages] = useState([coordinatorWelcome()])
  const [team, setTeam] = useState([])
  const awaitingRef = useRef({})
  const [inputValue, setInputValue] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [ws, setWs] = useState(null)
  // مرجع السوكت الفعلي + مؤقّت إعادة الاتصال — يضمنان اتصالاً واحداً فقط
  // (StrictMode يركّب المكوّن مرتين في التطوير فكانت الرسائل تصل مكررة)
  const wsRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  // نبضة + مراقب: المتصفح لا يرى ping/pong الخاص بالبروتوكول، فإن مات الخادم
  // فجأة (إعادة تشغيل/انهيار) يبقى readyState=OPEN ولا تصل رسائل أبداً —
  // «اتصال ميت صامت». نرسل نبضة دورية ونعيد الاتصال إن انقطع الوارد.
  const heartbeatRef = useRef(null)
  const lastInboundRef = useRef(Date.now())
  const retryRef = useRef(0)
  const HEARTBEAT_MS = 25000
  const SILENCE_LIMIT_MS = 70000
  const [isConnected, setIsConnected] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(true)
  const [accountsOpen, setAccountsOpen] = useState(false)
  const [agentsOpen, setAgentsOpen] = useState(false)
  // وضع "الدردشة مع الموظف الذكي": اسم الحساب المرتبط أو null
  const [employeeChat, setEmployeeChat] = useState(null)
  const { pinned: pinnedAccount, setPinned: setPinnedAccount } = usePinnedAccount()
  const messagesEndRef = useRef(null)
  const inputFocusRef = useRef(null)

  const notifyChat = (content) => {
    setMessages(prev => [...prev, {
      id: Date.now(),
      type: 'assistant',
      content,
      timestamp: new Date().toISOString()
    }])
  }

  const handlePinAccount = (label) => {
    setPinnedAccount(label)
    setMessages(prev => [...prev, {
      id: Date.now(),
      type: 'assistant',
      content: label
        ? `📌 تم تثبيت الحساب @${label}. الطلبات التي لا تحدّد موظفاً ستُوجَّه إليه، ويبقى المنسّق يرى الفريق كله.`
        : '📍 تم إلغاء تثبيت الحساب — المنسّق العام يوجّه الفريق كله.',
      timestamp: new Date().toISOString()
    }])
  }

  // فريق المستخدم كما يراه المنسّق (يُحدَّث دورياً لتظهر الحالة الحيّة)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await axios.get('/api/aeos/team', { headers: authHeaders() })
        if (alive) setTeam(res.data?.employees || [])
      } catch { /* الخادم يُعاد تشغيله — نحاول لاحقاً */ }
    }
    load()
    const timer = setInterval(load, 60000)
    return () => { alive = false; clearInterval(timer) }
  }, [user?.id, agentsOpen])

  const insertMention = (handle) => {
    setInputValue(prev => {
      const tag = `@${handle} `
      return prev.includes(tag) ? prev : (prev && !prev.endsWith(' ') ? `${prev} ${tag}` : `${prev}${tag}`)
    })
    if (typeof inputFocusRef.current === 'function') inputFocusRef.current()
  }

  const openEmployeeChat = (handle) => {
    window.dispatchEvent(new CustomEvent('mwj:open_employee_chat', { detail: { label: handle } }))
  }

  const refreshSidebarConversations = () => {
    window.dispatchEvent(new CustomEvent('mwj:conversation_updated'))
  }

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, isTyping])

  useEffect(() => {
    connectWebSocket()

    // لا تنتظر المؤقّت في الحالتين المعروفتين لعودة الاتصال: رجوع الشبكة،
    // ورجوع المستخدم إلى التبويب (المتصفح يخفض المؤقّتات في التبويب المخفي
    // فيبقى الاتصال مقطوعاً دقائق بعد عودة الظهور).
    const revive = () => {
      if (document.visibilityState === 'hidden') return
      const sock = wsRef.current
      if (!sock || sock.readyState === WebSocket.CLOSED) {
        retryRef.current = 0
        connectWebSocket()
      }
    }
    window.addEventListener('online', revive)
    document.addEventListener('visibilitychange', revive)

    return () => {
      // ألغِ أي إعادة اتصال مجدولة، ثم أغلق السوكت الحالي إغلاقاً مقصوداً
      // (تصفير wsRef قبل close يمنع onclose من إعادة الاتصال)
      window.removeEventListener('online', revive)
      document.removeEventListener('visibilitychange', revive)
      stopHeartbeat()
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      const sock = wsRef.current
      wsRef.current = null
      if (sock) sock.close()
    }
  }, [])

  useEffect(() => {
    const handleNewChat = (e) => {
      const newSessionId = e?.detail?.session_id || `session_${Date.now()}`
      localStorage.setItem('session_id', newSessionId)
      setEmployeeChat(null)
      setMessages([coordinatorWelcome()])
      setShowSuggestions(true)
      refreshSidebarConversations()
    }

    const handleLoadConversationEvent = async (e) => {
      const conversationId = e?.detail?.conversationId
      if (!conversationId) return
      setEmployeeChat(null)
      await handleLoadConversation(conversationId)
    }

    // فتح شات مخصّص مرتبط بموظف ذكي (من بطاقته في درج الموظفين)
    // يحفظ/يجلب جلسة المحادثة الخاصة به (emp_<label>) ويحمّل سياقها السابق.
    const handleOpenEmployeeChat = async (e) => {
      const label = e?.detail?.label
      if (!label) return
      const sessionId = `emp_${label}`
      setPinnedAccount(label)
      setEmployeeChat(label)
      localStorage.setItem('session_id', sessionId)
      setShowSuggestions(false)

      const welcome = {
        id: Date.now(),
        type: 'assistant',
        content:
          `🤖 **دردشة مع الموظف الذكي @${label}**\n\n` +
          'أنا موظفك الذكي على هذا الحساب — ناقشني أو وجّهني مباشرةً. يمكنك:\n\n' +
          '• 🧠 «حلّل واستنتج المواضيع» من الأخبار والترند والتايم لاين وأهم البوستات\n' +
          '• 📊 معرفة إحصائياتي وأدائي\n' +
          '• 💡 تحسين سلوكي واقتراحات لتطوير المحتوى\n' +
          '•  جدولة المحتوى اليومي · 🚀 تشغيل دورة فورية\n\n' +
          'استخدم الأزرار السريعة بالأسفل، أو اكتب لي أي شيء يخصّ الحساب والمحتوى.',
        timestamp: new Date().toISOString()
      }

      // جهّز/اجلب محادثة الموظف مع سياقها المحفوظ من الخادم
      try {
        const token = localStorage.getItem('token')
        const res = await axios.post('/api/conversations/ensure', {
          session_id: sessionId,
          title: `🤖 الموظف @${label}`,
        }, { headers: token ? { Authorization: `Bearer ${token}` } : {} })

        const msgs = res.data?.messages || []
        if (Array.isArray(msgs) && msgs.length > 0) {
          setMessages(msgs.map(m => ({
            id: m.id,
            type: m.role === 'user' ? 'user' : 'assistant',
            content: m.content,
            attachment: m.attachment || null,
            timestamp: m.created_at,
          })))
        } else {
          setMessages([welcome])
        }
      } catch (err) {
        console.error('ensure employee conversation failed:', err)
        setMessages([welcome])
      }
      refreshSidebarConversations()
    }

    window.addEventListener('mwj:new_chat', handleNewChat)
    window.addEventListener('mwj:load_conversation', handleLoadConversationEvent)
    window.addEventListener('mwj:open_employee_chat', handleOpenEmployeeChat)
    return () => {
      window.removeEventListener('mwj:new_chat', handleNewChat)
      window.removeEventListener('mwj:load_conversation', handleLoadConversationEvent)
      window.removeEventListener('mwj:open_employee_chat', handleOpenEmployeeChat)
    }
  }, [user])

  const connectWebSocket = () => {
    // اتصال واحد فقط: لا تنشئ سوكتاً جديداً إن كان الحالي مفتوحاً/قيد الفتح
    const existing = wsRef.current
    if (existing && (existing.readyState === WebSocket.OPEN ||
                     existing.readyState === WebSocket.CONNECTING)) {
      return
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    // التوكن في أول رسالة لا في الرابط: الرابط يُكتب كاملاً في سجلات الخادم والبروكسي
    const wsUrl = `${protocol}//${window.location.host}/ws/chat`
    
    const websocket = new WebSocket(wsUrl)

    websocket.onopen = () => {
      websocket.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('token') || '' }))
      console.log('WebSocket connected')
      setIsConnected(true)
      retryRef.current = 0
      lastInboundRef.current = Date.now()
      startHeartbeat()
    }

    websocket.onmessage = (event) => {
      lastInboundRef.current = Date.now()
      const data = JSON.parse(event.data)
      // الـpong إشارة حياة فقط — لا شأن له بمنطق المحادثة
      if (data?.type === 'pong' || data?.type === 'auth_ok' || data?.type === 'auth_expired') return
      handleWebSocketMessage(data)
    }
    
    websocket.onerror = (error) => {
      console.error('WebSocket error:', error)
    }

    websocket.onclose = (event) => {
      console.log('WebSocket disconnected')
      setIsConnected(false)
      stopHeartbeat()
      // 4401 = التوكن مرفوض/منتهٍ: إعادة الاتصال بلا جدوى — أعِد المستخدم لتسجيل الدخول
      if (event?.code === 4401) {
        wsRef.current = null
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        window.location.assign('/login')
        return
      }
      // أعد الاتصال فقط إن كان هذا هو السوكت النشط (ليس إغلاقاً مقصوداً
      // من التنظيف ولا سوكتاً قديماً استُبدل) — يمنع تراكم الاتصالات المكررة
      if (wsRef.current === websocket) {
        wsRef.current = null
        scheduleReconnect()
      }
    }
    
    wsRef.current = websocket
    setWs(websocket)
  }

  // تراجع أسّي: إعادة اتصال كل 3 ثوان ثابتة تُغرق الخادم أثناء إعادة تشغيله
  // وتُغرق سجل vite بالأخطاء؛ نبدأ سريعاً ثم نتباعد بسقف 15 ثانية.
  const scheduleReconnect = () => {
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
    const attempt = retryRef.current++
    const delay = Math.min(1000 * Math.pow(2, attempt), 15000)
    reconnectTimerRef.current = setTimeout(connectWebSocket, delay)
  }

  const stopHeartbeat = () => {
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current)
      heartbeatRef.current = null
    }
  }

  const startHeartbeat = () => {
    stopHeartbeat()
    heartbeatRef.current = setInterval(() => {
      const sock = wsRef.current
      if (!sock || sock.readyState !== WebSocket.OPEN) return
      // صمت طويل مع سوكت «مفتوح» = اتصال ميت: أغلقه ليعمل onclose ويعيد الوصل
      if (Date.now() - lastInboundRef.current > SILENCE_LIMIT_MS) {
        console.warn('WebSocket صامت — إعادة الاتصال')
        try { sock.close() } catch { /* ignore */ }
        return
      }
      try { sock.send(JSON.stringify({ type: 'ping' })) } catch { /* ignore */ }
    }, HEARTBEAT_MS)
  }

  const handleWebSocketMessage = (data) => {
    if (data.type === 'typing') {
      setIsTyping(data.status)
    } else if (data.type === 'assistant_message') {
      // تجاهل الرسائل الفارغة أو null
      if (data.message && data.message !== null) {
        // مع أكثر من موظف فعال: رسائل كل موظف تحمل session_id محادثته
        // (emp_<account>). نعرضها هنا فقط إذا كانت محادثته هي المفتوحة —
        // وإلا تبقى محفوظة في محادثتها ويكفي تحديث الشريط الجانبي.
        const openSession = localStorage.getItem('session_id')
        let content = data.message
        if (data.session_id && openSession && data.session_id !== openSession) {
          // استثناء: موظف أسند إليه المنسّق مهمة من هذه المحادثة للتو
          const handle = data.session_id.startsWith('emp_') ? data.session_id.slice(4) : null
          const since = handle ? awaitingRef.current[handle] : null
          if (!since || Date.now() - since > AWAIT_RESULTS_MS || openSession.startsWith('emp_')) {
            refreshSidebarConversations()
            return
          }
          content = `📨 **من @${handle}:**\n\n${data.message}`
        }
        for (const h of data.metadata?.dispatched || []) awaitingRef.current[h] = Date.now()
        setMessages(prev => [...prev, {
          id: Date.now() + Math.random(),
          type: 'assistant',
          content,
          attachment: data.attachment || null,
          timestamp: data.timestamp
        }])
        refreshSidebarConversations()
      }
    } else if (data.type === 'error') {
      // عرض رسالة خطأ عامة إذا كانت الرسالة null
      const errorMessage = data.message || 'حدث خطأ غير متوقع'
      setMessages(prev => [...prev, {
        id: Date.now(),
        type: 'error',
        content: errorMessage,
        timestamp: data.timestamp
      }])
    }
  }

  // إرسال نص للخادم (يستخدمه صندوق الإدخال وأزرار الإجراءات السريعة للموظف)
  const sendMessageText = (text) => {
    const content = (text || '').trim()
    if (!content || !isConnected) return

    setMessages(prev => [...prev, {
      id: Date.now(),
      type: 'user',
      content,
      timestamp: new Date().toISOString()
    }])
    setShowSuggestions(false)
    refreshSidebarConversations()

    if (ws && ws.readyState === WebSocket.OPEN) {
      const messageData = {
        message: content,
        user_id: user?.id || null,
        user_email: user?.email || null,
        session_id: localStorage.getItem('session_id') || `session_${Date.now()}`,
        pinned_account: pinnedAccount || null
      }
      if (!localStorage.getItem('session_id')) {
        localStorage.setItem('session_id', messageData.session_id)
      }
      ws.send(JSON.stringify(messageData))
    }
  }

  const handleSendMessage = () => {
    if (!inputValue.trim() || !isConnected) return
    sendMessageText(inputValue)
    setInputValue('')
  }

  const uploadAttachment = async (file) => {
    const form = new FormData()
    form.append('file', file)
    const response = await axios.post('/api/uploads', form, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })

    const data = response.data
    const fullUrl = data.url?.startsWith('http') ? data.url : new URL(data.url, window.location.origin).toString()

    return {
      kind: data.kind,
      url: data.url,
      url_full: fullUrl,
      original_name: data.original_name,
      size: data.size,
      content_type: data.content_type
    }
  }

  const sendAttachmentMessage = async (file) => {
    if (!isConnected || !ws || ws.readyState !== WebSocket.OPEN) return

    const attachment = await uploadAttachment(file)
    const messageData = {
      message: '',
      attachment,
      user_id: user?.id || null,
      user_email: user?.email || null,
      session_id: localStorage.getItem('session_id') || `session_${Date.now()}`,
      pinned_account: pinnedAccount || null
    }

    if (!localStorage.getItem('session_id')) {
      localStorage.setItem('session_id', messageData.session_id)
    }

    setMessages(prev => [...prev, {
      id: Date.now(),
      type: 'user',
      content: '',
      attachment,
      timestamp: new Date().toISOString()
    }])

    setShowSuggestions(false)

    refreshSidebarConversations()

    ws.send(JSON.stringify(messageData))
  }

  // رفع ملف كوكيز (JSON) أو ملف CSV لإضافة/تسجيل حسابات عبر الشات
  const sendCookiesFile = (file) => {
    if (!isConnected || !ws || ws.readyState !== WebSocket.OPEN) {
      notifyChat('⚠️ لا يوجد اتصال بالخادم حالياً — حاول مرة أخرى بعد لحظات.')
      return
    }

    const name = (file.name || '').toLowerCase()
    if (!name.endsWith('.json') && !name.endsWith('.csv')) {
      notifyChat('❌ نوع الملف غير مدعوم. ارفع ملف كوكيز بصيغة JSON أو ملف CSV.')
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      const content = reader.result
      const sessionId = localStorage.getItem('session_id') || `session_${Date.now()}`
      if (!localStorage.getItem('session_id')) {
        localStorage.setItem('session_id', sessionId)
      }

      setMessages(prev => [...prev, {
        id: Date.now(),
        type: 'user',
        content: `📎 رفع ملف: ${file.name}`,
        timestamp: new Date().toISOString()
      }])
      setShowSuggestions(false)

      ws.send(JSON.stringify({
        message: '',
        user_id: user?.id || null,
        user_email: user?.email || null,
        session_id: sessionId,
        file_upload: {
          name: file.name,
          content,
          type: file.type
        }
      }))
    }
    reader.onerror = () => notifyChat('❌ تعذّر قراءة الملف. تأكد أنه ملف نصي صالح.')
    reader.readAsText(file)
  }

  const handleNewChat = () => {
    if (window.confirm('هل تريد بدء محادثة جديدة؟ سيتم حذف المحادثة الحالية.')) {
      setMessages([coordinatorWelcome()])
      // إنشاء session_id جديد
      localStorage.setItem('session_id', `session_${Date.now()}`)
      setShowSuggestions(true)
      refreshSidebarConversations()
    }
  }

  const handleSuggestionClick = (text) => {
    setInputValue(text)
    setShowSuggestions(false)
    if (typeof inputFocusRef.current === 'function') {
      inputFocusRef.current()
    }
  }

  const handleLoadConversation = async (conversationId) => {
    try {
      const token = localStorage.getItem('token')
      const response = await axios.get(
        `/api/conversations/${conversationId}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      )
      
      const payload = response.data
      const msgs = payload?.messages || payload?.conversation?.messages || payload?.conversation_detail?.messages
      const conv = payload?.conversation || payload?.conversation_detail?.conversation

      if (Array.isArray(msgs)) {
        // تحويل الرسائل إلى التنسيق المطلوب
        const loadedMessages = msgs.map(msg => ({
          id: msg.id,
          type: msg.role === 'user' ? 'user' : 'assistant',
          content: msg.content,
          attachment: msg.attachment || null,
          timestamp: msg.created_at
        }))
        
        setMessages(loadedMessages)
        
        // تحديث session_id للمحادثة المحملة
        const loadedSession = conv?.session_id || payload?.session_id || `session_${conversationId}`
        localStorage.setItem('session_id', loadedSession)

        // محادثة موظف (emp_<account>)؟ ثبّت حسابه تلقائياً — بدون هذا تبقى
        // أوامر الشات («انشر عن…»، «تفاعل مع…») موجّهة للموظف المثبّت السابق
        // (الأول عادةً) رغم أنك تحادث موظفاً آخر.
        if (loadedSession.startsWith('emp_')) {
          const label = loadedSession.slice(4)
          setPinnedAccount(label)
          setEmployeeChat(label)
        }
        refreshSidebarConversations()
      }
    } catch (error) {
      console.error('Failed to load conversation:', error)
      alert('فشل تحميل المحادثة')
    }
  }

  return (
      <main className="flex-1 flex flex-col h-full bg-background-light dark:bg-background-dark relative">
        <div className="flex flex-col">
          {/* Header */}
          <header className="relative flex items-center justify-between px-6 py-5 min-h-[64px] shrink-0 bg-card-light dark:bg-sidebar-dark z-10">
            <button
              onClick={() => setDarkMode(!darkMode)}
              className="absolute left-6 top-1/2 -translate-y-1/2 flex items-center justify-center rounded-xl size-10 hover:bg-gray-50 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark transition-colors"
              title="تبديل المظهر"
            >
              {darkMode ? <FiSun size={20} /> : <FiMoon size={20} />}
            </button>
            <div className="flex items-center gap-2">
              <button className="flex items-center justify-center rounded-xl size-10 hover:bg-gray-50 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark transition-colors md:hidden">
                <FiMenu size={20} />
              </button>
            </div>
            <div className="flex items-center gap-2 text-text-primary-light dark:text-text-primary-dark">
              <span className="hidden md:inline text-sm font-bold">
                {employeeChat ? `🤖 الموظف @${employeeChat}` : '🧭 المنسّق العام'}
              </span>
              <ChatModelPicker />
              {pinnedAccount && (
                <div
                  className="flex items-center gap-1.5 pl-2 pr-3 py-1.5 rounded-full bg-primary/10 text-primary border border-primary/20"
                  title="الحساب المثبّت — كل الإجراءات تُنفّذ عليه"
                >
                  <MdPushPin size={16} />
                  <span className="text-xs font-bold">@{pinnedAccount}</span>
                  <button
                    onClick={() => handlePinAccount(null)}
                    className="hover:text-red-500 transition-colors"
                    title="إلغاء التثبيت"
                  >
                    <MdClose size={16} />
                  </button>
                </div>
              )}
              <button
                onClick={() => setAgentsOpen(true)}
                className="flex items-center gap-2 px-3 py-2 rounded-xl border border-primary/30 bg-primary/5 hover:bg-primary/10 text-primary transition-colors"
                title="الموظفين الذكيين — فعّل حساباً ليعمل تلقائياً بالجدولة"
              >
                <MdSmartToy size={20} />
                <span className="text-sm font-medium hidden sm:inline">الموظفين الذكيين</span>
              </button>
              <button
                onClick={() => setAccountsOpen(true)}
                className="flex items-center gap-2 px-3 py-2 rounded-xl border border-border-light dark:border-border-dark hover:bg-gray-50 dark:hover:bg-card-dark text-text-primary-light dark:text-text-primary-dark transition-colors"
                title="حساباتي — ثبّت حساباً لتنفيذ الإجراءات عليه"
              >
                <MdManageAccounts size={20} className="text-primary" />
                <span className="text-sm font-medium hidden sm:inline">حساباتي</span>
              </button>
            </div>
          </header>

          {/* فاصل أفقي تحت Header - يمتد بعرض كامل */}
          <div className="h-px bg-border-light dark:bg-border-dark w-full"></div>
        </div>

        {/* Messages */}
        <MessageList 
          messages={messages}
          isTyping={isTyping}
          messagesEndRef={messagesEndRef}
          showSuggestions={showSuggestions}
          onSuggestionClick={handleSuggestionClick}
        />

        {/* شريط الفريق — الشات الرئيسي هو المنسّق العام لكل الموظفين */}
        {!employeeChat && (
          <TeamBar
            team={team}
            userId={user?.id}
            pinned={pinnedAccount}
            disabled={!isConnected}
            onMention={insertMention}
            onOpenChat={openEmployeeChat}
            onQuick={(text, send) => (send ? sendMessageText(text) : handleSuggestionClick(text))}
          />
        )}

        {/* شريط الإجراءات السريعة — يظهر في وضع الدردشة مع الموظف الذكي */}
        {employeeChat && (
          <div className="px-4 pt-2">
            <div className="flex items-center gap-2 mb-1.5">
              <MdSmartToy size={16} className="text-primary" />
              <span className="text-xs font-bold text-primary">دردشة مع الموظف @{employeeChat}</span>
              <button
                onClick={() => window.dispatchEvent(new CustomEvent('mwj:new_chat'))}
                className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark hover:text-primary transition-colors"
                title="العودة لشات المنسّق العام"
              >
                ↩ المنسّق العام
              </button>
            </div>
            <div className="flex items-center gap-2 overflow-x-auto pb-1">
              {[
                { icon: '🧠', label: 'حلّل واستنتج المواضيع', text: 'بناءً على الأخبار والترند والتايم لاين وأهم البوستات، ما المواضيع المرتبطة باهتمامك؟' },
                { icon: '�', label: 'اعرض الأخبار', text: 'اعرض الأخبار المتعلقة بتخصصك' },
                { icon: '📈', label: 'اعرض الترند', text: 'اعرض الترند الذي يراه حسابك' },
                { icon: '🧵', label: 'التايم لاين', text: 'اعرض التايم لاين' },
                { icon: '🏆', label: 'أهم البوستات', text: 'اعرض أهم البوستات' },
                { icon: '�📊', label: 'إحصائياتك', text: 'حالة الموظف' },
                { icon: '💡', label: 'حسّن سلوكك', text: 'اقترح تحسينات لأدائك' },
                { icon: '📝', label: 'آخر محتوياتك', text: 'ماذا نشرت مؤخراً؟' },
                { icon: '🗒️', label: 'راجع المواضيع', text: 'راجع المواضيع المتاحة للنشر' },
                { icon: '📅', label: 'جدول محتوى يومي', text: 'اضبط للموظف محتوى يومي: 3 تغريدات' },
                { icon: '🚀', label: 'اشتغل الآن', text: 'شغّل دورة الآن' },
              ].map((qa) => (
                <button
                  key={qa.label}
                  onClick={() => sendMessageText(qa.text)}
                  disabled={!isConnected}
                  className="shrink-0 text-xs px-3 py-1.5 rounded-full border border-primary/30 bg-primary/5 hover:bg-primary/10 text-primary font-medium transition-colors disabled:opacity-40"
                >
                  {qa.icon} {qa.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Input */}
        <MessageInput 
          inputValue={inputValue}
          setInputValue={setInputValue}
          handleSendMessage={handleSendMessage}
          isConnected={isConnected}
          onAttachImage={sendAttachmentMessage}
          onAttachFile={sendAttachmentMessage}
          onAttachCookies={sendCookiesFile}
          onFocusRef={inputFocusRef}
          mentionOptions={team}
        />

        {/* درج حساباتي — تثبيت الحساب المُنفَّذ عليه */}
        <AccountsDrawer
          open={accountsOpen}
          onClose={() => setAccountsOpen(false)}
          pinned={pinnedAccount}
          onPin={handlePinAccount}
        />

        {/* درج الموظفين الذكيين — التفعيل الآلي بالجدولة */}
        <AgentsDrawer
          open={agentsOpen}
          onClose={() => setAgentsOpen(false)}
          onNotify={notifyChat}
        />
      </main>
  )
}

export default ChatInterface
