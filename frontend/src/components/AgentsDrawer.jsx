import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import EmployeeWizard from './EmployeeWizard'
import ToolsPanel from './ToolsPanel'
import VoiceProfileView from './VoiceProfileView'
import MemoryPanel from './MemoryPanel'
import clonePersona from '../hooks/clonePersona'
import {
  MdSmartToy,
  MdClose,
  MdRefresh,
  MdPerson,
  MdPlayArrow,
  MdTune,
  MdExpandMore,
  MdSave,
  MdHistory,
  MdCheckCircle,
  MdError,
  MdDelete,
  MdAdd,
  MdChat,
  MdBadge,
  MdEdit,
} from 'react-icons/md'

// تسميات عربية للمهارات و MCPs
const SKILL_LABELS = {
  on_this_day: 'أحداث في مثل هذا اليوم (توثيق تاريخي)',
  daily_content_creator: 'إنشاء محتوى يومي',
  community_engager: 'التفاعل مع المجتمع',
  trend_watcher: 'مراقبة الترندات',
  timeline_engager: 'تصفّح وتفاعل مع التايم لاين (حسب الاهتمام)',
  viral_content_creator: 'صناعة محتوى فايرل عن الترند',
  content_quoter: 'اقتباس مع تعليق (بناء الموقف)',
  swipe_file: 'ملف الإلهام (حفظ المنشورات القوية)',
  directed_engager: 'تنفيذ توجيه مباشر من الشات',
}
const MCP_LABELS = {
  x: 'منصة X (نشر/تفاعل)',
  playwright_x: 'Playwright X (دخول بالكوكيز + تصفّح التايم لاين)',
  content: 'توليد المحتوى (LLM)',
  memory: 'الذاكرة',
  browser: 'المتصفح',
  news: 'الأخبار',
  search: 'البحث',
  image: 'توليد الصور (صور أصلية للمنشورات)',
}

// صيغ المحتوى المتاحة (مطابقة لمعرّفات strategy.CONTENT_FORMATS في الخادم)
const FORMAT_LABELS = {
  insight: 'زبدة/استنتاج',
  hot_take: 'رأي صريح',
  howto: 'شرح عملي',
  question: 'سؤال يفتح نقاشاً',
  story: 'قصة/موقف',
  myth_bust: 'تصحيح مفهوم',
  thread: 'ثريد تحليلي (٤ تغريدات)',
  listicle: 'ثريد قائمة (٥ تغريدات)',
}
const IMAGE_STYLE_LABELS = {
  editorial: 'تحريري هادئ',
  cinematic: 'سينمائي',
  minimal: 'مسطّح بسيط',
  infographic: 'تجريدي معلوماتي',
  photoreal: 'فوتوغرافي واقعي',
}
const IMAGE_POLICY_LABELS = {
  auto: 'تلقائي (عند غياب صورة حقيقية)',
  always: 'دائماً (صورة مولّدة لكل منشور)',
  off: 'معطّل',
}
// الاهتمامات الرئيسية (fallback إن تعذّر جلبها من الخادم)
const INTEREST_LABELS = {
  politics: 'سياسة',
  economy: 'اقتصاد',
  sports: 'رياضة',
  entertainment: 'ترفيه',
  art: 'فن',
}

/**
 * درج "الموظفين الذكيين" (AEOS).
 *
 * فعّل الحساب ليعمل الموظف الذكي تلقائياً بالجدولة — يفكّر ويخطّط وينفّذ بنفسه،
 * دون أوامر مباشرة. يمكن تشغيل عدة موظفين معاً، ويتوقفون خارج ساعات العمل.
 *
 * Props:
 *  - open: boolean
 *  - onClose: () => void
 *  - onNotify: (text) => void   // لإظهار رسالة في الشات
 */
const AgentsDrawer = ({ open, onClose, onNotify }) => {
  const [rows, setRows] = useState([])          // [{label, agent}]
  const [summary, setSummary] = useState({ running: 0, total: 0 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState({})          // label -> bool
  const [expanded, setExpanded] = useState({})  // label -> bool (persona editor)
  const [pForm, setPForm] = useState({})        // label -> persona/goals form
  const [savingP, setSavingP] = useState({})    // label -> bool
  const [catalog, setCatalog] = useState({ skills: [], mcp_servers: [] })
  const [personaMeta, setPersonaMeta] = useState({ interests: [], templates: {} })
  const [genBusy, setGenBusy] = useState({})   // label -> bool (AI generate)
  const [cloneBusy, setCloneBusy] = useState({}) // label -> bool (استنساخ حساب)
  const [logsOpen, setLogsOpen] = useState({}) // label -> bool (cycle logs panel)
  const [logsData, setLogsData] = useState({}) // label -> [logs]
  const [logsBusy, setLogsBusy] = useState({}) // label -> bool
  const [delBusy, setDelBusy] = useState({})   // label -> bool (delete)
  const [insightBusy, setInsightBusy] = useState({}) // "label:kind" -> bool
  const [wizardOpen, setWizardOpen] = useState(false) // معالج إنشاء موظف جديد
  const [identBusy, setIdentBusy] = useState({})     // label -> 'preview' | 'apply' | 'field:<field>'
  const [identPreview, setIdentPreview] = useState({}) // label -> {name, bio, avatar_url, banner_url}
  const [identEdit, setIdentEdit] = useState({})       // label -> {field, prompt} (تعديل عنصر منفرد)
  const [skillPrompt, setSkillPrompt] = useState({})   // label -> نص برومبت المهارة
  const [skillDraft, setSkillDraft] = useState({})     // label -> {skill, summary} (معاينة قبل التفعيل)
  const [skillBusy, setSkillBusy] = useState({})       // label -> 'gen' | 'save' | 'id:<skill>'
  const [customSkills, setCustomSkills] = useState({}) // label -> [مهارات مخصّصة]
  const [skillInput, setSkillInput] = useState({})
  const [skillError, setSkillError] = useState({})
  const rowsRef = useRef(rows)
  const skillLocks = useRef({})
  const refreshSkillsRef = useRef(null)
  rowsRef.current = rows

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const token = localStorage.getItem('token')
      const headers = token ? { Authorization: `Bearer ${token}` } : {}
      const [accRes, agentsRes, catRes, tplRes] = await Promise.all([
        axios.get('/api/x/accounts', { headers, timeout: 8000 }).catch(() => ({ data: { accounts: [] } })),
        axios.get('/api/aeos/agents', { headers, timeout: 8000 }).catch(() => ({ data: { agents: [] } })),
        axios.get('/api/aeos/catalog', { headers, timeout: 8000 }).catch(() => ({ data: { skills: [], mcp_servers: [] } })),
        axios.get('/api/aeos/persona/templates', { headers, timeout: 8000 }).catch(() => ({ data: { interests: [], templates: [] } })),
      ])
      const accounts = accRes.data?.accounts || []
      const agents = agentsRes.data?.agents || []
      setCatalog({
        skills: catRes.data?.skills || [],
        mcp_servers: (catRes.data?.mcp_servers || []).map(m => (typeof m === 'string' ? m : m.name)),
      })
      const tplMap = {}
      ;(tplRes.data?.templates || []).forEach(t => { tplMap[t.interest] = t })
      setPersonaMeta({
        interests: tplRes.data?.interests || [],
        templates: tplMap,
      })
      const byLabel = {}
      agents.forEach(a => { byLabel[a.account_username] = a })

      const labels = new Set(accounts.map(a => a.label))
      agents.forEach(a => labels.add(a.account_username))

      const nextRows = Array.from(labels).map(label => ({ label, agent: byLabel[label] || null }))
      rowsRef.current = nextRows
      setRows(nextRows)
      setSummary({ running: agentsRes.data?.running || 0, total: agentsRes.data?.total || labels.size })
    } catch (e) {
      setError('تعذّر تحميل الموظفين')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const updateField = (label, field, value) => {
    setRows(prev => prev.map(r =>
      r.label === label
        ? { ...r, agent: { ...(r.agent || {}), [field]: value } }
        : r
    ))
  }

  const toggle = async (label, enabled) => {
    const row = rows.find(r => r.label === label)
    const a = row?.agent || {}
    const interval = parseInt(a.interval_minutes, 10) || 60
    const hs = a.active_hours_start ?? 8
    const he = a.active_hours_end ?? 23
    setBusy(prev => ({ ...prev, [label]: true }))
    try {
      const token = localStorage.getItem('token')
      const res = await axios.post('/api/aeos/agent/toggle', {
        platform: 'x',
        account_username: label,
        cookie_label: label,
        enabled,
        interval_minutes: interval,
        active_hours_start: Number(hs),
        active_hours_end: Number(he),
      }, { headers: token ? { Authorization: `Bearer ${token}` } : {} })

      if (res.data?.ok) {
        onNotify?.(
          enabled
            ? `✅ تم تفعيل الموظف الذكي للحساب @${label}. سيعمل تلقائياً كل ${interval} دقيقة بين الساعة ${hs}:00 و ${he}:00 — بدون أوامر منك.`
            : `⏸️ تم إيقاف الموظف الذكي للحساب @${label}.`
        )
        await load()
      }
    } catch (e) {
      onNotify?.(`❌ تعذّر تحديث حالة @${label}`)
    } finally {
      setBusy(prev => ({ ...prev, [label]: false }))
    }
  }

  // ── حذف موظف ذكي و/أو حساب مرتبط ──
  const deleteRow = async (label, agent, alsoAccount) => {
    const hasEmployee = !!agent?.employee_id
    const confirmMsg = alsoAccount
      ? `سيتم حذف الموظف الذكي والحساب @${label} نهائياً (بما في ذلك ملف الكوكيز والسجلّات). هل أنت متأكد؟`
      : `سيتم حذف الموظف الذكي للحساب @${label} نهائياً (مع سجلّات الدورات). هل أنت متأكد؟`
    if (!window.confirm(confirmMsg)) return

    setDelBusy(prev => ({ ...prev, [label]: true }))
    try {
      const token = localStorage.getItem('token')
      const headers = token ? { Authorization: `Bearer ${token}` } : {}
      // 1) احذف الموظف الذكي إن وُجد
      if (hasEmployee) {
        await axios.delete(`/api/aeos/employees/${agent.employee_id}`, { headers })
      }
      // 2) احذف الحساب المرتبط (ملف الكوكيز) عند الطلب
      if (alsoAccount) {
        await axios.delete(`/api/x/accounts/${encodeURIComponent(label)}`, { headers })
      }
      onNotify?.(
        alsoAccount
          ? `🗑️ تم حذف الموظف الذكي والحساب @${label} نهائياً.`
          : `🗑️ تم حذف الموظف الذكي للحساب @${label}.`
      )
      await load()
    } catch (e) {
      onNotify?.(`❌ تعذّر حذف @${label}: ${e?.response?.data?.detail || 'خطأ غير متوقع'}`)
    } finally {
      setDelBusy(prev => ({ ...prev, [label]: false }))
    }
  }

  // ── محرّر البرسونا والتعليمات ──
  const toggleExpand = (label, agent) => {
    setExpanded(prev => {
      const willOpen = !prev[label]
      // المهارات المخصّصة تُقرأ من نقطة مستقلة (ليست ضمن agent view)
      if (willOpen && agent?.employee_id) loadCustomSkills(label)
      if (willOpen && !pForm[label]) {
        const p = agent?.persona || {}
        const br = agent?.business_rules || {}
        setPForm(pf => ({
          ...pf,
          [label]: {
            display_name: p.display_name || label,
            role: p.role || 'Social Media Manager',
            tone: p.tone || 'professional',
            language: p.language || 'ar',
            dialect: p.dialect || '',
            values: p.values || [],
            bio: p.bio || '',
            instructions: p.instructions || '',
            dos: (p.dos || []).join('\n'),
            donts: (p.donts || []).join('\n'),
            interests: p.interests || [],
            voice_profile: br.voice_profile || null,
            voice_pending: null,
            brief: '',
            goals: (agent?.goals || []).map(g => g.description).join('\n'),
            skills: agent?.installed_skills || ['daily_content_creator', 'community_engager'],
            mcps: agent?.mcp_servers || ['x', 'content', 'memory'],
            // ── سقوف الدورة الواحدة ──
            max_actions_per_cycle: br.max_actions_per_cycle ?? 5,
            max_posts_per_cycle: br.max_posts_per_cycle ?? 1,
            max_replies_per_cycle: br.max_replies_per_cycle ?? 1,
            max_retweets_per_cycle: br.max_retweets_per_cycle ?? 1,
            max_likes_per_cycle: br.max_likes_per_cycle ?? 3,
            max_follows_per_cycle: br.max_follows_per_cycle ?? 1,
            max_quotes_per_cycle: br.max_quotes_per_cycle ?? 1,
            max_bookmarks_per_cycle: br.max_bookmarks_per_cycle ?? 1,
            // ── الميزانيات اليومية ──
            daily_post_target: br.daily_post_target ?? 1,
            max_posts_per_day: br.max_posts_per_day ?? 3,
            daily_thread_target: br.daily_thread_target ?? 1,
            daily_replies_target: br.daily_replies_target ?? 6,
            daily_retweets_target: br.daily_retweets_target ?? 4,
            daily_likes_target: br.daily_likes_target ?? 12,
            daily_follows_target: br.daily_follows_target ?? 3,
            daily_quotes_target: br.daily_quotes_target ?? 2,
            daily_bookmarks_target: br.daily_bookmarks_target ?? 4,
            // ── استراتيجية المحتوى ──
            content_strategy_enabled: br.content_strategy_enabled ?? true,
            allow_threads: br.allow_threads ?? true,
            content_formats: br.content_formats || Object.keys(FORMAT_LABELS),
            content_pillars: (br.content_pillars || []).join('\n'),
            content_series: br.content_series || '',
            content_series_every_posts: br.content_series_every_posts ?? 3,
            strategy_exploration: br.strategy_exploration ?? 0.35,
            // ── الصور ──
            generate_images: br.generate_images || 'auto',
            image_style: br.image_style || 'editorial',
            max_generated_images_per_day: br.max_generated_images_per_day ?? 3,
            image_prompt_extra: br.image_prompt_extra || '',
            // ── الجودة والتعلّم ──
            quality_gate: br.quality_gate ?? true,
            min_post_chars: br.min_post_chars ?? 70,
            min_relevance_score: br.min_relevance_score ?? 1,
            enforce_interest_scope: br.enforce_interest_scope ?? true,
            require_grounded_topic: br.require_grounded_topic ?? true,
            llm_decision_review: br.llm_decision_review ?? true,
            metrics_read_every_minutes: br.metrics_read_every_minutes ?? 180,
            observation_cache_minutes: br.observation_cache_minutes ?? 60,
            goal_weight: br.goal_weight ?? 0.25,
          },
        }))
      }
      return { ...prev, [label]: willOpen }
    })
  }

  const setP = (label, field, value) => {
    setPForm(pf => ({ ...pf, [label]: { ...(pf[label] || {}), [field]: value } }))
  }

  // ── حقول إعدادات مصغّرة (تُستخدم في لوحة الحدود والمحتوى) ──
  const inputCls = 'w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark'
  const capCls = 'block text-[10px] text-text-secondary-light dark:text-text-secondary-dark mb-1'

  const numField = (acct, field, caption, def, min, max, step) => (
    <div key={field}>
      <label className={capCls}>{caption}</label>
      <input type="number" min={min} max={max} step={step || 1}
        value={pForm[acct]?.[field] ?? def}
        onChange={(e) => setP(acct, field, e.target.value)}
        className={inputCls} />
    </div>
  )

  const chkField = (acct, field, caption, def = true) => (
    <label key={field} className="flex items-center gap-2 text-[11px] text-text-primary-light dark:text-text-primary-dark cursor-pointer">
      <input type="checkbox" checked={pForm[acct]?.[field] ?? def}
        onChange={(e) => setP(acct, field, e.target.checked)}
        className="accent-primary" />
      {caption}
    </label>
  )

  const selField = (acct, field, caption, options, def) => (
    <div key={field}>
      <label className={capCls}>{caption}</label>
      <select value={pForm[acct]?.[field] ?? def}
        onChange={(e) => setP(acct, field, e.target.value)}
        className={inputCls}>
        {Object.entries(options).map(([v, t]) => <option key={v} value={v}>{t}</option>)}
      </select>
    </div>
  )

  const settingsGroup = (title, hint, children) => (
    <div className="rounded-lg border border-border-light dark:border-border-dark p-2.5 space-y-2 bg-gray-50 dark:bg-card-dark/40">
      <label className="block text-[11px] font-medium text-text-primary-light dark:text-text-primary-dark">{title}</label>
      {hint && (
        <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark leading-relaxed">{hint}</p>
      )}
      {children}
    </div>
  )

  const splitLines = (s) => (s || '').split('\n').map(x => x.trim()).filter(Boolean)

  const toggleInList = (label, field, value) => {
    setPForm(pf => {
      const cur = new Set((pf[label]?.[field]) || [])
      cur.has(value) ? cur.delete(value) : cur.add(value)
      return { ...pf, [label]: { ...(pf[label] || {}), [field]: Array.from(cur) } }
    })
  }

  // طبّق نموذج اهتمام جاهز على حقول البرسونا
  const applyTemplate = (label, interestId) => {
    const tpl = personaMeta.templates?.[interestId]
    if (!tpl) return
    setPForm(pf => {
      const cur = pf[label] || {}
      const interests = Array.from(new Set([...(cur.interests || []), interestId]))
      return {
        ...pf,
        [label]: {
          ...cur,
          role: tpl.role || cur.role,
          tone: tpl.tone || cur.tone,
          language: tpl.language || cur.language,
          bio: tpl.bio || cur.bio,
          instructions: tpl.instructions || cur.instructions,
          dos: (tpl.dos || []).join('\n'),
          donts: (tpl.donts || []).join('\n'),
          interests,
        },
      }
    })
    onNotify?.(`تم تطبيق نموذج "${INTEREST_LABELS[interestId] || interestId}". يمكنك تعديله ثم الحفظ.`)
  }

  // ولّد برسونا كاملة عبر الذكاء الاصطناعي بناءً على الاهتمامات والوصف
  const generateAI = async (label) => {
    const f = pForm[label] || {}
    setGenBusy(prev => ({ ...prev, [label]: true }))
    try {
      const token = localStorage.getItem('token')
      const res = await axios.post('/api/aeos/persona/generate', {
        interests: f.interests || [],
        brief: f.brief || '',
        language: f.language || 'ar',
        account_username: label,
      }, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      const p = res.data?.persona
      if (p) {
        setP(label, 'display_name', p.display_name || f.display_name || label)
        setP(label, 'role', p.role || f.role)
        setP(label, 'tone', p.tone || f.tone)
        setP(label, 'language', p.language || f.language)
        setP(label, 'bio', p.bio || '')
        setP(label, 'instructions', p.instructions || '')
        setP(label, 'dos', (p.dos || []).join('\n'))
        setP(label, 'donts', (p.donts || []).join('\n'))
        if (Array.isArray(p.interests) && p.interests.length) setP(label, 'interests', p.interests)
        onNotify?.(res.data?.source === 'ai'
          ? `✨ تم توليد البرسونا بالذكاء الاصطناعي لـ @${label}. راجعها ثم احفظ.`
          : `تم إنشاء برسونا من النماذج الجاهزة لـ @${label} (الذكاء الاصطناعي غير متاح).`)
      }
    } catch (e) {
      onNotify?.(`❌ تعذّر توليد البرسونا لـ @${label}`)
    } finally {
      setGenBusy(prev => ({ ...prev, [label]: false }))
    }
  }

  // استنساخ أسلوب حساب X: يسحب عيّنة من تغريداته وردوده ثم يبني برسونا مطابقة
  const cloneStyle = async (label) => {
    const f = pForm[label] || {}
    const handle = (f.clone_handle || '').trim().replace(/^@/, '')
    if (!handle) {
      onNotify?.('❗ أدخل يوزر الحساب المراد استنساخه أولاً (مثال: @user)')
      return
    }
    setCloneBusy(prev => ({ ...prev, [label]: true }))
    const row = rows.find(r => r.label === label)
    const running = !!row?.agent?.enabled
    onNotify?.(
      `🧬 جارِ استنساخ هوية @${handle} بعمق: مئات التغريدات والردود مع ما ردّ عليه، موزّعة على أشهر لا آخر أسبوعين، ثم تحليل الهوية وأنواع الردود واختبار مطابقة — عادةً من 3 إلى 5 دقائق.`
      + (running ? ' ⚠️ الموظف يعمل الآن وقد يحتجز المتصفح؛ إن تعذّرت العملية فأوقفه مؤقتاً ثم أعد المحاولة.' : '')
    )
    try {
      const token = localStorage.getItem('token')
      const data = await clonePersona({
        account_username: label,
        target_handle: handle,
        language: f.language || 'ar',
      }, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        onProgress: (msg) => onNotify?.(`🧬 ${msg}`),
      })
      const res = { data }
      const p = res.data?.persona
      const tgt = res.data?.target || {}
      if (p) {
        // الاسم والنبذة لا يُنقلان من الحساب الأصلي (هوية لا انتحال): نُبقي الحاليين
        setP(label, 'role', p.role || f.role)
        setP(label, 'tone', p.tone || f.tone)
        setP(label, 'language', p.language || f.language)
        if (p.dialect) setP(label, 'dialect', p.dialect)
        if (Array.isArray(p.values) && p.values.length) setP(label, 'values', p.values)
        setP(label, 'instructions', p.instructions || '')
        setP(label, 'dos', (p.dos || []).join('\n'))
        setP(label, 'donts', (p.donts || []).join('\n'))
        if (Array.isArray(p.interests) && p.interests.length) setP(label, 'interests', p.interests)
        setP(label, 'voice_pending', res.data?.voice_profile || null)
        setP(label, 'clone_fidelity', res.data?.fidelity || null)
        const score = res.data?.fidelity?.score
        const cov = tgt.coverage || {}
        onNotify?.(
          `🧬 تم استنساخ هوية @${handle}: ${tgt.tweet_count ?? '?'} منشوراً أصلياً (بلا ردود)` +
          (cov.from ? ` من ${cov.from} إلى ${cov.to} على ${cov.days} يوماً` : '') +
          (tgt.scrape_seconds ? ` · السحب ${tgt.scrape_seconds} ثانية` : '') +
          (res.data?.source === 'ai' ? '' : ' — تحليل إحصائي فقط (LLM غير متاح)') +
          (score != null ? ` · درجة المطابقة ${score}%` : '') +
          '. راجع بصمة الصوت والعيّنات ثم اضغط حفظ.'
        )
      }
    } catch (e) {
      // السبب الحقيقي يأتي من الخادم (متصفح مشغول/حساب محمي/كوكيز منتهية)
      const detail = e?.response?.data?.detail
      const msg = detail || (e?.code === 'ECONNABORTED'
        ? 'استغرقت العملية وقتاً أطول من المتوقّع. أوقف الموظف مؤقتاً (فهو يحتجز المتصفح) ثم أعد المحاولة.'
        : 'خطأ غير متوقع')
      onNotify?.(`❌ تعذّر استنساخ @${handle}: ${msg}`)
    } finally {
      setCloneBusy(prev => ({ ...prev, [label]: false }))
    }
  }

  const savePersona = async (label) => {
    const f = pForm[label] || {}
    const row = rows.find(r => r.label === label)
    const a = row?.agent || {}
    const key = skillKeyOf(label, a.employee_id)
    if (skillLocks.current[key] || savingP[label]) return
    setSkillDraft(prev => ({ ...prev, [key]: null }))
    setSavingP(prev => ({ ...prev, [label]: true }))
    try {
      const token = localStorage.getItem('token')
      const persona = {
        display_name: f.display_name || label,
        role: f.role || 'Social Media Manager',
        tone: f.tone || 'professional',
        language: f.language || 'ar',
        // كانت اللهجة والقيم لا تُرسلان فيمسحهما كل حفظ من البطاقة (Persona.from_dict
        // يملأ الغائب بقيمة فارغة) — حتى لهجة الاستنساخ كانت تضيع عند أول حفظ
        dialect: f.dialect || '',
        values: f.values || [],
        bio: f.bio || '',
        instructions: f.instructions || '',
        dos: splitLines(f.dos),
        donts: splitLines(f.donts),
        interests: f.interests || [],
      }
      const goals = splitLines(f.goals).map(desc => ({ description: desc, priority: 3 }))
      const clampInt = (v, def, min, max) => {
        const n = parseInt(v, 10)
        if (Number.isNaN(n)) return def
        return Math.max(min, Math.min(max, n))
      }
      const clampFloat = (v, def, min, max) => {
        const n = parseFloat(v)
        if (Number.isNaN(n)) return def
        return Math.max(min, Math.min(max, n))
      }
      const business_rules = {
        // سقوف الدورة الواحدة
        max_actions_per_cycle: clampInt(f.max_actions_per_cycle, 5, 1, 20),
        max_posts_per_cycle: clampInt(f.max_posts_per_cycle, 1, 0, 3),
        max_quotes_per_cycle: clampInt(f.max_quotes_per_cycle, 1, 0, 5),
        max_bookmarks_per_cycle: clampInt(f.max_bookmarks_per_cycle, 1, 0, 5),
        // الميزانيات اليومية
        max_posts_per_day: clampInt(f.max_posts_per_day, 3, 0, 30),
        daily_thread_target: clampInt(f.daily_thread_target, 1, 0, 10),
        daily_replies_target: clampInt(f.daily_replies_target, 6, 0, 60),
        daily_retweets_target: clampInt(f.daily_retweets_target, 4, 0, 40),
        daily_likes_target: clampInt(f.daily_likes_target, 12, 0, 100),
        daily_follows_target: clampInt(f.daily_follows_target, 3, 0, 30),
        daily_quotes_target: clampInt(f.daily_quotes_target, 2, 0, 20),
        daily_bookmarks_target: clampInt(f.daily_bookmarks_target, 4, 0, 40),
        // استراتيجية المحتوى
        content_strategy_enabled: !!f.content_strategy_enabled,
        allow_threads: !!f.allow_threads,
        content_formats: (f.content_formats || []).filter(x => FORMAT_LABELS[x]),
        content_pillars: splitLines(f.content_pillars),
        content_series: (f.content_series || '').trim(),
        content_series_every_posts: clampInt(f.content_series_every_posts, 3, 1, 20),
        strategy_exploration: clampFloat(f.strategy_exploration, 0.35, 0, 1),
        // الصور
        generate_images: IMAGE_POLICY_LABELS[f.generate_images] ? f.generate_images : 'auto',
        image_style: IMAGE_STYLE_LABELS[f.image_style] ? f.image_style : 'editorial',
        max_generated_images_per_day: clampInt(f.max_generated_images_per_day, 3, 0, 20),
        image_prompt_extra: (f.image_prompt_extra || '').trim().slice(0, 200),
        // الجودة والتعلّم
        quality_gate: !!f.quality_gate,
        min_post_chars: clampInt(f.min_post_chars, 70, 20, 260),
        min_relevance_score: clampInt(f.min_relevance_score, 1, 0, 5),
        enforce_interest_scope: !!f.enforce_interest_scope,
        require_grounded_topic: !!f.require_grounded_topic,
        llm_decision_review: !!f.llm_decision_review,
        metrics_read_every_minutes: clampInt(f.metrics_read_every_minutes, 180, 0, 1440),
        observation_cache_minutes: clampInt(f.observation_cache_minutes, 60, 0, 720),
        goal_weight: clampFloat(f.goal_weight, 0.25, 0, 1),
        // سقوف التفاعل الأساسية
        max_replies_per_cycle: clampInt(f.max_replies_per_cycle, 1, 0, 10),
        max_retweets_per_cycle: clampInt(f.max_retweets_per_cycle, 1, 0, 10),
        max_likes_per_cycle: clampInt(f.max_likes_per_cycle, 3, 0, 20),
        max_follows_per_cycle: clampInt(f.max_follows_per_cycle, 1, 0, 5),
        daily_post_target: clampInt(f.daily_post_target, 1, 0, 20),
        // بصمة صوت جديدة من استنساخ لم يُحفظ بعد (business_rules تُدمج فلا تمسح القديمة)
        ...(f.voice_pending ? { voice_profile: f.voice_pending } : {}),
      }
      const res = await axios.post('/api/aeos/agent/toggle', {
        platform: 'x',
        account_username: label,
        cookie_label: label,
        enabled: a.enabled || false,
        interval_minutes: parseInt(a.interval_minutes, 10) || 60,
        active_hours_start: Number(a.active_hours_start ?? 8),
        active_hours_end: Number(a.active_hours_end ?? 23),
        persona,
        goals,
        business_rules,
        installed_skills: f.skills || [],
        // mcp_servers لا يُرسَل: الأدوات تُدار فوراً من ToolsPanel، وقائمة قديمة من
        // النموذج كانت ستفصل بصمت خادماً رُبط من اللوحة بعد فتح المحرر.
      }, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      if (res.data?.ok) {
        onNotify?.(`🧠 تم حفظ برسونا وتعليمات الموظف @${label}. سيلتزم بها في كل دورة قادمة.`)
        await load()
      }
    } catch (e) {
      onNotify?.(`❌ تعذّر حفظ التعليمات لـ @${label}`)
    } finally {
      setSavingP(prev => ({ ...prev, [label]: false }))
    }
  }

  const runNow = async (label) => {
    const row = rows.find(r => r.label === label)
    const empId = row?.agent?.employee_id
    if (!empId) { onNotify?.('فعّل الموظف أولاً.'); return }
    try {
      const token = localStorage.getItem('token')
      // force_fresh: الدورة اليدوية مقصودة — امسح كل المصادر من جديد بدل
      // إعادة استخدام ذاكرة الرصد (TTL ساعة)؛ بدونها تبدو الدورة اليدوية
      // «بلا أدوات» لأنها تقفز مباشرة للتحليل بلقطة قديمة.
      await axios.post(`/api/aeos/employees/${empId}/trigger`,
        { event_type: 'manual', payload: { force_fresh: true }, sync: false },
        { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      onNotify?.(`🚀 تم إيقاظ الموظف @${label} لتنفيذ دورة الآن.`)
    } catch (e) {
      onNotify?.(`❌ تعذّر تشغيل @${label}`)
    }
  }

  // ── هوية X من الشخصية: توليد معاينة (اسم/بايو/صورة/هيدر) ثم تطبيقها ──
  const previewIdentity = async (label) => {
    const row = rows.find(r => r.label === label)
    const empId = row?.agent?.employee_id
    if (!empId) { onNotify?.('فعّل الموظف أولاً.'); return }
    setIdentBusy(prev => ({ ...prev, [label]: 'preview' }))
    try {
      const token = localStorage.getItem('token')
      // الخادم يمهل توليد الصور 300ث (IMAGE_API_TIMEOUT) — مهلة الواجهة يجب أن تتجاوزها
      const res = await axios.post(`/api/aeos/employees/${empId}/identity/preview`, {},
        { headers: token ? { Authorization: `Bearer ${token}` } : {}, timeout: 330000 })
      const ident = res.data?.identity
      if (ident) {
        setIdentPreview(prev => ({ ...prev, [label]: ident }))
      } else {
        onNotify?.(`❌ لم ترجع هوية للمعاينة لـ @${label}`)
      }
    } catch (e) {
      const detail = e?.response?.data?.detail
      onNotify?.(`❌ تعذّر توليد هوية لـ @${label}: ${detail || (e?.code === 'ECONNABORTED' ? 'استغرق التوليد وقتاً أطول من المتوقّع — أعد المحاولة' : 'خطأ غير متوقع')}`)
    } finally {
      setIdentBusy(prev => ({ ...prev, [label]: null }))
    }
  }

  const applyIdentity = async (label) => {
    const row = rows.find(r => r.label === label)
    const empId = row?.agent?.employee_id
    const ident = identPreview[label]
    if (!empId || !ident) return
    setIdentBusy(prev => ({ ...prev, [label]: 'apply' }))
    try {
      const token = localStorage.getItem('token')
      const res = await axios.post(`/api/aeos/employees/${empId}/identity/apply`,
        { identity: ident },
        { headers: token ? { Authorization: `Bearer ${token}` } : {}, timeout: 15000 })
      onNotify?.(res.data?.message || `🪩 جارِ تطبيق الهوية على @${label}… ستصلك النتيجة في الشات.`)
      setIdentPreview(prev => ({ ...prev, [label]: null }))
    } catch (e) {
      onNotify?.(`❌ تعذّر تطبيق الهوية على @${label}: ${e?.response?.data?.detail || 'خطأ غير متوقع'}`)
    } finally {
      setIdentBusy(prev => ({ ...prev, [label]: null }))
    }
  }

  // ── تعديل عنصر واحد من الهوية (صورة/هيدر/اسم/بايو) ببرومبت اختياري ──
  const regenerateField = async (label, field) => {
    const row = rows.find(r => r.label === label)
    const empId = row?.agent?.employee_id
    if (!empId) return
    const prompt = (identEdit[label]?.prompt || '').trim()
    setIdentBusy(prev => ({ ...prev, [label]: `field:${field}` }))
    try {
      const token = localStorage.getItem('token')
      const res = await axios.post(`/api/aeos/employees/${empId}/identity/field`,
        { field, prompt },
        { headers: token ? { Authorization: `Bearer ${token}` } : {}, timeout: 330000 })
      const patch = res.data?.patch
      if (patch) {
        setIdentPreview(prev => ({ ...prev, [label]: { ...(prev[label] || {}), ...patch } }))
        setIdentEdit(prev => ({ ...prev, [label]: null }))
      }
    } catch (e) {
      const detail = e?.response?.data?.detail
      onNotify?.(`❌ تعذّر تعديل العنصر: ${detail || (e?.code === 'ECONNABORTED' ? 'استغرق التوليد وقتاً أطول من المتوقّع — أعد المحاولة' : 'خطأ غير متوقع')}`)
    } finally {
      setIdentBusy(prev => ({ ...prev, [label]: null }))
    }
  }

  // ── مهارات مخصّصة: يكتب المالك برومبتاً فيتحوّل إلى مهارة يستخدمها الموظف ──
  const authHeaders = () => {
    const token = localStorage.getItem('token')
    return token ? { Authorization: `Bearer ${token}` } : {}
  }
  const employeeIdOf = (label) => rowsRef.current.find(r => r.label === label)?.agent?.employee_id
  const skillKeyOf = (label, empId) => JSON.stringify([label, empId])
  const skillText = (value) => {
    if (value == null) return ''
    if (typeof value === 'string') {
      const translated = ({ reply: 'رد', like: 'إعجاب', retweet: 'إعادة نشر', quote: 'اقتباس', follow: 'متابعة', bookmark: 'حفظ مرجع', post: 'نشر', thread: 'سلسلة منشورات', generated: 'إنشاء محلي', text: 'نص خارجي', file: 'ملف نصي', url: 'رابط خارجي' })[value]
      return typeof translated === 'string' ? translated : value
    }
    if (typeof value === 'boolean') return value ? 'نعم' : 'لا'
    if (Array.isArray(value)) return value.map(skillText).filter(Boolean).join('، ')
    if (typeof value === 'object') {
      if (value.msg || value.message || value.detail) return skillText(value.msg || value.message || value.detail)
      const captions = { type: 'الإجراء', count: 'العدد', source: 'المصدر', every_cycles: 'كل عدد دورات', hours: 'الساعات المحددة', max_per_day: 'الحد اليومي', requires_mentions: 'يشترط وجود منشنات', content_brief: 'توجيه المحتوى', format: 'الصيغة' }
      return Object.entries(value).map(([key, val]) => `${captions[key] || key}: ${skillText(val) || 'غير محدد'}`).join('؛ ')
    }
    return String(value)
  }
  const skillErrorText = (e) => skillText(e?.response?.data?.detail || e?.response?.data?.error || e?.message) || 'تعذّرت العملية. أعد المحاولة.'
  const skillIsCurrent = (label, empId) => !!empId && employeeIdOf(label) === empId
  const beginSkill = (label, empId, operation) => {
    const key = skillKeyOf(label, empId)
    if (!skillIsCurrent(label, empId) || skillLocks.current[key] || savingP[label] || delBusy[label] || busy[label]) return false
    skillLocks.current[key] = operation
    setSkillBusy(prev => ({ ...prev, [key]: operation }))
    setSkillError(prev => ({ ...prev, [key]: '' }))
    return true
  }
  const endSkill = (key) => {
    delete skillLocks.current[key]
    setSkillBusy(prev => ({ ...prev, [key]: null }))
  }
  const changeSkillInput = (label, empId, patch) => {
    const key = skillKeyOf(label, empId)
    if (!skillIsCurrent(label, empId) || skillLocks.current[key]) return
    setSkillInput(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
    setSkillDraft(prev => ({ ...prev, [key]: null }))
    setSkillError(prev => ({ ...prev, [key]: '' }))
  }
  const fetchCustomSkills = async (label, empId) => {
    const res = await axios.get(`/api/aeos/employees/${encodeURIComponent(empId)}/skills`,
      { headers: authHeaders(), timeout: 8000 })
    if (!skillIsCurrent(label, empId)) return
    if (res.data?.employee_id != null && res.data.employee_id !== empId) throw new Error('قائمة المهارات لا تخص الموظف الحالي.')
    setCustomSkills(prev => ({ ...prev, [skillKeyOf(label, empId)]: res.data?.custom_skills || [] }))
  }
  const loadCustomSkills = async (label, empId = employeeIdOf(label)) => {
    if (!beginSkill(label, empId, 'list')) return
    const key = skillKeyOf(label, empId)
    try {
      await fetchCustomSkills(label, empId)
    } catch (e) { /* قائمة المهارات ليست حرجة لفتح المحرر */
      if (skillIsCurrent(label, empId)) setSkillError(prev => ({ ...prev, [key]: skillErrorText(e) }))
    } finally {
      endSkill(key)
    }
  }

  // معاينة أولاً: المالك يقرأ بالعربية متى تعمل المهارة وماذا تفعل قبل تفعيلها
  const generateSkill = async (label, empId) => {
    if (!beginSkill(label, empId, 'preview')) return
    const key = skillKeyOf(label, empId)
    const input = skillInput[key] || {}
    setSkillDraft(prev => ({ ...prev, [key]: null }))
    try {
      let payload
      const importing = input.mode === 'import'
      if (!importing) {
        const prompt = (skillPrompt[key] || '').trim()
        if (!prompt) throw new Error('اكتب وصف المهارة التي تريدها أولاً.')
        payload = { prompt }
      } else if (input.source === 'url') {
        const url = (input.url || '').trim()
        let parsed
        try { parsed = new URL(url) } catch { throw new Error('أدخل رابط HTTPS صالحاً لمصدر المهارة.') }
        if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password) throw new Error('يجب استخدام رابط HTTPS خارجي دون بيانات دخول.')
        payload = { url }
      } else {
        const content = input.content || ''
        if (!content.trim()) throw new Error('ألصق نص المهارة أو اختر ملفاً نصياً أولاً.')
        if (new TextEncoder().encode(content).byteLength > 32768) throw new Error('حجم النص يجب ألا يتجاوز 32768 بايت بترميز UTF-8.')
        payload = { content, ...(input.filename ? { filename: input.filename } : {}) }
      }
      const res = await axios.post(`/api/aeos/employees/${encodeURIComponent(empId)}/skills/${importing ? 'import' : 'generate'}`,
        payload, { headers: authHeaders(), timeout: 60000 })
      if (!skillIsCurrent(label, empId)) return
      if (res.data?.employee_id !== empId || !res.data?.skill || res.data?.saved !== false || res.data?.ok === false) throw new Error('تعذّر تأكيد معاينة غير محفوظة تخص هذا الموظف. أعد المعاينة.')
      setSkillDraft(prev => ({ ...prev, [key]: res.data }))
    } catch (e) {
      if (skillIsCurrent(label, empId)) setSkillError(prev => ({ ...prev, [key]: skillErrorText(e) }))
    } finally {
      endSkill(key)
    }
  }

  const readSkillFile = async (label, empId, file) => {
    if (!file || !beginSkill(label, empId, 'file')) return
    const key = skillKeyOf(label, empId)
    setSkillDraft(prev => ({ ...prev, [key]: null }))
    setSkillInput(prev => ({ ...prev, [key]: { ...prev[key], content: '', filename: '' } }))
    try {
      if (!/\.(md|txt|json)$/i.test(file.name)) throw new Error('اختر ملف .md أو .txt أو .json فقط.')
      if (file.size > 32768) throw new Error('حجم الملف يجب ألا يتجاوز 32768 بايت.')
      const content = await file.text()
      if (new TextEncoder().encode(content).byteLength > 32768) throw new Error('حجم النص يجب ألا يتجاوز 32768 بايت بترميز UTF-8.')
      if (!content.trim()) throw new Error('الملف فارغ. اختر ملفاً يحتوي على وصف المهارة.')
      if (skillIsCurrent(label, empId)) setSkillInput(prev => ({ ...prev, [key]: { ...prev[key], content, filename: file.name } }))
    } catch (e) {
      if (skillIsCurrent(label, empId)) setSkillError(prev => ({ ...prev, [key]: skillErrorText(e) }))
    } finally {
      endSkill(key)
    }
  }

  const activateSkill = async (label, empId) => {
    const key = skillKeyOf(label, empId)
    const draft = skillDraft[key]
    if (!skillIsCurrent(label, empId) || draft?.employee_id !== empId || !draft?.skill) {
      setSkillDraft(prev => ({ ...prev, [key]: null }))
      setSkillError(prev => ({ ...prev, [key]: 'هذه المعاينة لا تخص الموظف الحالي. أنشئ معاينة جديدة.' }))
      return
    }
    if (draft.readiness?.ready === false || !beginSkill(label, empId, 'save')) return
    try {
      const res = await axios.post(`/api/aeos/employees/${encodeURIComponent(empId)}/skills`,
        { spec: draft.skill }, { headers: authHeaders(), timeout: 20000 })
      if (!skillIsCurrent(label, empId)) return
      if (res.data?.employee_id !== empId || !res.data?.skill || res.data?.ok === false) throw new Error('تعذّر تأكيد حفظ المهارة لهذا الموظف. حدّث القائمة قبل إعادة المحاولة.')
      onNotify?.(`تم حفظ مهارة «${res.data.skill?.label}» للموظف @${label} فقط. ستخضع للجاهزية والحدود في الدورات القادمة.`)
      setSkillDraft(prev => ({ ...prev, [key]: null }))
      setSkillPrompt(prev => ({ ...prev, [key]: '' }))
      setSkillInput(prev => ({ ...prev, [key]: { mode: prev[key]?.mode || 'create' } }))
      await fetchCustomSkills(label, empId)
    } catch (e) {
      if (skillIsCurrent(label, empId)) setSkillError(prev => ({ ...prev, [key]: skillErrorText(e) }))
    } finally {
      endSkill(key)
    }
  }

  const toggleCustomSkill = async (label, empId, skill) => {
    if (!skill.enabled && skill.readiness?.ready === false) return
    if (!beginSkill(label, empId, `id:${skill.id}`)) return
    const key = skillKeyOf(label, empId)
    try {
      await axios.patch(`/api/aeos/employees/${encodeURIComponent(empId)}/skills/${encodeURIComponent(skill.id)}`,
        { enabled: !skill.enabled }, { headers: authHeaders(), timeout: 15000 })
      if (skillIsCurrent(label, empId)) await fetchCustomSkills(label, empId)
    } catch (e) {
      if (skillIsCurrent(label, empId)) setSkillError(prev => ({ ...prev, [key]: skillErrorText(e) }))
    } finally {
      endSkill(key)
    }
  }

  const deleteCustomSkill = async (label, empId, skill) => {
    if (!skillIsCurrent(label, empId) || skillLocks.current[skillKeyOf(label, empId)]) return
    if (!window.confirm(`حذف مهارة «${skill.label}» من الموظف @${label} نهائياً؟`)) return
    if (!beginSkill(label, empId, `id:${skill.id}`)) return
    const key = skillKeyOf(label, empId)
    try {
      await axios.delete(`/api/aeos/employees/${encodeURIComponent(empId)}/skills/${encodeURIComponent(skill.id)}`,
        { headers: authHeaders(), timeout: 15000 })
      if (!skillIsCurrent(label, empId)) return
      onNotify?.(`حُذفت مهارة «${skill.label}» من الموظف @${label}.`)
      await fetchCustomSkills(label, empId)
    } catch (e) {
      if (skillIsCurrent(label, empId)) setSkillError(prev => ({ ...prev, [key]: skillErrorText(e) }))
    } finally {
      endSkill(key)
    }
  }

  refreshSkillsRef.current = loadCustomSkills
  useEffect(() => {
    if (!open) return
    rows.forEach(({ label, agent }) => {
      if (expanded[label] && agent?.employee_id) refreshSkillsRef.current(label, agent.employee_id)
    })
  }, [rows, expanded, open, savingP, busy])

  const skillReadiness = (readiness, warnings = []) => {
    const messages = [...new Set([...(Array.isArray(warnings) ? warnings : []), ...(readiness?.warnings || [])])]
    return (
      <div className="text-[11px] space-y-1 text-text-secondary-light dark:text-text-secondary-dark">
        <p className={readiness?.ready === false ? 'text-amber-700 dark:text-amber-300' : ''}>
          الجاهزية: {readiness?.ready === true ? 'جاهزة وفق إعدادات الموظف المحفوظة' : readiness?.ready === false ? 'غير جاهزة للتفعيل' : 'غير متاحة؛ حدّث القائمة للتحقق'}
        </p>
        {!!readiness?.missing_mcp?.length && <p>أدوات ناقصة: {readiness.missing_mcp.map(mc => MCP_LABELS[mc] || mc).join('، ')}</p>}
        {!!readiness?.blocked_capabilities?.length && <p>قدرات غير متاحة: {skillText(readiness.blocked_capabilities)}</p>}
        {messages.length > 0 && <ul className="list-disc list-inside text-amber-700 dark:text-amber-300">{messages.map((message, index) => <li key={index}>{skillText(message)}</li>)}</ul>}
      </div>
    )
  }

  // ── فتح شات مخصّص مرتبط بالموظف: يثبّت حسابه ويفتح جلسة نقاش خاصة به ──
  const openChat = (label) => {
    window.dispatchEvent(new CustomEvent('mwj:open_employee_chat', { detail: { label } }))
    onClose?.()
  }

  // ── رؤى الموظف: إحصائيات / محتويات / اقتراحات — تُعرض في الشات بنقرة ──
  const showInsights = async (label, kind) => {
    const row = rows.find(r => r.label === label)
    const empId = row?.agent?.employee_id
    if (!empId) { onNotify?.('فعّل الموظف أولاً.'); return }
    setInsightBusy(prev => ({ ...prev, [`${label}:${kind}`]: true }))
    try {
      const token = localStorage.getItem('token')
      const res = await axios.get(`/api/aeos/employees/${empId}/insights`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      const d = res.data || {}
      const text = kind === 'contents' ? d.contents_text
        : kind === 'suggestions' ? d.suggestions_text
        : d.stats_text
      onNotify?.(text || 'لا توجد بيانات بعد.')
    } catch (e) {
      onNotify?.(`❌ تعذّر جلب بيانات @${label}`)
    } finally {
      setInsightBusy(prev => ({ ...prev, [`${label}:${kind}`]: false }))
    }
  }

  // ── سجلّات الدورات: افتح/أغلق واقرأ ماذا حصل في كل دورة ──
  const openLogs = async (label, empId) => {
    const willOpen = !logsOpen[label]
    setLogsOpen(prev => ({ ...prev, [label]: willOpen }))
    if (!willOpen) return
    if (!empId) { onNotify?.('فعّل الموظف أولاً لعرض سجلّات الدورات.'); return }
    setLogsBusy(prev => ({ ...prev, [label]: true }))
    try {
      const token = localStorage.getItem('token')
      // نريد "تسلسل الدورات كامل" داخل سجل الدورات
      const res = await axios.get(`/api/aeos/employees/${empId}/logs?limit=25&latest_only=false`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {}, timeout: 8000 })

      // عند latest_only=false backend يرجع logs: [...]
      const logsArr = Array.isArray(res.data?.logs) ? res.data.logs : []

      setLogsData(prev => ({ ...prev, [label]: logsArr }))
    } catch (e) {
      onNotify?.(`❌ تعذّر تحميل سجلّات @${label}`)
      setLogsData(prev => ({ ...prev, [label]: [] }))
    } finally {
      setLogsBusy(prev => ({ ...prev, [label]: false }))
    }
  }

  const fmtTime = (iso) => {
    if (!iso) return '—'
    try {
      const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
      return d.toLocaleString('ar-SA', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
    } catch { return '—' }
  }

  const fmtWake = (iso) => {
    if (!iso) return '—'
    try {
      return new Date(iso).toLocaleString('ar-SA', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
    } catch { return '—' }
  }

  return (
    <>
      <div
        className={`fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
      />
      <aside
        className={`fixed top-0 right-0 h-full w-full sm:w-[440px] bg-card-light dark:bg-sidebar-dark border-l border-border-light dark:border-border-dark shadow-2xl z-50 flex flex-col transition-transform duration-300 ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-light dark:border-border-dark shrink-0">
          <div className="flex items-center gap-2">
            <MdSmartToy className="text-primary" size={22} />
            <h3 className="text-base font-bold text-text-primary-light dark:text-text-primary-dark">الموظفين الذكيين</h3>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setWizardOpen(true)}
              className="flex items-center gap-1 text-xs font-bold px-2.5 py-1.5 rounded-lg bg-primary text-white hover:bg-primary/90"
              title="إنشاء موظف ذكي جديد خطوة بخطوة"
            >
              <MdAdd size={16} /> موظف جديد
            </button>
            <button onClick={load} className="size-8 flex items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark" title="تحديث">
              <MdRefresh size={20} />
            </button>
            <button onClick={onClose} className="size-8 flex items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark" title="إغلاق">
              <MdClose size={20} />
            </button>
          </div>
        </div>

        <div className="px-5 py-3 border-b border-border-light dark:border-border-dark bg-gray-50 dark:bg-card-dark/40 shrink-0">
          <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark leading-relaxed">
            فعّل الحساب ليعمل الموظف الذكي <b>تلقائياً بالجدولة</b> — يفكّر، يخطّط، وينفّذ بنفسه دون أوامر مباشرة.
            يمكن تشغيل عدة موظفين معاً، ويتوقفون خارج ساعات العمل.
          </p>
          <div className="mt-2 text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark">
            يعمل الآن: <span className="text-green-600 dark:text-green-400 font-bold">{summary.running}</span> / {summary.total} موظف
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {loading ? (
            <div className="flex items-center justify-center py-10">
              <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="text-center text-sm text-red-500 py-10">{error}</div>
          ) : rows.length === 0 ? (
            <div className="text-center text-sm text-text-secondary-light dark:text-text-secondary-dark py-10">
              لا توجد حسابات. ارفع ملف كوكيز أولاً.
            </div>
          ) : (
            rows.map(({ label, agent }) => {
              const enabled = agent?.enabled || false
              const within = agent?.within_active_hours ?? true
              const interval = agent?.interval_minutes ?? 60
              const hs = agent?.active_hours_start ?? 8
              const he = agent?.active_hours_end ?? 23
              const cycles = agent?.cycles_completed ?? 0
              const employeeId = agent?.employee_id
              const skillKey = skillKeyOf(label, employeeId)
              const skillForm = skillInput[skillKey] || {}
              const skillMode = skillForm.mode || 'create'
              const draft = skillDraft[skillKey]?.employee_id === employeeId ? skillDraft[skillKey] : null
              const skillsDisabled = !employeeId || !!skillBusy[skillKey] || !!savingP[label] || !!delBusy[label] || !!busy[label]
              const skillInputId = `employee-skill-${encodeURIComponent(label)}-${encodeURIComponent(employeeId || 'none')}`
              return (
                <div key={label} className="rounded-xl border border-border-light dark:border-border-dark bg-card-light dark:bg-card-dark p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="size-9 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <MdPerson className="text-primary" size={20} />
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-bold text-text-primary-light dark:text-text-primary-dark truncate">@{label}</p>
                        <p className={`text-xs ${enabled ? (within ? 'text-green-600 dark:text-green-400' : 'text-amber-500') : 'text-text-secondary-light dark:text-text-secondary-dark'}`}>
                          {enabled ? (within ? 'نشط ويعمل' : 'نشط (خارج ساعات العمل)') : 'متوقف'}
                        </p>
                      </div>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer shrink-0">
                      <input
                        type="checkbox"
                        className="sr-only peer"
                        checked={enabled}
                        disabled={busy[label] || !!skillBusy[skillKey]}
                        onChange={(e) => toggle(label, e.target.checked)}
                      />
                      <div className="w-11 h-6 bg-gray-200 dark:bg-gray-700 rounded-full peer peer-checked:bg-primary after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-5 rtl:peer-checked:after:-translate-x-5" />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mt-3">
                    <div>
                      <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">كل (دقيقة)</label>
                      <input type="number" min="1" value={interval}
                        onChange={(e) => updateField(label, 'interval_minutes', e.target.value)}
                        className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                    </div>
                    <div>
                      <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">يبدأ الساعة</label>
                      <input type="number" min="0" max="23" value={hs}
                        onChange={(e) => updateField(label, 'active_hours_start', e.target.value)}
                        className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                    </div>
                    <div>
                      <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">يتوقف الساعة</label>
                      <input type="number" min="0" max="23" value={he}
                        onChange={(e) => updateField(label, 'active_hours_end', e.target.value)}
                        className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                    </div>
                  </div>

                  <div className="flex items-center justify-between mt-3 text-[11px] text-text-secondary-light dark:text-text-secondary-dark">
                    <button
                      onClick={() => openLogs(label, agent?.employee_id)}
                      disabled={!agent?.employee_id}
                      title="اعرض سجلّ كل دورة وماذا حصل فيها"
                      className="flex items-center gap-1 text-text-secondary-light dark:text-text-secondary-dark hover:text-primary disabled:opacity-40 disabled:hover:text-text-secondary-light"
                    >
                      <MdHistory size={15} /> الدورات: <b className="text-primary underline">{cycles}</b>
                    </button>
                    <span>الإيقاظ التالي: <b className="text-text-primary-light dark:text-text-primary-dark">{fmtWake(agent?.next_wake_at)}</b></span>
                    <button
                      onClick={() => runNow(label)}
                      disabled={!agent?.employee_id}
                      className="flex items-center gap-1 text-primary hover:underline font-medium disabled:opacity-40 disabled:no-underline"
                    >
                      <MdPlayArrow size={16} /> تشغيل الآن
                    </button>
                  </div>

                  {/* دردشة مخصّصة مع الموظف: نقاش + تحسين سلوك + مراجعة مواضيع + جدولة + إحصائيات */}
                  <button
                    onClick={() => openChat(label)}
                    disabled={!agent?.employee_id}
                    title="افتح شاتاً خاصاً بهذا الموظف: ناقشه، حسّن سلوكه، راجع المواضيع، جدول محتوى يومي، واطّلع على إحصائياته"
                    className="w-full flex items-center justify-center gap-2 mt-3 px-3 py-2.5 rounded-xl bg-primary text-white font-bold text-sm hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    <MdChat size={18} /> دردشة مع الموظف
                  </button>

                  {/* رؤى سريعة: تظهر النتيجة في الشات بنقرة واحدة */}
                  <div className="flex items-center gap-2 mt-2">
                    <button
                      onClick={() => showInsights(label, 'stats')}
                      disabled={!agent?.employee_id || insightBusy[`${label}:stats`]}
                      className="flex-1 text-[11px] px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark hover:bg-primary/10 text-text-primary-light dark:text-text-primary-dark disabled:opacity-40"
                    >
                      📊 إحصائيات
                    </button>
                    <button
                      onClick={() => showInsights(label, 'contents')}
                      disabled={!agent?.employee_id || insightBusy[`${label}:contents`]}
                      className="flex-1 text-[11px] px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark hover:bg-primary/10 text-text-primary-light dark:text-text-primary-dark disabled:opacity-40"
                    >
                      📝 المحتويات
                    </button>
                    <button
                      onClick={() => showInsights(label, 'suggestions')}
                      disabled={!agent?.employee_id || insightBusy[`${label}:suggestions`]}
                      className="flex-1 text-[11px] px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark hover:bg-primary/10 text-text-primary-light dark:text-text-primary-dark disabled:opacity-40"
                    >
                      💡 اقتراحات
                    </button>
                  </div>

                  {/* هوية X من الشخصية: توليد + معاينة + تطبيق بضغطة */}
                  {!identPreview[label] ? (
                    <button
                      onClick={() => previewIdentity(label)}
                      disabled={!agent?.employee_id || !!identBusy[label]}
                      title="يولّد هوية كاملة (اسم + بايو + صورة + هيدر) من شخصية الموظف ويعرضها للمعاينة قبل تطبيقها على حسابه في X"
                      className="w-full flex items-center justify-center gap-2 mt-2 px-3 py-2 rounded-xl border-2 border-dashed border-primary/60 text-primary font-bold text-xs hover:bg-primary/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      {identBusy[label] === 'preview' ? (
                        <>
                          <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                          جارِ توليد الهوية من الشخصية… (قد تأخذ دقيقتين)
                        </>
                      ) : (
                        <>
                          <MdBadge size={17} /> هوية X من الشخصية (معاينة ثم تطبيق)
                        </>
                      )}
                    </button>
                  ) : (
                    <div className="mt-2 rounded-xl border border-primary/40 overflow-hidden bg-gray-50 dark:bg-card-dark/40">
                      {/* معاينة بشكل بروفايل X مصغّر */}
                      {identPreview[label].banner_url && (
                        <img src={identPreview[label].banner_url} alt="header"
                          className="w-full h-20 object-cover" />
                      )}
                      <div className="px-3 pb-3">
                        <div className="flex items-end gap-2 -mt-6">
                          {identPreview[label].avatar_url && (
                            <img src={identPreview[label].avatar_url} alt="avatar"
                              className="size-14 rounded-full border-4 border-card-light dark:border-card-dark object-cover shrink-0" />
                          )}
                          <div className="min-w-0 pt-7">
                            <p className="text-sm font-bold text-text-primary-light dark:text-text-primary-dark truncate">
                              {identPreview[label].name || '—'}
                            </p>
                            <p className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark">@{label}</p>
                          </div>
                        </div>
                        {identPreview[label].bio && (
                          <p className="mt-1.5 text-xs text-text-primary-light dark:text-text-primary-dark leading-relaxed">
                            {identPreview[label].bio}
                          </p>
                        )}
                        <div className="flex items-center gap-2 mt-2.5">
                          <button
                            onClick={() => applyIdentity(label)}
                            disabled={identBusy[label] === 'apply'}
                            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary/90 disabled:opacity-50"
                          >
                            {identBusy[label] === 'apply'
                              ? <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                              : <MdCheckCircle size={15} />}
                            تطبيق على X
                          </button>
                          <button
                            onClick={() => previewIdentity(label)}
                            disabled={!!identBusy[label]}
                            title="توليد هوية مختلفة من نفس الشخصية"
                            className="flex items-center gap-1 px-2.5 py-2 rounded-lg border border-border-light dark:border-border-dark text-xs text-text-primary-light dark:text-text-primary-dark hover:bg-primary/10 disabled:opacity-50"
                          >
                            {identBusy[label] === 'preview'
                              ? <div className="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                              : <MdRefresh size={15} />}
                            إعادة توليد
                          </button>
                          <button
                            onClick={() => setIdentPreview(prev => ({ ...prev, [label]: null }))}
                            disabled={!!identBusy[label]}
                            className="px-2.5 py-2 rounded-lg border border-border-light dark:border-border-dark text-xs text-text-secondary-light dark:text-text-secondary-dark hover:bg-gray-100 dark:hover:bg-card-dark disabled:opacity-50"
                          >
                            إلغاء
                          </button>
                        </div>
                        {/* تعديل عنصر منفرد: صورة / هيدر / اسم / بايو — ببرومبت اختياري */}
                        <div className="flex flex-wrap items-center gap-1.5 mt-2.5 pt-2 border-t border-border-light dark:border-border-dark">
                          <span className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">تعديل عنصر:</span>
                          {[['avatar', 'الصورة'], ['banner', 'الهيدر'], ['name', 'الاسم'], ['bio', 'البايو']].map(([f, fl]) => (
                            <button
                              key={f}
                              onClick={() => setIdentEdit(prev => ({ ...prev, [label]: prev[label]?.field === f ? null : { field: f, prompt: prev[label]?.prompt || '' } }))}
                              disabled={!!identBusy[label]}
                              title={`إعادة توليد ${fl} فقط — مع إمكانية كتابة طلب يوجّه التعديل`}
                              className={`flex items-center gap-1 px-2 py-1 rounded-lg border text-[11px] disabled:opacity-50 transition-colors ${identEdit[label]?.field === f
                                ? 'border-primary bg-primary/10 text-primary font-bold'
                                : 'border-border-light dark:border-border-dark text-text-primary-light dark:text-text-primary-dark hover:bg-primary/10'}`}
                            >
                              {identBusy[label] === `field:${f}`
                                ? <div className="w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                                : <MdEdit size={12} />}
                              {fl}
                            </button>
                          ))}
                        </div>
                        {identEdit[label]?.field && (
                          <div className="flex items-center gap-1.5 mt-1.5">
                            <input
                              type="text"
                              value={identEdit[label]?.prompt || ''}
                              onChange={e => setIdentEdit(prev => ({ ...prev, [label]: { ...prev[label], prompt: e.target.value } }))}
                              onKeyDown={e => { if (e.key === 'Enter' && !identBusy[label]) regenerateField(label, identEdit[label].field) }}
                              placeholder={identEdit[label].field === 'avatar' ? 'وصف التعديل (اختياري) — مثال: خلفية زرقاء وملامح أكثر جدية'
                                : identEdit[label].field === 'banner' ? 'وصف التعديل (اختياري) — مثال: مشهد مدينة ليلاً بألوان هادئة'
                                  : identEdit[label].field === 'name' ? 'وصف التعديل (اختياري) — مثال: اسم أقصر وأسهل حفظاً'
                                    : 'وصف التعديل (اختياري) — مثال: بايو أقصر مع لمسة حماسية'}
                              disabled={!!identBusy[label]}
                              dir="rtl"
                              className="flex-1 min-w-0 px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-white dark:bg-card-dark text-[11px] text-text-primary-light dark:text-text-primary-dark placeholder:text-text-secondary-light/60 dark:placeholder:text-text-secondary-dark/60 focus:outline-none focus:border-primary"
                            />
                            <button
                              onClick={() => regenerateField(label, identEdit[label].field)}
                              disabled={!!identBusy[label]}
                              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-primary text-white text-[11px] font-bold hover:bg-primary/90 disabled:opacity-50 shrink-0"
                            >
                              {identBusy[label] === `field:${identEdit[label].field}`
                                ? <div className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                : <MdRefresh size={13} />}
                              توليد
                            </button>
                          </div>
                        )}
                        <p className="mt-1.5 text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
                          التطبيق يغيّر الاسم والبايو والصورة والهيدر في حساب X الحقيقي، ويحدّث برسونا الموظف لتطابقها. التعديل المنفرد يحدّث المعاينة فقط — اضغط «تطبيق على X» لاعتماده.
                        </p>
                      </div>
                    </div>
                  )}

                  {/* حذف: الموظف الذكي فقط، أو الموظف + الحساب المرتبط */}
                  <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border-light dark:border-border-dark">
                    <button
                      onClick={() => deleteRow(label, agent, false)}
                      disabled={delBusy[label] || !agent?.employee_id || !!skillBusy[skillKey]}
                      title="حذف الموظف الذكي فقط (يبقى الحساب/الكوكيز)"
                      className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg border border-red-300 dark:border-red-500/40 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <MdDelete size={14} /> حذف الموظف
                    </button>
                    <button
                      onClick={() => deleteRow(label, agent, true)}
                      disabled={delBusy[label] || !!skillBusy[skillKey]}
                      title="حذف الموظف الذكي والحساب المرتبط (ملف الكوكيز) نهائياً"
                      className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <MdDelete size={14} /> حذف الموظف + الحساب
                    </button>
                    {delBusy[label] && (
                      <div className="w-4 h-4 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
                    )}
                  </div>

                  {/* لوحة سجلّات الدورات — ماذا حصل في كل دورة */}
                  {logsOpen[label] && (
                    <div className="mt-3 pt-3 border-t border-border-light dark:border-border-dark">
                      <div className="flex items-center gap-2 mb-2 text-sm font-medium text-text-primary-light dark:text-text-primary-dark">
                        <MdHistory size={16} className="text-primary" /> سجلّ الدورات (الأحدث أولاً)
                      </div>
                      {logsBusy[label] ? (
                        <div className="flex items-center justify-center py-4">
                          <div className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                        </div>
                      ) : !(logsData[label]?.length) ? (
                        <div className="text-xs text-text-secondary-light dark:text-text-secondary-dark py-3 text-center">
                          لا توجد دورات مسجّلة بعد.
                        </div>
                      ) : (
                        <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                          {logsData[label].map(lg => {
                            const results = Array.isArray(lg.results) ? lg.results : []
                            const decisions = Array.isArray(lg.decisions) ? lg.decisions : []
                            const hasErr = !lg.success || (lg.phase_errors?.length) || lg.error
                              || results.some(r => r && r.success === false)
                            return (
                              <div key={lg.id} className="rounded-lg border border-border-light dark:border-border-dark p-2.5 text-[11px] bg-gray-50 dark:bg-card-dark/40">
                                <div className="flex items-center justify-between">
                                  <span className="flex items-center gap-1 font-medium">
                                    {hasErr ? <MdError size={14} className="text-red-500" /> : <MdCheckCircle size={14} className="text-green-600 dark:text-green-400" />}
                                    {lg.event_type || 'cycle'}
                                  </span>
                                  <span className="text-text-secondary-light dark:text-text-secondary-dark">{fmtTime(lg.created_at)} · {lg.duration_ms ?? 0}ms</span>
                                </div>

                                {lg.thought && lg.thought !== 'heuristic mode (no LLM)' && (
                                  <div className="mt-1 text-text-secondary-light dark:text-text-secondary-dark"><b>الفكرة:</b> {lg.thought}</div>
                                )}

                                {decisions.length > 0 && (
                                  <div className="mt-1">
                                    <b>القرار:</b> {decisions.map((d, i) => (
                                      <span key={i}>{d.capability}{d.rationale ? ` (${d.rationale})` : ''}{i < decisions.length - 1 ? '، ' : ''}</span>
                                    ))}
                                  </div>
                                )}

                                {results.length > 0 && (
                                  <div className="mt-1 space-y-0.5">
                                    <b>النتيجة:</b>
                                    {results.map((r, i) => (
                                      <div key={i} className={`flex items-start gap-1 ${r.success ? 'text-green-700 dark:text-green-400' : 'text-red-500'}`}>
                                        {r.success ? '✓' : '✗'} {r.capability}
                                        {r.error ? <span className="opacity-90">— {r.error}</span> : null}
                                        {r.output?.tweet_url ? <a href={r.output.tweet_url} target="_blank" rel="noreferrer" className="underline">التغريدة</a> : null}
                                      </div>
                                    ))}
                                  </div>
                                )}

                                {lg.error && <div className="mt-1 text-red-500"><b>خطأ:</b> {lg.error}</div>}
                                {lg.phase_errors?.length > 0 && (
                                  <div className="mt-1 text-red-500"><b>مراحل فشلت:</b> {lg.phase_errors.join('، ')}</div>
                                )}
                                {lg.lesson && <div className="mt-1 text-text-secondary-light dark:text-text-secondary-dark"><b>الخلاصة:</b> {lg.lesson}</div>}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )}

                  {/* محرّر البرسونا والتعليمات */}
                  <button
                    onClick={() => toggleExpand(label, agent)}
                    className="w-full flex items-center justify-between mt-3 pt-3 border-t border-border-light dark:border-border-dark text-sm font-medium text-text-primary-light dark:text-text-primary-dark"
                  >
                    <span className="flex items-center gap-2"><MdTune size={18} className="text-primary" /> التعليمات والبرسونا</span>
                    <MdExpandMore size={20} className={`transition-transform ${expanded[label] ? 'rotate-180' : ''}`} />
                  </button>

                  {expanded[label] && (
                    <div className="mt-3 space-y-2">
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">الاسم الظاهر</label>
                          <input value={pForm[label]?.display_name || ''} onChange={(e) => setP(label, 'display_name', e.target.value)}
                            className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                        </div>
                        <div>
                          <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">الدور / الوظيفة</label>
                          <input value={pForm[label]?.role || ''} onChange={(e) => setP(label, 'role', e.target.value)}
                            className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                        </div>
                        <div>
                          <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">النبرة</label>
                          <select value={pForm[label]?.tone || 'professional'} onChange={(e) => setP(label, 'tone', e.target.value)}
                            className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-card-light dark:bg-card-dark text-text-primary-light dark:text-text-primary-dark">
                            <option value="professional">احترافية</option>
                            <option value="casual">عفوية</option>
                            <option value="witty">ساخرة / ذكية</option>
                            <option value="formal">رسمية</option>
                            <option value="friendly">ودّية</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">اللغة</label>
                          <select value={pForm[label]?.language || 'ar'} onChange={(e) => setP(label, 'language', e.target.value)}
                            className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-card-light dark:bg-card-dark text-text-primary-light dark:text-text-primary-dark">
                            <option value="ar">العربية</option>
                            <option value="en">English</option>
                          </select>
                        </div>
                      </div>

                      <div>
                        <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">نبذة (Bio)</label>
                        <input value={pForm[label]?.bio || ''} onChange={(e) => setP(label, 'bio', e.target.value)}
                          className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                      </div>

                      {/* الاهتمامات + النماذج الجاهزة + التوليد بالذكاء الاصطناعي */}
                      <div className="rounded-lg border border-border-light dark:border-border-dark p-2.5 space-y-2 bg-gray-50 dark:bg-card-dark/40">
                        <label className="block text-[11px] font-medium text-text-primary-light dark:text-text-primary-dark">الاهتمام (اختر واحداً أو أكثر — يطبّق نموذجاً جاهزاً)</label>
                        <div className="flex flex-wrap gap-1.5">
                          {(personaMeta.interests.length ? personaMeta.interests : Object.entries(INTEREST_LABELS).map(([id, label]) => ({ id, label }))).map(it => {
                            const on = (pForm[label]?.interests || []).includes(it.id)
                            return (
                              <button key={it.id} type="button"
                                onClick={() => { toggleInList(label, 'interests', it.id); if (!on) applyTemplate(label, it.id) }}
                                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${on ? 'bg-primary text-white border-primary' : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark'}`}>
                                {it.label || INTEREST_LABELS[it.id] || it.id}
                              </button>
                            )
                          })}
                        </div>
                        <input value={pForm[label]?.brief || ''} onChange={(e) => setP(label, 'brief', e.target.value)}
                          placeholder="وصف اختياري للبرسونا المطلوبة (مثال: حساب شبابي يهتم بالتقنية والرياضة)"
                          className="w-full text-xs px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                        <button type="button" onClick={() => generateAI(label)} disabled={genBusy[label]}
                          className="w-full flex items-center justify-center gap-2 px-3 py-1.5 rounded-lg border border-primary text-primary hover:bg-primary hover:text-white transition-colors text-xs font-medium disabled:opacity-50">
                          <MdSmartToy size={16} /> {genBusy[label] ? 'جارِ التوليد...' : 'توليد برسونا بالذكاء الاصطناعي'}
                        </button>
                      </div>

                      {/* استنساخ أسلوب حساب X معيّن */}
                      <div className="rounded-lg border border-purple-300 dark:border-purple-500/40 p-2.5 space-y-2 bg-purple-50/50 dark:bg-purple-500/5">
                        <label className="block text-[11px] font-medium text-text-primary-light dark:text-text-primary-dark">
                          🧬 استنساخ أسلوب حساب
                        </label>
                        <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark leading-relaxed">
                          أدخل يوزر حساب X — سيدخل النظام بكوكيز حسابك، يسحب عيّنة من تغريداته وردوده، يحلّل أسلوبه ومواضيعه وطريقة نقاشه، ثم يبني برسونا مطابقة تملأ الحقول تلقائياً لمراجعتها قبل الحفظ.
                        </p>
                        <div className="flex gap-2">
                          <input
                            value={pForm[label]?.clone_handle || ''}
                            onChange={(e) => setP(label, 'clone_handle', e.target.value)}
                            placeholder="@اليوزر المراد استنساخه"
                            dir="ltr"
                            className="flex-1 text-xs px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark"
                          />
                          <button type="button" onClick={() => cloneStyle(label)} disabled={cloneBusy[label]}
                            className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-xs font-medium disabled:opacity-50">
                            {cloneBusy[label]
                              ? (<><span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" /> جارِ الاستنساخ...</>)
                              : '🧬 استنساخ'}
                          </button>
                        </div>
                        <VoiceProfileView
                          profile={pForm[label]?.voice_pending || pForm[label]?.voice_profile}
                          pending={!!pForm[label]?.voice_pending}
                          fidelity={pForm[label]?.clone_fidelity}
                        />
                      </div>

                      <div>
                        <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">اللهجة</label>
                        <input value={pForm[label]?.dialect || ''} onChange={(e) => setP(label, 'dialect', e.target.value)}
                          placeholder="مثال: سعودية، مصرية، فصحى"
                          className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
                      </div>

                      <div>
                        <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">التعليمات الثابتة (ما الذي تريده أن يفعله؟)</label>
                        <textarea rows={4} value={pForm[label]?.instructions || ''} onChange={(e) => setP(label, 'instructions', e.target.value)}
                          placeholder="مثال: انشر تغريدة يومية عن التقنية بأسلوب مبسّط، وتفاعل مع المتابعين، وتجنّب المواضيع السياسية."
                          className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark resize-y" />
                      </div>

                      <div>
                        <label className="block text-[11px] text-text-secondary-light dark:text-text-secondary-dark mb-1">الأهداف (هدف في كل سطر)</label>
                        <textarea rows={2} value={pForm[label]?.goals || ''} onChange={(e) => setP(label, 'goals', e.target.value)}
                          placeholder="زيادة التفاعل\nنشر محتوى تعليمي"
                          className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark resize-y" />
                      </div>

                      <div>
                        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                          <h4 className="text-xs font-semibold text-text-primary-light dark:text-text-primary-dark">المهارات (Skills)</h4>
                          <div className="flex gap-2" role="group" aria-label={`إضافة مهارة للموظف @${label}`}>
                            {[['create', 'إنشاء مهارة'], ['import', 'جلب مهارة']].map(([mode, caption]) => (
                              <button key={mode} type="button" aria-pressed={skillMode === mode}
                                aria-controls={`${skillInputId}-editor`} disabled={skillsDisabled}
                                onClick={() => changeSkillInput(label, employeeId, { mode })}
                                className={`px-3 py-1.5 rounded-lg border text-xs font-medium disabled:opacity-50 ${skillMode === mode ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-indigo-400 text-indigo-700 dark:text-indigo-300'}`}>
                                {caption}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {(catalog.skills.length ? catalog.skills : ['daily_content_creator','community_engager','trend_watcher']).map(sk => {
                            const on = (pForm[label]?.skills || []).includes(sk)
                            return (
                              <button key={sk} type="button" onClick={() => toggleInList(label, 'skills', sk)}
                                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${on ? 'bg-primary text-white border-primary' : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark'}`}>
                                {SKILL_LABELS[sk] || sk}
                              </button>
                            )
                          })}
                        </div>
                      </div>

                      {/* ── مهارة مخصّصة لهذا الموظف من برومبت ── */}
                      {settingsGroup(
                        'فهم المهارة ومعاينة التنفيذ',
                        `هذه المهارات محلية للموظف @${label} فقط، ولا تُضاف إلى الكتالوج العام. المعاينة لا تحفظ شيئاً ولا تدرّب النموذج؛ بعد التفعيل تُستخدم في الدورات القادمة وفق حارس الشخصية والأدوات والميزانيات.`,
                        <div id={`${skillInputId}-editor`} role="group" aria-label={`فهم المهارة ومعاينة التنفيذ للموظف @${label}`} aria-busy={!!skillBusy[skillKey]} className="space-y-2">
                          {!employeeId && <p className="text-xs text-amber-700 dark:text-amber-300">فعّل الموظف أولاً لإنشاء أو جلب مهارة تخصّه.</p>}
                          {skillMode === 'create' ? (
                            <div>
                              <label htmlFor={`${skillInputId}-prompt`} className={capCls}>وصف المهارة المطلوب إنشاؤها</label>
                              <textarea id={`${skillInputId}-prompt`} rows={3} disabled={skillsDisabled}
                                value={skillPrompt[skillKey] || ''}
                                onChange={(e) => {
                                  if (skillLocks.current[skillKey] || !skillIsCurrent(label, employeeId)) return
                                  changeSkillInput(label, employeeId, {})
                                  setSkillPrompt(prev => ({ ...prev, [skillKey]: e.target.value }))
                                }}
                                placeholder="صف متى تعمل المهارة، ومن أين تختار الهدف، وما الإجراء المطلوب."
                                className={`${inputCls} resize-y disabled:opacity-50`} />
                            </div>
                          ) : (
                            <div className="space-y-2">
                              <div role="group" aria-label="مصدر المهارة الخارجية" className="flex flex-wrap gap-2">
                                {[['text', 'نص أو ملف'], ['url', 'رابط HTTPS']].map(([source, caption]) => (
                                  <button key={source} type="button" aria-pressed={(skillForm.source || 'text') === source}
                                    disabled={skillsDisabled} onClick={() => changeSkillInput(label, employeeId, { source })}
                                    className={`text-xs px-3 py-1 rounded-lg border disabled:opacity-50 ${(skillForm.source || 'text') === source ? 'bg-primary text-white border-primary' : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark'}`}>
                                    {caption}
                                  </button>
                                ))}
                              </div>
                              {skillForm.source === 'url' ? (
                                <div>
                                  <label htmlFor={`${skillInputId}-url`} className={capCls}>رابط HTTPS خارجي لملف المهارة</label>
                                  <input id={`${skillInputId}-url`} type="url" dir="ltr" disabled={skillsDisabled}
                                    value={skillForm.url || ''} placeholder="https://example.com/SKILL.md"
                                    onChange={(e) => changeSkillInput(label, employeeId, { url: e.target.value })}
                                    className={inputCls} />
                                  <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">الخادم يجلب الرابط ويتحقق منه؛ لا يُفتح أو يُنفّذ محتواه في المتصفح.</p>
                                </div>
                              ) : (
                                <div className="space-y-2">
                                  <div>
                                    <label htmlFor={`${skillInputId}-file`} className={capCls}>قراءة ملف مهارة (.md أو .txt أو .json، حتى 32768 بايت)</label>
                                    <input key={skillKey} id={`${skillInputId}-file`} type="file" accept=".md,.txt,.json"
                                      disabled={skillsDisabled} className={`${inputCls} text-xs`}
                                      onChange={(e) => {
                                        const file = e.target.files?.[0]
                                        e.target.value = ''
                                        readSkillFile(label, employeeId, file)
                                      }} />
                                  </div>
                                  {skillForm.filename && <p className="text-[11px] break-all text-text-secondary-light dark:text-text-secondary-dark">الملف المقروء: {skillForm.filename}</p>}
                                  <label htmlFor={`${skillInputId}-content`} className={capCls}>محتوى المهارة (JSON أو Markdown أو نص عادي)</label>
                                  <textarea id={`${skillInputId}-content`} rows={4} disabled={skillsDisabled}
                                    value={skillForm.content || ''}
                                    onChange={(e) => changeSkillInput(label, employeeId, { content: e.target.value, filename: '' })}
                                    className={`${inputCls} resize-y`} />
                                  <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">{new TextEncoder().encode(skillForm.content || '').byteLength} / 32768 بايت UTF-8. يُرسل النص فقط للمعاينة، دون رفع أو تشغيل ملفات تنفيذية.</p>
                                </div>
                              )}
                            </div>
                          )}
                          <div className="flex flex-wrap gap-2">
                            <button type="button" onClick={() => generateSkill(label, employeeId)}
                              disabled={skillsDisabled || (skillMode === 'create' ? !(skillPrompt[skillKey] || '').trim() : skillForm.source === 'url' ? !(skillForm.url || '').trim() : !(skillForm.content || '').trim() || new TextEncoder().encode(skillForm.content || '').byteLength > 32768)}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium disabled:opacity-50">
                              {skillBusy[skillKey] === 'preview' ? 'جارٍ فهم المهارة...' : 'فهم المهارة ومعاينة التنفيذ'}
                            </button>
                            <button type="button" onClick={() => loadCustomSkills(label, employeeId)} disabled={skillsDisabled}
                              className="px-3 py-1.5 rounded-lg border border-border-light dark:border-border-dark text-xs text-text-secondary-light dark:text-text-secondary-dark disabled:opacity-50">
                              تحديث القائمة والجاهزية
                            </button>
                          </div>
                          {skillBusy[skillKey] && <p role="status" className="text-xs text-text-secondary-light dark:text-text-secondary-dark">{skillBusy[skillKey] === 'file' ? 'جارٍ قراءة النص...' : skillBusy[skillKey] === 'list' ? 'جارٍ تحميل مهارات الموظف...' : 'جارٍ تنفيذ الطلب لهذا الموظف...'}</p>}
                          {skillError[skillKey] && <p role="alert" className="text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap">{skillError[skillKey]}</p>}

                          {/* معاينة المهارة قبل التفعيل */}
                          {draft?.skill && (
                            <div role="region" aria-label={`معاينة مهارة للموظف @${label}`} className="rounded-lg border border-indigo-400/50 bg-indigo-50 dark:bg-indigo-900/20 p-2.5 space-y-2">
                              <div className="text-xs font-semibold text-text-primary-light dark:text-text-primary-dark">
                                {skillText(draft.skill.label)}
                              </div>
                              <p className="text-[11px] break-all text-text-secondary-light dark:text-text-secondary-dark">معاينة غير محفوظة للموظف @{label} فقط — المعرّف: {draft.employee_id}</p>
                              <pre className="text-[11px] whitespace-pre-wrap leading-relaxed text-text-secondary-light dark:text-text-secondary-dark font-sans">
                                {skillText(draft.summary)}
                              </pre>
                              <dl className="text-[11px] space-y-1 text-text-secondary-light dark:text-text-secondary-dark">
                                <div><dt className="font-medium">القدرات والإجراءات</dt><dd>{skillText(draft.skill.capabilities || draft.skill.actions) || 'راجع ملخص التنفيذ أعلاه.'}</dd></div>
                                <div><dt className="font-medium">متى تعمل</dt><dd>{skillText(draft.skill.trigger || draft.skill.triggers) || 'وفق الملخص وجدولة الموظف.'}</dd></div>
                                <div><dt className="font-medium">المتطلبات والأدوات</dt><dd>{skillText(draft.skill.required_mcp || draft.skill.requirements) || 'تُراجع وفق إعدادات الموظف المحفوظة والجاهزية أدناه.'}</dd></div>
                              </dl>
                              {skillReadiness(draft.readiness, draft.warnings)}
                              {draft.readiness?.ready === false && <p className="text-[11px] text-amber-700 dark:text-amber-300">وفّر الأدوات والصلاحيات المطلوبة واحفظ إعدادات الموظف، ثم أعد المعاينة قبل التفعيل.</p>}
                              <div className="flex flex-wrap gap-2">
                                <button type="button" onClick={() => activateSkill(label, employeeId)}
                                  disabled={skillsDisabled || draft.employee_id !== employeeId || draft.readiness?.ready === false}
                                  className="px-3 py-1.5 rounded-lg bg-green-600 hover:bg-green-700 text-white text-xs font-medium disabled:opacity-50">
                                  {skillBusy[skillKey] === 'save' ? 'جارٍ التفعيل...' : `تفعيل المهارة لـ @${label} فقط`}
                                </button>
                                <button type="button" disabled={skillsDisabled}
                                  onClick={() => changeSkillInput(label, employeeId, {})}
                                  className="px-3 py-1.5 rounded-lg border border-border-light dark:border-border-dark text-xs text-text-secondary-light dark:text-text-secondary-dark disabled:opacity-50">
                                  إلغاء المعاينة
                                </button>
                              </div>
                            </div>
                          )}

                          {/* المهارات المخصّصة الحالية */}
                          <h5 className="text-xs font-semibold text-text-primary-light dark:text-text-primary-dark">المهارات الخاصة الحالية لـ @{label}</h5>
                          {customSkills[skillKey]?.length === 0 && <p className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark">لا توجد مهارات خاصة محفوظة لهذا الموظف بعد.</p>}
                          {(customSkills[skillKey] || []).map(sk => (
                            <div key={sk.id}
                              className="rounded-lg border border-border-light dark:border-border-dark p-2 space-y-1.5">
                              <div className="flex items-center justify-between gap-2">
                                <span className={`text-xs font-medium ${sk.enabled ? 'text-text-primary-light dark:text-text-primary-dark' : 'text-text-secondary-light dark:text-text-secondary-dark line-through'}`}>
                                  {skillText(sk.label)}
                                </span>
                                <div className="flex items-center gap-1.5 shrink-0">
                                  <button type="button" onClick={() => toggleCustomSkill(label, employeeId, sk)}
                                    aria-pressed={!!sk.enabled} aria-label={`${sk.enabled ? 'إيقاف' : 'تفعيل'} مهارة ${sk.label} للموظف @${label}`}
                                    disabled={skillsDisabled || (!sk.enabled && sk.readiness?.ready === false)}
                                    className={`text-[11px] px-2 py-1 rounded-full border disabled:opacity-50 ${sk.enabled ? 'bg-primary text-white border-primary' : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark'}`}>
                                    {sk.enabled ? 'مفعّلة' : 'موقوفة'}
                                  </button>
                                  <button type="button" onClick={() => deleteCustomSkill(label, employeeId, sk)}
                                    disabled={skillsDisabled} aria-label={`حذف مهارة ${sk.label} من الموظف @${label}`}
                                    className="text-[11px] px-2 py-1 rounded-full border border-red-400/60 text-red-500 disabled:opacity-50">
                                    حذف
                                  </button>
                                </div>
                              </div>
                              <pre className="text-[10px] whitespace-pre-wrap leading-relaxed text-text-secondary-light dark:text-text-secondary-dark font-sans">
                                {skillText(sk.summary)}
                              </pre>
                              <p className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark">مرات التنفيذ اليوم: {sk.runs_today ?? 'غير متاحة'}</p>
                              {sk.source && <p className="text-[11px] break-all text-text-secondary-light dark:text-text-secondary-dark">المصدر: {skillText(sk.source.kind)}{sk.source.name ? ` — ${skillText(sk.source.name)}` : ''}</p>}
                              {skillReadiness(sk.readiness, sk.warnings)}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* ── الذاكرة طويلة المدى للموظف ── */}
                      {agent?.employee_id && settingsGroup(
                        '🧠 ذاكرة الموظف',
                        'ما قلته له أو عنه في الشات من تفضيلات وتعليمات دائمة، ودروس تقييم دوراته. يلتزم بها في كل دورة ودردشة. تُحفظ فوراً.',
                        <MemoryPanel employeeId={agent.employee_id} />
                      )}

                      {/* ── الأدوات: بحث الويب + خوادم MCP خارجية (تُحفظ فوراً) ── */}
                      {settingsGroup(
                        '🔌 الأدوات والربط الخارجي',
                        'بحث الويب قبل الكتابة، والأخبار والصور، وخوادم MCP خارجية تضيفها وتربطها بهذا الموظف. التغييرات هنا تُحفظ فوراً.',
                        <ToolsPanel employeeId={agent?.employee_id} onNotify={onNotify} />
                      )}

                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-[11px] text-green-600 dark:text-green-400 mb-1">افعل (سطر لكل بند)</label>
                          <textarea rows={2} value={pForm[label]?.dos || ''} onChange={(e) => setP(label, 'dos', e.target.value)}
                            className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark resize-y" />
                        </div>
                        <div>
                          <label className="block text-[11px] text-red-500 mb-1">لا تفعل (سطر لكل بند)</label>
                          <textarea rows={2} value={pForm[label]?.donts || ''} onChange={(e) => setP(label, 'donts', e.target.value)}
                            className="w-full text-sm px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark resize-y" />
                        </div>
                      </div>

                      {/* ── سقوف الدورة الواحدة ── */}
                      {settingsGroup(
                        'التفاعل والتنويع (سقوف الدورة الواحدة)',
                        'السقف المكتوب هنا هو السقف الفعلي: إن رفعت «إعجاب/دورة» إلى 3 ينفّذ ثلاثة إعجابات في الدورة نفسها. «إجراءات/دورة» هو الحد الكلي فوق الجميع، فارفعه أولاً قبل رفع البقية. صفر = تعطيل هذا النوع.',
                        <div className="grid grid-cols-3 gap-2">
                          {numField(label, 'max_actions_per_cycle', 'إجراءات/دورة (الحد الكلي)', 5, 1, 20)}
                          {numField(label, 'max_posts_per_cycle', 'منشورات/دورة', 1, 0, 3)}
                          {numField(label, 'max_replies_per_cycle', 'ردود/دورة', 1, 0, 10)}
                          {numField(label, 'max_retweets_per_cycle', 'رتويت/دورة', 1, 0, 10)}
                          {numField(label, 'max_likes_per_cycle', 'إعجاب/دورة', 3, 0, 20)}
                          {numField(label, 'max_follows_per_cycle', 'متابعة/دورة', 1, 0, 5)}
                          {numField(label, 'max_quotes_per_cycle', 'اقتباس مع تعليق/دورة', 1, 0, 5)}
                          {numField(label, 'max_bookmarks_per_cycle', 'حفظ مراجع/دورة', 1, 0, 5)}
                        </div>
                      )}

                      {/* ── الميزانيات اليومية ── */}
                      {settingsGroup(
                        'الميزانيات اليومية',
                        'تُوزّع الإجراءات على مدار اليوم بدل تكدّسها في أول دورة. بلوغ الهدف يوقف نوعه حتى الغد (ردود المنشنات معفاة دائماً). صفر = تعطيل.',
                        <div className="grid grid-cols-3 gap-2">
                          {numField(label, 'daily_post_target', 'نشر/يوم (الهدف)', 1, 0, 20)}
                          {numField(label, 'max_posts_per_day', 'أقصى نشر/يوم', 3, 0, 30)}
                          {numField(label, 'daily_thread_target', 'ثريد/يوم', 1, 0, 10)}
                          {numField(label, 'daily_replies_target', 'ردود/يوم', 6, 0, 60)}
                          {numField(label, 'daily_retweets_target', 'رتويت/يوم', 4, 0, 40)}
                          {numField(label, 'daily_likes_target', 'إعجاب/يوم', 12, 0, 100)}
                          {numField(label, 'daily_follows_target', 'متابعة/يوم', 3, 0, 30)}
                          {numField(label, 'daily_quotes_target', 'اقتباس/يوم', 2, 0, 20)}
                          {numField(label, 'daily_bookmarks_target', 'حفظ مراجع/يوم', 4, 0, 40)}
                        </div>
                      )}

                      {/* ── استراتيجية المحتوى: محاور وصيغ وسلاسل ── */}
                      {settingsGroup(
                        'استراتيجية المحتوى (محاور وصيغ)',
                        'الموظف يدوّر بين محاور المحتوى وصيغ العرض بترجيح ما نجح فعلاً في حسابك. اترك المحاور فارغة لتُشتق من اهتماماته تلقائياً. عطّل صيغةً لتختفي من التدوير.',
                        <>
                          <div className="flex flex-wrap gap-2">
                            {chkField(label, 'content_strategy_enabled', 'تفعيل الاستراتيجية')}
                            {chkField(label, 'allow_threads', 'السماح بالثريدات')}
                          </div>
                          <div>
                            <label className={capCls}>الصيغ المسموحة</label>
                            <div className="flex flex-wrap gap-1.5">
                              {Object.entries(FORMAT_LABELS).map(([fid, txt]) => {
                                const on = (pForm[label]?.content_formats || []).includes(fid)
                                return (
                                  <button key={fid} type="button"
                                    onClick={() => toggleInList(label, 'content_formats', fid)}
                                    className={`text-[11px] px-2 py-1 rounded-full border transition-colors ${on ? 'bg-primary text-white border-primary' : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark'}`}>
                                    {txt}
                                  </button>
                                )
                              })}
                            </div>
                          </div>
                          <div>
                            <label className={capCls}>محاور المحتوى (سطر لكل محور — فارغ = من الاهتمامات)</label>
                            <textarea rows={2} value={pForm[label]?.content_pillars || ''}
                              onChange={(e) => setP(label, 'content_pillars', e.target.value)}
                              className={`${inputCls} resize-y`} />
                          </div>
                          <div className="grid grid-cols-3 gap-2">
                            <div className="col-span-1">
                              <label className={capCls}>اسم سلسلة مستمرة (اختياري)</label>
                              <input type="text" value={pForm[label]?.content_series || ''}
                                onChange={(e) => setP(label, 'content_series', e.target.value)}
                                placeholder="مثال: قراءة الأسبوع"
                                className={inputCls} />
                            </div>
                            {numField(label, 'content_series_every_posts', 'حلقة سلسلة كل (منشور)', 3, 1, 20)}
                            {numField(label, 'strategy_exploration', 'نسبة الاستكشاف (0-1)', 0.35, 0, 1, 0.05)}
                          </div>
                        </>
                      )}

                      {/* ── الصور ── */}
                      {settingsGroup(
                        'الصور والمحتوى البصري',
                        'الأولوية لصورة حقيقية من مصدر الموضوع؛ وشعارات المصادر والأيقونات تُرفض تلقائياً. عند غيابها يولّد صورة أصلية بالنمط المختار (ثبات النمط يبني هوية بصرية). النص داخل الصورة ممنوع لأن العربية تخرج مشوّهة.',
                        <div className="grid grid-cols-3 gap-2">
                          {selField(label, 'generate_images', 'توليد الصور', IMAGE_POLICY_LABELS, 'auto')}
                          {selField(label, 'image_style', 'النمط البصري', IMAGE_STYLE_LABELS, 'editorial')}
                          {numField(label, 'max_generated_images_per_day', 'أقصى صور مولّدة/يوم', 3, 0, 20)}
                          <div className="col-span-3">
                            <label className={capCls}>إضافة لوصف الصورة (اختياري — لون/جو بصري ثابت)</label>
                            <input type="text" value={pForm[label]?.image_prompt_extra || ''}
                              onChange={(e) => setP(label, 'image_prompt_extra', e.target.value)}
                              placeholder="مثال: dark navy and gold palette"
                              className={inputCls} />
                          </div>
                        </div>
                      )}

                      {/* ── الجودة والتعلّم ── */}
                      {settingsGroup(
                        'جودة المحتوى والتعلّم',
                        'بوابة الجودة ترفض القوالب الفارغة («في عالم متسارع»، «ما رأيكم») وإعادة صياغة التوجيه وتكرار المطلع، وتعيد التوليد مرة واحدة. «قراءة الأداء» تفتح متصفحاً فلا تجعلها قصيرة جداً.',
                        <>
                          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                            {chkField(label, 'quality_gate', 'بوابة الجودة قبل النشر')}
                            {chkField(label, 'enforce_interest_scope', 'إلزام نطاق التخصص')}
                            {chkField(label, 'require_grounded_topic', 'اشتراط موضوع حقيقي')}
                            {chkField(label, 'llm_decision_review', 'مراجعة ذكية للقرار')}
                          </div>
                          <div className="grid grid-cols-3 gap-2">
                            {numField(label, 'min_post_chars', 'أدنى أحرف للمنشور', 70, 20, 260)}
                            {numField(label, 'min_relevance_score', 'أدنى درجة صلة', 1, 0, 5)}
                            {numField(label, 'goal_weight', 'وزن الأهداف (0-1)', 0.25, 0, 1, 0.05)}
                            {numField(label, 'metrics_read_every_minutes', 'قراءة الأداء كل (دقيقة)', 180, 0, 1440)}
                            {numField(label, 'observation_cache_minutes', 'صلاحية ذاكرة الرصد (دقيقة)', 60, 0, 720)}
                          </div>
                        </>
                      )}

                      <button
                        onClick={() => savePersona(label)}
                        disabled={savingP[label] || !!skillBusy[skillKey]}
                        className="w-full flex items-center justify-center gap-2 mt-1 px-3 py-2 rounded-lg bg-primary hover:bg-secondary text-white text-sm font-medium disabled:opacity-50"
                      >
                        <MdSave size={18} /> {savingP[label] ? 'جارِ الحفظ...' : 'حفظ التعليمات والبرسونا'}
                      </button>
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      </aside>

      {/* معالج إنشاء موظف ذكي جديد — 4 خطوات منظمة */}
      <EmployeeWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onNotify={onNotify}
        onCreated={load}
      />
    </>
  )
}

export default AgentsDrawer
