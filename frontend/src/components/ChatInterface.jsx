import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { FiMenu, FiSun, FiMoon } from 'react-icons/fi'
import { MdManageAccounts, MdPushPin, MdClose, MdSmartToy } from 'react-icons/md'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import AccountsDrawer from './AccountsDrawer'
import AgentsDrawer from './AgentsDrawer'
import usePinnedAccount from '../hooks/usePinnedAccount'
import logoLight from '../assets/logos/logo-light.png'
import logoDark from '../assets/logos/logo-dark.png'

const ChatInterface = ({ darkMode, setDarkMode, user }) => {
  const logo = darkMode ? logoLight : logoDark
  const [messages, setMessages] = useState([
    {
      id: 1,
      type: 'assistant',
      content: 'مرحباً! أنا مساعدك الذكي في Mwj AI. يمكنني مساعدتك في إدارة وسائل التواصل الاجتماعي، التحليلات، والأتمتة. كيف يمكنني مساعدتك اليوم؟',
      timestamp: new Date().toISOString()
    }
  ])
  const [inputValue, setInputValue] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [ws, setWs] = useState(null)
  // مرجع السوكت الفعلي + مؤقّت إعادة الاتصال — يضمنان اتصالاً واحداً فقط
  // (StrictMode يركّب المكوّن مرتين في التطوير فكانت الرسائل تصل مكررة)
  const wsRef = useRef(null)
  const reconnectTimerRef = useRef(null)
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
        ? `📌 تم تثبيت الحساب @${label}. كل الإجراءات القادمة ستُنفّذ على هذا الحساب.`
        : '📍 تم إلغاء تثبيت الحساب.',
      timestamp: new Date().toISOString()
    }])
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
    return () => {
      // ألغِ أي إعادة اتصال مجدولة، ثم أغلق السوكت الحالي إغلاقاً مقصوداً
      // (تصفير wsRef قبل close يمنع onclose من إعادة الاتصال)
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
      setMessages([{
        id: Date.now(),
        type: 'assistant',
        content: 'مرحباً! أنا مساعدك الذكي في Mwj AI. يمكنني مساعدتك في إدارة وسائل التواصل الاجتماعي، التحليلات، والأتمتة. كيف يمكنني مساعدتك اليوم؟',
        timestamp: new Date().toISOString()
      }])
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
    const wsUrl = `${protocol}//${window.location.host}/ws/chat`
    
    const websocket = new WebSocket(wsUrl)
    
    websocket.onopen = () => {
      console.log('WebSocket connected')
      setIsConnected(true)
    }
    
    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleWebSocketMessage(data)
    }
    
    websocket.onerror = (error) => {
      console.error('WebSocket error:', error)
    }
    
    websocket.onclose = () => {
      console.log('WebSocket disconnected')
      setIsConnected(false)
      // أعد الاتصال فقط إن كان هذا هو السوكت النشط (ليس إغلاقاً مقصوداً
      // من التنظيف ولا سوكتاً قديماً استُبدل) — يمنع تراكم الاتصالات المكررة
      if (wsRef.current === websocket) {
        wsRef.current = null
        reconnectTimerRef.current = setTimeout(connectWebSocket, 3000)
      }
    }
    
    wsRef.current = websocket
    setWs(websocket)
  }

  const handleWebSocketMessage = (data) => {
    if (data.type === 'typing') {
      setIsTyping(data.status)
    } else if (data.type === 'assistant_message') {
      // تجاهل الرسائل الفارغة أو null
      if (data.message && data.message !== null) {
        setMessages(prev => [...prev, {
          id: Date.now(),
          type: 'assistant',
          content: data.message,
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
      setMessages([{
        id: Date.now(),
        type: 'assistant',
        content: 'مرحباً! أنا مساعدك الذكي في Mwj AI. يمكنني مساعدتك في إدارة وسائل التواصل الاجتماعي، التحليلات، والأتمتة. كيف يمكنني مساعدتك اليوم؟',
        timestamp: new Date().toISOString()
      }])
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
        localStorage.setItem('session_id', conv?.session_id || payload?.session_id || `session_${conversationId}`)
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

        {/* شريط الإجراءات السريعة — يظهر في وضع الدردشة مع الموظف الذكي */}
        {employeeChat && (
          <div className="px-4 pt-2">
            <div className="flex items-center gap-2 mb-1.5">
              <MdSmartToy size={16} className="text-primary" />
              <span className="text-xs font-bold text-primary">دردشة مع الموظف @{employeeChat}</span>
              <button
                onClick={() => setEmployeeChat(null)}
                className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark hover:text-red-500 transition-colors"
                title="إنهاء وضع دردشة الموظف"
              >
                إنهاء
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
