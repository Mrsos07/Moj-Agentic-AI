import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { Menu, Sun, Moon, Users, CircleUserRound, Pin, X, MessageSquare, Brain, Newspaper, TrendingUp, ListTree, Trophy, BarChart3, Lightbulb, FileText, ListChecks, CalendarDays, Play } from 'lucide-react'
import GlassIcon from './ui/GlassIcon'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import AccountsDrawer from './AccountsDrawer'
import AgentsDrawer from './AgentsDrawer'
import TeamBar from './TeamBar'
import ChatModelPicker from './ChatModelPicker'
import TaskActivityPanel, { STATUS as TASK_STATUS } from './TaskActivityPanel'
import authHeaders from '../authHeaders'
import usePinnedAccount from '../hooks/usePinnedAccount'
import logoLight from '../assets/logos/logo-light.png'
import logoDark from '../assets/logos/logo-dark.png'

// نتائج أوامر المنسّق تصل في محادثة الموظف (emp_<handle>)؛ نعرضها أيضاً في
// شات المنسّق خلال هذه المدة بعد الإسناد، وإلا بدا الأمر وكأنه لم يُنفّذ.
const AWAIT_RESULTS_MS = 30 * 60 * 1000

// صندوق الانتظار: رسائل لم يؤكّد الخادم استلامها (task_update بمعرّفها). تبقى في
// localStorage فتُرسل عند عودة الاتصال ولو أُعيد تحميل الصفحة؛ الخادم يمنع التكرار بـtask_id.
const OUTBOX_KEY = 'chat_outbox'
const loadOutbox = () => {
  try { return JSON.parse(localStorage.getItem(OUTBOX_KEY) || '[]') || [] } catch { return [] }
}
const newTaskId = () =>
  (window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`)
    .replace(/[^A-Za-z0-9_-]/g, '')

// مهمة الخادم ← صيغة لوحة سجل التنفيذ
const toTaskView = (t) => t && ({
  id: t.id,
  title: t.title || (t.kind === 'cycle' ? 'دورة موظف' : 'طلب'),
  status: t.status,
  progress: typeof t.progress === 'number' ? t.progress : undefined,
  startedAt: t.started_at,
  finishedAt: t.finished_at,
})
const LEVEL_OF = { running: 'info', succeeded: 'success', failed: 'error', interrupted: 'warning' }
const toTaskEvent = (t) => ({
  id: t.id,
  taskId: t.id,
  title: t.title || (t.kind === 'cycle' ? 'دورة موظف' : 'طلب'),
  detail: [
    TASK_STATUS[t.status]?.label || t.status,
    t.phase_label && t.status !== 'succeeded' ? `المرحلة: ${t.phase_label}` : null,
    t.done_actions ? `${t.done_actions} فعل نُفّذ على X` : null,
    t.error,
  ].filter(Boolean).join(' · '),
  timestamp: t.finished_at || t.started_at,
  level: LEVEL_OF[t.status] || 'info',
  resumable: t.status === 'interrupted',
})

const coordinatorWelcome = () => ({
  id: Date.now(),
  type: 'assistant',
  content:
    'أهلاً! أنا **موج**، أدير فريق وكلائك على X.\n\n' +
    'أرى كل وكلائك وأوجّههم نيابةً عنك. جرّب مثلاً:\n\n' +
    '• «وش حالة فريقي اليوم؟»\n' +
    '• «خل كل الوكلاء ينشرون عن اليوم الوطني كل واحد حسب تخصصه»\n' +
    '• «@اليوزر روح للتايم لاين وردّ على تغريدة تناسب تخصصك»\n' +
    '• «خل عماد يشتغل كل 45 دقيقة وركّز اهتماماته على الاقتصاد»\n\n' +
    'اذكر وكيلاً بـ **@** لتخاطبه مباشرة، أو افتح محادثته من شريط الفريق.',
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
  const [everConnected, setEverConnected] = useState(false)
  // نقاط الاستعادة: مهام الخادم (جارية/مقطوعة/منتهية) + آخر رقم تسلسلي رأيناه من الرسائل،
  // يُرسل عند عودة الاتصال ليعيد الخادم ما فات. null = صفحة جديدة (لا إعادة، لقطة فقط)
  const [tasks, setTasks] = useState([])
  const lastSeqRef = useRef(null)
  const seenSeqRef = useRef(new Set())
  const outboxRef = useRef(loadOutbox())
  const [showSuggestions, setShowSuggestions] = useState(true)
  const [accountsOpen, setAccountsOpen] = useState(false)
  const [agentsOpen, setAgentsOpen] = useState(false)
  // وضع "الدردشة مع الموظف الذكي": اسم الحساب المرتبط أو null
  const [employeeChat, setEmployeeChat] = useState(null)
  const { pinned: pinnedAccount, setPinned: setPinnedAccount } = usePinnedAccount()
  const messagesEndRef = useRef(null)
  const inputFocusRef = useRef(null)

  const headerBtn =
    'moj-pill moj-hover flex items-center gap-2 h-10 px-3 rounded-xl text-sm font-medium transition-colors duration-150 ' +
    'focus-visible:outline-none focus-visible:ring-2 ' +
    'focus-visible:ring-primary/60 focus-visible:ring-offset-1'

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
        ? `📌 تم تثبيت الحساب @${label}. الطلبات التي لا تحدّد وكيلاً ستُوجَّه إليه، ويبقى المنسّق يرى الفريق كله.`
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
          `**دردشة مع الوكيل @${label}**\n\n` +
          'أنا وكيلك على هذا الحساب — ناقشني أو وجّهني مباشرةً. يمكنك:\n\n' +
          '• «حلّل واستنتج المواضيع» من الأخبار والترند والتايم لاين وأهم البوستات\n' +
          '• معرفة إحصائياتي وأدائي\n' +
          '• تحسين سلوكي واقتراحات لتطوير المحتوى\n' +
          '• جدولة المحتوى اليومي · تشغيل دورة فورية\n\n' +
          'استخدم الأزرار السريعة بالأسفل، أو اكتب لي أي شيء يخصّ الحساب والمحتوى.',
        timestamp: new Date().toISOString()
      }

      // جهّز/اجلب محادثة الموظف مع سياقها المحفوظ من الخادم
      try {
        const token = localStorage.getItem('token')
        const res = await axios.post('/api/conversations/ensure', {
          session_id: sessionId,
          title: `الوكيل @${label}`,
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
      setEverConnected(true)
      retryRef.current = 0
      lastInboundRef.current = Date.now()
      startHeartbeat()
    }

    websocket.onmessage = (event) => {
      lastInboundRef.current = Date.now()
      const data = JSON.parse(event.data)
      // الـpong إشارة حياة فقط — لا شأن له بمنطق المحادثة
      if (data?.type === 'pong' || data?.type === 'auth_expired') return
      if (data?.type === 'auth_ok') {
        // بعد التوثيق: اطلب ما فات منذ آخر رسالة رأيناها + لقطة المهام، ثم أرسل ما في
        // صندوق الانتظار (رسائل كُتبت أثناء الانقطاع أو لم يؤكَّد استلامها)
        setIsTyping(false)
        websocket.send(JSON.stringify({ type: 'resume', since: lastSeqRef.current }))
        for (const m of outboxRef.current) websocket.send(JSON.stringify(m))
        return
      }
      if (typeof data?.seq === 'number') {
        const seen = seenSeqRef.current
        if (seen.has(data.seq)) return
        seen.add(data.seq)
        if (seen.size > 600) seenSeqRef.current = new Set([...seen].slice(-300))
        lastSeqRef.current = Math.max(lastSeqRef.current ?? 0, data.seq)
      }
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

  const saveOutbox = (list) => {
    outboxRef.current = list
    try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(list)) } catch { /* ممتلئ — يبقى في الذاكرة */ }
  }

  // الخادم عرف المهمة (سجّلها أو رآها مكررة) ← لا داعي لإعادة إرسالها
  const ackOutbox = (ids) => {
    const known = new Set(ids)
    if (outboxRef.current.some(m => known.has(m.task_id))) {
      saveOutbox(outboxRef.current.filter(m => !known.has(m.task_id)))
    }
  }

  const handleTaskUpdate = (t) => {
    if (!t) return
    ackOutbox([t.id])
    setTasks(prev => [t, ...prev.filter(x => x.id !== t.id)].slice(0, 30))
    if (t.kind === 'chat' && t.status !== 'running' && t.session_id === localStorage.getItem('session_id')) {
      setIsTyping(false)
    }
  }

  const handleWebSocketMessage = (data) => {
    if (data.type === 'tasks_snapshot') {
      const list = data.tasks || []
      lastSeqRef.current = Math.max(lastSeqRef.current ?? 0, data.seq || 0)
      setTasks(list)
      ackOutbox(list.map(t => t.id))
      const open = localStorage.getItem('session_id')
      setIsTyping(list.some(t => t.kind === 'chat' && t.status === 'running' && t.session_id === open))
    } else if (data.type === 'task_update') {
      // أمر انتظار تسلّمته دورته: يختفي من السجل وتحلّ محله الدورة
      if (data.removed && data.task) setTasks(prev => prev.filter(x => x.id !== data.task.id))
      else handleTaskUpdate(data.task)
    } else if (data.type === 'typing') {
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
  // كل رسالة تحمل task_id وتدخل صندوق الانتظار حتى يؤكّدها الخادم: إن انقطع الاتصال
  // قبل وصولها أو أثناء تنفيذها تُعاد عند عودته، ولا تُنفّذ مرتين (الخادم يميّزها بالمعرّف)
  const queueAndSend = (messageData) => {
    saveOutbox([...outboxRef.current, messageData].slice(-20))
    const sock = wsRef.current
    if (sock && sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify(messageData))
  }

  const sendMessageText = (text) => {
    const content = (text || '').trim()
    if (!content) return

    setMessages(prev => [...prev, {
      id: Date.now(),
      type: 'user',
      content,
      timestamp: new Date().toISOString()
    }])
    setShowSuggestions(false)
    refreshSidebarConversations()

    const messageData = {
      message: content,
      task_id: newTaskId(),
      session_id: localStorage.getItem('session_id') || `session_${Date.now()}`,
      pinned_account: pinnedAccount || null
    }
    if (!localStorage.getItem('session_id')) {
      localStorage.setItem('session_id', messageData.session_id)
    }
    queueAndSend(messageData)
  }

  const handleSendMessage = () => {
    if (!inputValue.trim()) return
    sendMessageText(inputValue)
    setInputValue('')
  }

  const resumeTask = (taskId) => {
    const sock = wsRef.current
    if (!sock || sock.readyState !== WebSocket.OPEN) {
      notifyChat('⚠️ لا يوجد اتصال بالخادم حالياً — حاول الاستئناف بعد عودة الاتصال.')
      return
    }
    const t = tasks.find(x => x.id === taskId)
    notifyChat(`▶️ جارٍ استئناف: **${t?.title || 'المهمة'}**`)
    sock.send(JSON.stringify({ type: 'resume_task', task_id: taskId }))
  }

  const dismissTask = (taskId) => {
    const sock = wsRef.current
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ type: 'dismiss_task', task_id: taskId }))
    }
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
      task_id: newTaskId(),
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

    queueAndSend(messageData)
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
      <main className="flex-1 flex flex-col h-full bg-transparent relative">
        <div className="flex flex-col">
          {/* Header */}
          <header className="moj-glass rounded-2xl mx-4 mt-3 relative flex items-center justify-between gap-3 px-3 md:px-4 py-2 min-h-[56px] shrink-0 z-10">
            <div className="flex items-center gap-2">
              <button
                className={`${headerBtn} !px-0 size-10 justify-center text-text-secondary-light dark:text-text-secondary-dark md:hidden`}
                aria-label="القائمة"
              >
                <GlassIcon size="sm"><Menu size={16} /></GlassIcon>
              </button>
            </div>
            <div className="flex items-center gap-2 min-w-0 text-text-primary-light dark:text-text-primary-dark">
              {employeeChat && (
                <span className="hidden md:inline text-sm font-bold">
                  الوكيل <span dir="ltr" className="inline-block">@{employeeChat}</span>
                </span>
              )}
              <ChatModelPicker />
              {pinnedAccount && (
                <div
                  className="moj-pill flex items-center gap-1.5 ps-1.5 pe-2.5 h-8 rounded-xl text-primary"
                  title="الحساب المثبّت — كل الإجراءات تُنفّذ عليه"
                >
                  <Pin size={14} />
                  <span className="text-xs font-bold" dir="ltr">@{pinnedAccount}</span>
                  <button
                    onClick={() => handlePinAccount(null)}
                    className="moj-hover size-6 rounded-md inline-flex items-center justify-center hover:text-red-500 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                    title="إلغاء التثبيت"
                    aria-label="إلغاء التثبيت"
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
              <button
                onClick={() => setAgentsOpen(true)}
                className={`${headerBtn} text-primary dark:text-white`}
                title="الوكلاء — فعّل حساباً ليعمل تلقائياً بالجدولة"
              >
                <GlassIcon size="sm"><Users size={16} /></GlassIcon>
                <span className="hidden sm:inline">الوكلاء</span>
              </button>
              <button
                onClick={() => setAccountsOpen(true)}
                className={`${headerBtn} text-text-primary-light dark:text-text-primary-dark`}
                title="حساباتي — ثبّت حساباً لتنفيذ الإجراءات عليه"
              >
                <GlassIcon size="sm"><CircleUserRound size={16} /></GlassIcon>
                <span className="hidden sm:inline">حساباتي</span>
              </button>
              <button
                onClick={() => setDarkMode(!darkMode)}
                className={`${headerBtn} !px-0 size-10 justify-center text-text-secondary-light dark:text-text-secondary-dark shrink-0`}
                title="تبديل المظهر"
                aria-label="تبديل المظهر"
              >
                <GlassIcon size="sm">{darkMode ? <Sun size={16} /> : <Moon size={16} />}</GlassIcon>
              </button>
            </div>
          </header>
        </div>

        {/* Messages */}
        <MessageList 
          messages={messages}
          isTyping={isTyping}
          messagesEndRef={messagesEndRef}
          showSuggestions={showSuggestions}
          onSuggestionClick={handleSuggestionClick}
        />

        {/* سجل التنفيذ — مهام الخادم الحيّة: الجارية والمقطوعة (استئناف/تجاهل) وأحدث المنتهية */}
        <div className="px-4 md:px-6">
          <div className="max-w-[900px] mx-auto">
            <TaskActivityPanel
              task={toTaskView(
                tasks.find(t => t.status === 'running')
                || tasks.find(t => t.status === 'interrupted')
                || tasks[0]
              )}
              events={tasks.map(toTaskEvent)}
              sourceState={!everConnected ? 'loading' : isConnected ? 'ready' : 'disconnected'}
              onResume={resumeTask}
              onDismiss={dismissTask}
            />
          </div>
        </div>

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
              <MessageSquare size={16} className="text-primary" />
              <span className="text-xs font-bold text-primary">دردشة مع الوكيل <span dir="ltr" className="inline-block">@{employeeChat}</span></span>
              <button
                onClick={() => window.dispatchEvent(new CustomEvent('mwj:new_chat'))}
                className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark hover:text-primary transition-colors"
                title="العودة لشات المنسّق العام"
              >
                العودة للمنسّق
              </button>
            </div>
            <div className="flex items-center gap-2 overflow-x-auto pb-1">
              {[
                { icon: Brain, label: 'حلّل واستنتج المواضيع', text: 'بناءً على الأخبار والترند والتايم لاين وأهم البوستات، ما المواضيع المرتبطة باهتمامك؟' },
                { icon: Newspaper, label: 'اعرض الأخبار', text: 'اعرض الأخبار المتعلقة بتخصصك' },
                { icon: TrendingUp, label: 'اعرض الترند', text: 'اعرض الترند الذي يراه حسابك' },
                { icon: ListTree, label: 'التايم لاين', text: 'اعرض التايم لاين' },
                { icon: Trophy, label: 'أهم البوستات', text: 'اعرض أهم البوستات' },
                { icon: BarChart3, label: 'إحصائياتك', text: 'حالة الموظف' },
                { icon: Lightbulb, label: 'حسّن سلوكك', text: 'اقترح تحسينات لأدائك' },
                { icon: FileText, label: 'آخر محتوياتك', text: 'ماذا نشرت مؤخراً؟' },
                { icon: ListChecks, label: 'راجع المواضيع', text: 'راجع المواضيع المتاحة للنشر' },
                { icon: CalendarDays, label: 'جدول محتوى يومي', text: 'اضبط للموظف محتوى يومي: 3 تغريدات' },
                { icon: Play, label: 'اشتغل الآن', text: 'شغّل دورة الآن' },
              ].map((qa) => (
                <button
                  key={qa.label}
                  onClick={() => sendMessageText(qa.text)}
                  disabled={!isConnected}
                  className="moj-pill moj-hover shrink-0 inline-flex items-center gap-2 h-8 ps-2 pe-3 rounded-xl text-xs font-medium text-text-primary-light dark:text-text-primary-dark focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-40"
                >
                  <GlassIcon size="sm"><qa.icon size={14} /></GlassIcon> {qa.label}
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
