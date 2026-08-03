import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import {
  MdClose,
  MdArrowBack,
  MdArrowForward,
  MdPerson,
  MdAutoAwesome,
  MdContentCopy,
  MdChecklist,
  MdRocketLaunch,
  MdCheckCircle,
  MdSmartToy,
  MdUploadFile,
  MdKey,
  MdBadge,
} from 'react-icons/md'

/**
 * معالج إنشاء موظف ذكي جديد — آلية منظمة من 4 خطوات:
 *  1) الحساب: اختر حساب X متصل (بلا موظف بعد).
 *  2) الشخصية: نموذج جاهز / توليد بالذكاء الاصطناعي / استنساخ حساب — ثم عدّل.
 *  3) القواعد والجدولة: الاهتمامات محسومة في الخطوة 2؛ هنا الأهداف والحدود والتوقيت.
 *  4) مراجعة وتفعيل: ملخص كامل ثم إنشاء الموظف وتفعيله بضغطة واحدة.
 */

const STEPS = [
  { id: 1, title: 'الحساب', icon: MdPerson },
  { id: 2, title: 'الشخصية', icon: MdAutoAwesome },
  { id: 3, title: 'القواعد والجدولة', icon: MdChecklist },
  { id: 4, title: 'مراجعة وتفعيل', icon: MdRocketLaunch },
]

const TONES = [
  { id: 'professional', label: 'احترافية' },
  { id: 'casual', label: 'ودّية' },
  { id: 'witty', label: 'ذكية وخفيفة' },
  { id: 'bold', label: 'جريئة' },
]

const DEFAULT_FORM = {
  account: '',
  // ربط الحساب: تسجيل دخول بالبيانات
  login_username: '',
  login_password: '',
  login_email: '',
  // persona
  display_name: '',
  role: 'Social Media Manager',
  tone: 'professional',
  language: 'ar',
  dialect: '',
  bio: '',
  instructions: '',
  dos: '',
  donts: '',
  interests: [],
  customInterest: '',
  brief: '',
  clone_handle: '',
  // rules & schedule
  goals: '',
  interval_minutes: 60,
  active_hours_start: 8,
  active_hours_end: 23,
  daily_post_target: 1,
  max_actions_per_cycle: 5,
  max_replies_per_cycle: 1,
  max_retweets_per_cycle: 1,
  max_likes_per_cycle: 3,
  max_follows_per_cycle: 1,
  max_quotes_per_cycle: 1,
  max_bookmarks_per_cycle: 1,
  allow_threads: true,
  generate_images: 'auto',
  image_style: 'editorial',
  // مزامنة الهوية على ملف حساب X الفعلي
  sync_profile: false,
  profile_location: '',
  profile_website: '',
  profile_avatar_url: '',
  profile_banner_url: '',
}

const inputCls =
  'w-full text-sm px-3 py-2 rounded-lg border border-border-light dark:border-border-dark ' +
  'bg-transparent text-text-primary-light dark:text-text-primary-dark focus:outline-none focus:border-primary'
const labelCls = 'block text-[11px] font-medium text-text-secondary-light dark:text-text-secondary-dark mb-1'

const EmployeeWizard = ({ open, onClose, onNotify, onCreated }) => {
  const [step, setStep] = useState(1)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [accounts, setAccounts] = useState([])       // [{label, hasEmployee}]
  const [interestsMeta, setInterestsMeta] = useState([])  // [{id, label}]
  const [templates, setTemplates] = useState({})     // interest -> template
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')               // '' | 'generate' | 'clone' | 'create' | 'upload' | 'login'
  const [error, setError] = useState('')
  const [acctMode, setAcctMode] = useState('list')   // 'list' | 'cookies' | 'login'
  const [loginWaiting, setLoginWaiting] = useState('') // اليوزر الذي ننتظر اكتمال دخوله
  const pollRef = useRef(null)
  const fileRef = useRef(null)

  const set = (field, value) => setForm(f => ({ ...f, [field]: value }))
  const splitLines = (s) => (s || '').split('\n').map(x => x.trim()).filter(Boolean)
  const authHeaders = () => {
    const token = localStorage.getItem('token')
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  // ── تحميل الحسابات والاهتمامات عند الفتح ──
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const headers = authHeaders()
      const [accRes, agentsRes, tplRes] = await Promise.all([
        axios.get('/api/x/accounts', { headers, timeout: 8000 }).catch(() => ({ data: { accounts: [] } })),
        axios.get('/api/aeos/agents', { headers, timeout: 8000 }).catch(() => ({ data: { agents: [] } })),
        axios.get('/api/aeos/persona/templates', { headers, timeout: 8000 }).catch(() => ({ data: { interests: [], templates: [] } })),
      ])
      const withEmployee = new Set(
        (agentsRes.data?.agents || []).filter(a => a.employee_id).map(a => a.account_username)
      )
      setAccounts((accRes.data?.accounts || []).map(a => ({
        label: a.label,
        hasEmployee: withEmployee.has(a.label),
      })))
      setInterestsMeta(tplRes.data?.interests || [])
      const tplMap = {}
      ;(tplRes.data?.templates || []).forEach(t => { tplMap[t.interest] = t })
      setTemplates(tplMap)
    } catch {
      setError('تعذّر تحميل البيانات — تأكد من تشغيل الخادم.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      setStep(1)
      setForm(DEFAULT_FORM)
      setAcctMode('list')
      setLoginWaiting('')
      load()
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [open, load])

  // ── الخطوة 1: ربط الحساب (كوكيز / بيانات دخول) ──
  const uploadCookies = async (file) => {
    if (!file) return
    setBusy('upload')
    try {
      const fd = new FormData()
      fd.append('cookie_file', file)
      const res = await axios.post('/api/x/upload-cookies', fd, {
        headers: { ...authHeaders(), 'Content-Type': 'multipart/form-data' },
        timeout: 30000,
      })
      const label = res.data?.label
      if (label) {
        onNotify?.(`✅ تم حفظ كوكيز الحساب @${label} واختياره للموظف الجديد.`)
        await load()
        set('account', label)
        setAcctMode('list')
      }
    } catch (e) {
      onNotify?.(`❌ تعذّر رفع الكوكيز: ${e?.response?.data?.detail || 'تأكد أن الملف JSON صالح ويحتوي auth_token'}`)
    } finally {
      setBusy('')
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const loginWithCredentials = async () => {
    const username = (form.login_username || '').trim().replace(/^@/, '')
    if (!username || !(form.login_password || '').trim()) {
      onNotify?.('❗ أدخل اسم المستخدم وكلمة المرور.')
      return
    }
    setBusy('login')
    try {
      await axios.post('/api/x/login-credentials', {
        username,
        password: form.login_password,
        email: form.login_email || '',
      }, { headers: authHeaders(), timeout: 20000 })
      onNotify?.(`🔐 بدأ تسجيل دخول @${username} — قد يستغرق دقيقة أو دقيقتين. سيُختار الحساب تلقائياً عند اكتماله.`)
      setLoginWaiting(username)
      // راقب ظهور الحساب في القائمة (كل 5 ثوان، حتى 3 دقائق)
      let tries = 0
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        tries += 1
        try {
          const res = await axios.get('/api/x/accounts', { headers: authHeaders(), timeout: 8000 })
          const found = (res.data?.accounts || []).some(a => a.label === username)
          if (found) {
            clearInterval(pollRef.current)
            setLoginWaiting('')
            await load()
            set('account', username)
            setAcctMode('list')
            onNotify?.(`✅ تم تسجيل دخول @${username} وحفظ الكوكيز — الحساب جاهز.`)
          } else if (tries >= 36) {
            clearInterval(pollRef.current)
            setLoginWaiting('')
            onNotify?.(`⚠️ لم يكتمل دخول @${username} بعد — تابع الحالة من زر التحديث أو تحقق من خادم loginx.`)
          }
        } catch { /* تجاهل وأعد المحاولة */ }
      }, 5000)
    } catch (e) {
      onNotify?.(`❌ تعذّر بدء تسجيل الدخول: ${e?.response?.data?.detail || 'تأكد من تشغيل خادم loginx'}`)
    } finally {
      setBusy('')
    }
  }

  // ── الخطوة 2: تعبئة الشخصية ──
  const toggleInterest = (id) => {
    setForm(f => {
      const cur = new Set(f.interests)
      cur.has(id) ? cur.delete(id) : cur.add(id)
      return { ...f, interests: Array.from(cur) }
    })
  }

  const addCustomInterest = () => {
    const v = (form.customInterest || '').trim()
    if (!v) return
    setForm(f => ({
      ...f,
      interests: Array.from(new Set([...f.interests, v])),
      customInterest: '',
    }))
  }

  const applyPersona = (p) => {
    if (!p) return
    setForm(f => ({
      ...f,
      display_name: p.display_name || f.display_name || f.account,
      role: p.role || f.role,
      tone: p.tone || f.tone,
      language: p.language || f.language,
      dialect: p.dialect || f.dialect,
      bio: p.bio || f.bio,
      instructions: p.instructions || f.instructions,
      dos: (p.dos || []).join('\n') || f.dos,
      donts: (p.donts || []).join('\n') || f.donts,
      interests: (Array.isArray(p.interests) && p.interests.length) ? p.interests : f.interests,
    }))
  }

  const applyTemplate = (interestId) => {
    const tpl = templates[interestId]
    if (!tpl) return
    applyPersona({ ...tpl, interests: Array.from(new Set([...form.interests, interestId])) })
    onNotify?.(`تم تطبيق نموذج «${interestsMeta.find(i => i.id === interestId)?.label || interestId}». عدّل الحقول كما تريد.`)
  }

  const generateAI = async () => {
    setBusy('generate')
    try {
      const res = await axios.post('/api/aeos/persona/generate', {
        interests: form.interests,
        brief: form.brief || '',
        language: form.language || 'ar',
        account_username: form.account,
      }, { headers: authHeaders(), timeout: 60000 })
      applyPersona(res.data?.persona)
      onNotify?.(res.data?.source === 'ai'
        ? '✨ تم توليد الشخصية بالذكاء الاصطناعي — راجعها وعدّلها.'
        : 'تم بناء الشخصية من النماذج الجاهزة (الذكاء الاصطناعي غير متاح).')
    } catch {
      onNotify?.('❌ تعذّر توليد الشخصية.')
    } finally {
      setBusy('')
    }
  }

  const cloneStyle = async () => {
    const handle = (form.clone_handle || '').trim().replace(/^@/, '')
    if (!handle) { onNotify?.('❗ أدخل يوزر الحساب المراد استنساخه أولاً.'); return }
    setBusy('clone')
    onNotify?.(`🧬 جارِ تحليل أسلوب @${handle}... قد يستغرق دقيقة أو دقيقتين.`)
    try {
      const res = await axios.post('/api/aeos/persona/clone', {
        account_username: form.account,
        target_handle: handle,
        limit: 30,
        language: form.language || 'ar',
      }, { headers: authHeaders(), timeout: 300000 })
      applyPersona(res.data?.persona)
      const tgt = res.data?.target || {}
      onNotify?.(`🧬 تم استنساخ أسلوب @${handle} (عيّنة: ${tgt.tweet_count ?? '?'} تغريدة و${tgt.reply_count ?? '?'} رد). راجع الحقول.`)
    } catch (e) {
      onNotify?.(`❌ تعذّر استنساخ @${handle}: ${e?.response?.data?.detail || 'خطأ غير متوقع'}`)
    } finally {
      setBusy('')
    }
  }

  // ── الخطوة 4: إنشاء وتفعيل ──
  const clampInt = (v, def, min, max) => {
    const n = parseInt(v, 10)
    if (Number.isNaN(n)) return def
    return Math.max(min, Math.min(max, n))
  }

  const createAndActivate = async () => {
    setBusy('create')
    try {
      const persona = {
        display_name: form.display_name || form.account,
        role: form.role || 'Social Media Manager',
        tone: form.tone || 'professional',
        language: form.language || 'ar',
        dialect: form.dialect || '',
        bio: form.bio || '',
        instructions: form.instructions || '',
        dos: splitLines(form.dos),
        donts: splitLines(form.donts),
        interests: form.interests,
        values: [],
      }
      const goals = splitLines(form.goals).map(desc => ({ description: desc, priority: 3 }))
      const business_rules = {
        daily_post_target: clampInt(form.daily_post_target, 1, 0, 20),
        max_actions_per_cycle: clampInt(form.max_actions_per_cycle, 5, 1, 10),
        max_replies_per_cycle: clampInt(form.max_replies_per_cycle, 1, 0, 5),
        max_retweets_per_cycle: clampInt(form.max_retweets_per_cycle, 1, 0, 5),
        max_likes_per_cycle: clampInt(form.max_likes_per_cycle, 3, 0, 20),
        max_follows_per_cycle: clampInt(form.max_follows_per_cycle, 1, 0, 5),
        max_quotes_per_cycle: clampInt(form.max_quotes_per_cycle, 1, 0, 5),
        max_bookmarks_per_cycle: clampInt(form.max_bookmarks_per_cycle, 1, 0, 5),
        allow_threads: !!form.allow_threads,
        generate_images: ['auto', 'always', 'off'].includes(form.generate_images)
          ? form.generate_images : 'auto',
        image_style: form.image_style || 'editorial',
      }
      const res = await axios.post('/api/aeos/agent/toggle', {
        platform: 'x',
        account_username: form.account,
        cookie_label: form.account,
        enabled: true,
        interval_minutes: clampInt(form.interval_minutes, 60, 1, 1440),
        active_hours_start: clampInt(form.active_hours_start, 8, 0, 23),
        active_hours_end: clampInt(form.active_hours_end, 23, 0, 23),
        persona,
        goals,
        business_rules,
      }, { headers: authHeaders() })
      if (res.data?.ok) {
        onNotify?.(
          `🎉 تم إنشاء الموظف الذكي @${form.account} وتفعيله!\n` +
          `👤 ${persona.display_name} — ${persona.role}\n` +
          `🎯 الاهتمامات: ${form.interests.join('، ') || '—'}\n` +
          `⏰ يعمل كل ${form.interval_minutes} دقيقة بين ${form.active_hours_start}:00 و${form.active_hours_end}:00.\n` +
          `سيبدأ أول دورة قريباً ويوافيك بما رصده وفعله هنا في الشات.`
        )
        // مزامنة الهوية على ملف حساب X الفعلي (الاسم، النبذة، الموقع، الصور)
        if (form.sync_profile) {
          onNotify?.(`🪞 جارِ تحديث ملف الحساب @${form.account} على X ليطابق الهوية الجديدة... قد يستغرق دقيقة.`)
          try {
            await axios.post('/api/x/profile/update', {
              label: form.account,
              name: persona.display_name || undefined,
              bio: persona.bio || undefined,
              location: form.profile_location || undefined,
              website: form.profile_website || undefined,
              avatar_url: form.profile_avatar_url || undefined,
              banner_url: form.profile_banner_url || undefined,
            }, { headers: authHeaders(), timeout: 240000 })
            onNotify?.(`✅ تم تحديث هوية الحساب @${form.account} على X (الاسم/النبذة${form.profile_avatar_url ? '/الصورة' : ''}${form.profile_banner_url ? '/الغلاف' : ''}).`)
          } catch (e) {
            onNotify?.(`⚠️ الموظف أُنشئ لكن تعذّر تحديث ملف الحساب على X: ${e?.response?.data?.detail || 'خطأ غير متوقع'}`)
          }
        }
        onCreated?.()
        onClose?.()
      }
    } catch (e) {
      onNotify?.(`❌ تعذّر إنشاء الموظف: ${e?.response?.data?.detail || 'خطأ غير متوقع'}`)
    } finally {
      setBusy('')
    }
  }

  // ── صلاحية كل خطوة للانتقال ──
  const canNext = useMemo(() => {
    if (step === 1) return !!form.account
    if (step === 2) return form.interests.length > 0 && !!(form.display_name || form.account)
    if (step === 3) return true
    return true
  }, [step, form])

  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[60]" onClick={onClose} />
      <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 pointer-events-none">
        <div className="pointer-events-auto w-full max-w-2xl max-h-[92vh] flex flex-col rounded-2xl bg-card-light dark:bg-sidebar-dark border border-border-light dark:border-border-dark shadow-2xl overflow-hidden">

          {/* رأس المعالج */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-light dark:border-border-dark shrink-0">
            <div className="flex items-center gap-2">
              <MdSmartToy className="text-primary" size={22} />
              <h3 className="text-base font-bold text-text-primary-light dark:text-text-primary-dark">إنشاء موظف ذكي جديد</h3>
            </div>
            <button onClick={onClose} className="size-8 flex items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark">
              <MdClose size={20} />
            </button>
          </div>

          {/* شريط الخطوات */}
          <div className="flex items-center justify-between px-6 py-3 border-b border-border-light dark:border-border-dark bg-gray-50 dark:bg-card-dark/40 shrink-0">
            {STEPS.map((s, i) => {
              const Icon = s.icon
              const state = step === s.id ? 'current' : step > s.id ? 'done' : 'todo'
              return (
                <div key={s.id} className="flex items-center flex-1 last:flex-none">
                  <div className="flex flex-col items-center gap-1 min-w-[64px]">
                    <div className={`size-8 rounded-full flex items-center justify-center text-white transition-colors ${
                      state === 'done' ? 'bg-green-500' : state === 'current' ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'
                    }`}>
                      {state === 'done' ? <MdCheckCircle size={18} /> : <Icon size={16} />}
                    </div>
                    <span className={`text-[10px] font-medium ${state === 'current' ? 'text-primary' : 'text-text-secondary-light dark:text-text-secondary-dark'}`}>
                      {s.title}
                    </span>
                  </div>
                  {i < STEPS.length - 1 && (
                    <div className={`h-0.5 flex-1 mx-1 mb-4 rounded ${step > s.id ? 'bg-green-500' : 'bg-gray-200 dark:bg-gray-700'}`} />
                  )}
                </div>
              )
            })}
          </div>

          {/* محتوى الخطوة */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
              </div>
            ) : error ? (
              <div className="text-center text-sm text-red-500 py-10">{error}</div>
            ) : step === 1 ? (
              /* ───────── الخطوة 1: الحساب ───────── */
              <div className="space-y-3">
                <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark leading-relaxed">
                  اختر حساباً متصلاً، أو أضف حساباً جديداً برفع كوكيز أو ببيانات الدخول.
                </p>

                {/* طرق ربط الحساب */}
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { id: 'list', label: 'حساب متصل', Icon: MdPerson },
                    { id: 'cookies', label: 'رفع كوكيز', Icon: MdUploadFile },
                    { id: 'login', label: 'بيانات الدخول', Icon: MdKey },
                  ].map(({ id, label, Icon }) => (
                    <button key={id} onClick={() => setAcctMode(id)}
                      className={`flex items-center justify-center gap-1.5 text-xs px-2 py-2 rounded-xl border transition-colors ${
                        acctMode === id
                          ? 'border-primary bg-primary/10 text-primary font-bold'
                          : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark hover:border-primary/50'
                      }`}>
                      <Icon size={15} /> {label}
                    </button>
                  ))}
                </div>

                {acctMode === 'cookies' ? (
                  <div className="rounded-xl border border-dashed border-border-light dark:border-border-dark p-5 text-center space-y-2">
                    <MdUploadFile className="mx-auto text-primary" size={28} />
                    <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark">
                      ارفع ملف كوكيز الحساب (JSON يحتوي auth_token) — يُحفظ ويُختار تلقائياً.
                    </p>
                    <input ref={fileRef} type="file" accept=".json,application/json" className="hidden"
                      onChange={e => uploadCookies(e.target.files?.[0])} />
                    <button onClick={() => fileRef.current?.click()} disabled={busy === 'upload'}
                      className="text-xs px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50">
                      {busy === 'upload' ? 'جارِ الرفع…' : 'اختر ملف الكوكيز'}
                    </button>
                  </div>
                ) : acctMode === 'login' ? (
                  <div className="rounded-xl border border-border-light dark:border-border-dark p-4 space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className={labelCls}>اسم المستخدم</label>
                        <input value={form.login_username} onChange={e => set('login_username', e.target.value)}
                          placeholder="@username" className={inputCls} dir="ltr" />
                      </div>
                      <div>
                        <label className={labelCls}>كلمة المرور</label>
                        <input type="password" value={form.login_password} onChange={e => set('login_password', e.target.value)}
                          className={inputCls} dir="ltr" />
                      </div>
                    </div>
                    <div>
                      <label className={labelCls}>البريد (اختياري — للتحقق إن طُلب)</label>
                      <input value={form.login_email} onChange={e => set('login_email', e.target.value)}
                        placeholder="email@example.com" className={inputCls} dir="ltr" />
                    </div>
                    <button onClick={loginWithCredentials} disabled={busy === 'login' || !!loginWaiting}
                      className="w-full text-xs px-4 py-2 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50">
                      {loginWaiting ? `⏳ بانتظار اكتمال دخول @${loginWaiting}…` : busy === 'login' ? 'جارِ البدء…' : 'سجّل الدخول واحفظ الكوكيز'}
                    </button>
                    <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
                      يتم الدخول عبر متصفح آلي (loginx) وتُحفظ الكوكيز باسم الحساب — لا تُخزّن كلمة المرور.
                    </p>
                  </div>
                ) : accounts.length === 0 ? (
                  <div className="text-center text-sm text-text-secondary-light dark:text-text-secondary-dark py-8">
                    لا توجد حسابات متصلة — أضف حساباً من «رفع كوكيز» أو «بيانات الدخول» بالأعلى.
                  </div>
                ) : (
                  accounts.map(a => (
                    <button
                      key={a.label}
                      onClick={() => set('account', a.label)}
                      className={`w-full flex items-center justify-between px-4 py-3 rounded-xl border transition-colors text-right ${
                        form.account === a.label
                          ? 'border-primary bg-primary/10'
                          : 'border-border-light dark:border-border-dark hover:border-primary/50'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div className="size-9 rounded-full bg-primary/10 flex items-center justify-center">
                          <MdPerson className="text-primary" size={20} />
                        </div>
                        <span className="text-sm font-bold text-text-primary-light dark:text-text-primary-dark">@{a.label}</span>
                      </div>
                      {a.hasEmployee && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400">
                          لديه موظف
                        </span>
                      )}
                    </button>
                  ))
                )}
              </div>
            ) : step === 2 ? (
              /* ───────── الخطوة 2: الشخصية ───────── */
              <div className="space-y-4">
                {/* الاهتمامات أولاً — تحدد التخصص وكل الترشيح */}
                <div>
                  <label className={labelCls}>التخصص / الاهتمامات (إلزامي — يتفاعل فقط ضمنها)</label>
                  <div className="flex flex-wrap gap-2">
                    {interestsMeta.map(i => (
                      <button
                        key={i.id}
                        onClick={() => toggleInterest(i.id)}
                        className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                          form.interests.includes(i.id)
                            ? 'bg-primary text-white border-primary'
                            : 'border-border-light dark:border-border-dark text-text-primary-light dark:text-text-primary-dark hover:border-primary/50'
                        }`}
                      >
                        {i.label}
                      </button>
                    ))}
                    {form.interests.filter(x => !interestsMeta.some(i => i.id === x)).map(x => (
                      <button key={x} onClick={() => toggleInterest(x)}
                        className="text-xs px-3 py-1.5 rounded-full border bg-primary text-white border-primary">
                        {x} ✕
                      </button>
                    ))}
                  </div>
                  <div className="flex gap-2 mt-2">
                    <input
                      value={form.customInterest}
                      onChange={e => set('customInterest', e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && addCustomInterest()}
                      placeholder="اهتمام مخصص (مثال: العطور، العقارات...)"
                      className={inputCls}
                    />
                    <button onClick={addCustomInterest}
                      className="text-xs px-3 rounded-lg border border-primary/40 text-primary hover:bg-primary/10 shrink-0">
                      إضافة
                    </button>
                  </div>
                </div>

                {/* طرق بناء الشخصية */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div className="rounded-xl border border-border-light dark:border-border-dark p-3 space-y-2">
                    <p className="text-[11px] font-bold text-text-primary-light dark:text-text-primary-dark">📋 نموذج جاهز</p>
                    <select
                      onChange={e => e.target.value && applyTemplate(e.target.value)}
                      defaultValue=""
                      className={inputCls}
                    >
                      <option value="">اختر نموذجاً…</option>
                      {Object.keys(templates).map(k => (
                        <option key={k} value={k}>{templates[k].label || k}</option>
                      ))}
                    </select>
                  </div>
                  <div className="rounded-xl border border-border-light dark:border-border-dark p-3 space-y-2">
                    <p className="text-[11px] font-bold text-text-primary-light dark:text-text-primary-dark">✨ توليد بالذكاء الاصطناعي</p>
                    <input
                      value={form.brief}
                      onChange={e => set('brief', e.target.value)}
                      placeholder="وصف مختصر (اختياري)"
                      className={inputCls}
                    />
                    <button onClick={generateAI} disabled={busy === 'generate'}
                      className="w-full text-xs px-2 py-1.5 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50">
                      {busy === 'generate' ? 'جارِ التوليد…' : 'ولّد الشخصية'}
                    </button>
                  </div>
                  <div className="rounded-xl border border-border-light dark:border-border-dark p-3 space-y-2">
                    <p className="text-[11px] font-bold text-text-primary-light dark:text-text-primary-dark">🧬 استنساخ حساب</p>
                    <input
                      value={form.clone_handle}
                      onChange={e => set('clone_handle', e.target.value)}
                      placeholder="@اليوزر المستهدف"
                      className={inputCls}
                    />
                    <button onClick={cloneStyle} disabled={busy === 'clone'}
                      className="w-full text-xs px-2 py-1.5 rounded-lg border border-primary text-primary hover:bg-primary/10 disabled:opacity-50">
                      {busy === 'clone' ? 'جارِ التحليل…' : 'استنسخ الأسلوب'}
                    </button>
                  </div>
                </div>

                {/* الحقول القابلة للتعديل */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className={labelCls}>اسم العرض</label>
                    <input value={form.display_name} onChange={e => set('display_name', e.target.value)}
                      placeholder={form.account} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>الدور</label>
                    <input value={form.role} onChange={e => set('role', e.target.value)} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>النبرة</label>
                    <select value={form.tone} onChange={e => set('tone', e.target.value)} className={inputCls}>
                      {TONES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelCls}>اللهجة (اختياري)</label>
                    <input value={form.dialect} onChange={e => set('dialect', e.target.value)}
                      placeholder="سعودية، مصرية…" className={inputCls} />
                  </div>
                </div>
                <div>
                  <label className={labelCls}>نبذة (Bio)</label>
                  <textarea value={form.bio} onChange={e => set('bio', e.target.value)} rows={2} className={inputCls} />
                </div>
                <div>
                  <label className={labelCls}>تعليمات دائمة</label>
                  <textarea value={form.instructions} onChange={e => set('instructions', e.target.value)} rows={2} className={inputCls} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className={labelCls}>افعل (سطر لكل بند)</label>
                    <textarea value={form.dos} onChange={e => set('dos', e.target.value)} rows={3} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>لا تفعل (سطر لكل بند)</label>
                    <textarea value={form.donts} onChange={e => set('donts', e.target.value)} rows={3} className={inputCls} />
                  </div>
                </div>
              </div>
            ) : step === 3 ? (
              /* ───────── الخطوة 3: القواعد والجدولة ───────── */
              <div className="space-y-4">
                <div>
                  <label className={labelCls}>الأهداف (سطر لكل هدف — اختياري)</label>
                  <textarea value={form.goals} onChange={e => set('goals', e.target.value)} rows={3}
                    placeholder={'زيادة التفاعل مع جمهور التخصص\nبناء حضور يومي ثابت'} className={inputCls} />
                </div>
                <div>
                  <p className="text-[11px] font-bold text-text-primary-light dark:text-text-primary-dark mb-2">⏰ الجدولة</p>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className={labelCls}>دورة كل (دقيقة)</label>
                      <input type="number" min="1" value={form.interval_minutes}
                        onChange={e => set('interval_minutes', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>يبدأ الساعة</label>
                      <input type="number" min="0" max="23" value={form.active_hours_start}
                        onChange={e => set('active_hours_start', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>يتوقف الساعة</label>
                      <input type="number" min="0" max="23" value={form.active_hours_end}
                        onChange={e => set('active_hours_end', e.target.value)} className={inputCls} />
                    </div>
                  </div>
                </div>
                <div>
                  <p className="text-[11px] font-bold text-text-primary-light dark:text-text-primary-dark mb-2">🎚️ حدود التفاعل لكل دورة</p>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className={labelCls}>منشورات/يوم</label>
                      <input type="number" min="0" max="20" value={form.daily_post_target}
                        onChange={e => set('daily_post_target', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>إجمالي الإجراءات</label>
                      <input type="number" min="1" max="10" value={form.max_actions_per_cycle}
                        onChange={e => set('max_actions_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>ردود</label>
                      <input type="number" min="0" max="5" value={form.max_replies_per_cycle}
                        onChange={e => set('max_replies_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>إعادات نشر</label>
                      <input type="number" min="0" max="5" value={form.max_retweets_per_cycle}
                        onChange={e => set('max_retweets_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>إعجابات</label>
                      <input type="number" min="0" max="20" value={form.max_likes_per_cycle}
                        onChange={e => set('max_likes_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>متابعات</label>
                      <input type="number" min="0" max="5" value={form.max_follows_per_cycle}
                        onChange={e => set('max_follows_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>اقتباس مع تعليق</label>
                      <input type="number" min="0" max="5" value={form.max_quotes_per_cycle}
                        onChange={e => set('max_quotes_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                    <div>
                      <label className={labelCls}>حفظ مراجع</label>
                      <input type="number" min="0" max="5" value={form.max_bookmarks_per_cycle}
                        onChange={e => set('max_bookmarks_per_cycle', e.target.value)} className={inputCls} />
                    </div>
                  </div>
                  <p className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark mt-2 leading-relaxed">
                    الرقم المكتوب هو العدد الفعلي في الدورة الواحدة، و«إجمالي الإجراءات» هو الحد الكلي فوقها جميعاً. صفر = تعطيل النوع.
                  </p>
                  <div className="grid grid-cols-2 gap-2 mt-3">
                    <div>
                      <label className={labelCls}>الصور مع المنشورات</label>
                      <select value={form.generate_images}
                        onChange={e => set('generate_images', e.target.value)} className={inputCls}>
                        <option value="auto">تلقائي (عند غياب صورة حقيقية)</option>
                        <option value="always">دائماً (صورة مولّدة لكل منشور)</option>
                        <option value="off">معطّل</option>
                      </select>
                    </div>
                    <div>
                      <label className={labelCls}>النمط البصري</label>
                      <select value={form.image_style}
                        onChange={e => set('image_style', e.target.value)} className={inputCls}>
                        <option value="editorial">تحريري هادئ</option>
                        <option value="cinematic">سينمائي</option>
                        <option value="minimal">مسطّح بسيط</option>
                        <option value="infographic">تجريدي معلوماتي</option>
                        <option value="photoreal">فوتوغرافي واقعي</option>
                      </select>
                    </div>
                  </div>
                  <label className="flex items-center gap-2 text-[12px] mt-2 cursor-pointer text-text-primary-light dark:text-text-primary-dark">
                    <input type="checkbox" checked={!!form.allow_threads}
                      onChange={e => set('allow_threads', e.target.checked)} className="accent-primary" />
                    السماح بنشر الثريدات (سلاسل تغريدات تحليلية)
                  </label>
                </div>
              </div>
            ) : (
              /* ───────── الخطوة 4: مراجعة وتفعيل ───────── */
              <div className="space-y-3">
                <div className="rounded-xl border border-border-light dark:border-border-dark divide-y divide-border-light dark:divide-border-dark text-sm">
                  {[
                    ['الحساب', `@${form.account}`],
                    ['الاسم والدور', `${form.display_name || form.account} — ${form.role}`],
                    ['النبرة واللهجة', `${TONES.find(t => t.id === form.tone)?.label || form.tone}${form.dialect ? ' / ' + form.dialect : ''}`],
                    ['الاهتمامات', form.interests.map(x => interestsMeta.find(i => i.id === x)?.label || x).join('، ') || '—'],
                    ['الأهداف', splitLines(form.goals).length ? `${splitLines(form.goals).length} هدف` : '—'],
                    ['الجدولة', `كل ${form.interval_minutes} دقيقة، من ${form.active_hours_start}:00 إلى ${form.active_hours_end}:00`],
                    ['حدود الدورة', `${form.daily_post_target} منشور/يوم، ${form.max_actions_per_cycle} إجراءات، ${form.max_replies_per_cycle} رد، ${form.max_likes_per_cycle} إعجاب`],
                  ].map(([k, v]) => (
                    <div key={k} className="flex items-start justify-between gap-4 px-4 py-2.5">
                      <span className="text-[11px] font-medium text-text-secondary-light dark:text-text-secondary-dark shrink-0">{k}</span>
                      <span className="text-xs text-text-primary-light dark:text-text-primary-dark text-left">{v}</span>
                    </div>
                  ))}
                </div>
                {/* مزامنة الهوية على ملف حساب X الفعلي */}
                <div className="rounded-xl border border-border-light dark:border-border-dark p-4 space-y-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={form.sync_profile}
                      onChange={e => set('sync_profile', e.target.checked)}
                      className="size-4 accent-primary" />
                    <span className="flex items-center gap-1.5 text-xs font-bold text-text-primary-light dark:text-text-primary-dark">
                      <MdBadge size={16} className="text-primary" />
                      حدّث هوية الحساب على X لتطابق الشخصية (الاسم + النبذة + الصور)
                    </span>
                  </label>
                  {form.sync_profile && (
                    <div className="space-y-3">
                      <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
                        سيُطبّق الاسم «{form.display_name || form.account}» والنبذة المكتوبة في الخطوة 2 على ملف الحساب الفعلي، مع الحقول الاختيارية أدناه.
                      </p>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className={labelCls}>الموقع الجغرافي (اختياري)</label>
                          <input value={form.profile_location} onChange={e => set('profile_location', e.target.value)}
                            placeholder="الرياض، السعودية" className={inputCls} />
                        </div>
                        <div>
                          <label className={labelCls}>الموقع الإلكتروني (اختياري)</label>
                          <input value={form.profile_website} onChange={e => set('profile_website', e.target.value)}
                            placeholder="https://…" className={inputCls} dir="ltr" />
                        </div>
                        <div>
                          <label className={labelCls}>رابط الصورة الشخصية (اختياري)</label>
                          <input value={form.profile_avatar_url} onChange={e => set('profile_avatar_url', e.target.value)}
                            placeholder="https://….jpg" className={inputCls} dir="ltr" />
                        </div>
                        <div>
                          <label className={labelCls}>رابط صورة الغلاف (اختياري)</label>
                          <input value={form.profile_banner_url} onChange={e => set('profile_banner_url', e.target.value)}
                            placeholder="https://….jpg" className={inputCls} dir="ltr" />
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                <p className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark leading-relaxed">
                  عند التفعيل سيبدأ الموظف أول دورة: يقرأ التايم لاين والترندات والهاشتاقات وأهم البوستات،
                  يتفاعل ضمن تخصصه فقط، ويوافيك بكل دورة في الشات. يمكنك توجيهه لاحقاً («انشر عن …»)
                  أو تعديل كل شيء من درج الموظفين الذكيين.
                </p>
              </div>
            )}
          </div>

          {/* أزرار التنقل */}
          <div className="flex items-center justify-between px-5 py-4 border-t border-border-light dark:border-border-dark shrink-0">
            <button
              onClick={() => step > 1 ? setStep(step - 1) : onClose()}
              className="flex items-center gap-1 text-sm px-4 py-2 rounded-xl border border-border-light dark:border-border-dark text-text-primary-light dark:text-text-primary-dark hover:bg-gray-50 dark:hover:bg-card-dark"
            >
              <MdArrowForward size={16} />
              {step > 1 ? 'السابق' : 'إلغاء'}
            </button>
            {step < 4 ? (
              <button
                onClick={() => canNext && setStep(step + 1)}
                disabled={!canNext}
                className="flex items-center gap-1 text-sm px-5 py-2 rounded-xl bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                التالي
                <MdArrowBack size={16} />
              </button>
            ) : (
              <button
                onClick={createAndActivate}
                disabled={busy === 'create'}
                className="flex items-center gap-1.5 text-sm px-5 py-2 rounded-xl bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
              >
                <MdRocketLaunch size={16} />
                {busy === 'create' ? 'جارِ الإنشاء…' : 'إنشاء وتفعيل'}
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

export default EmployeeWizard
