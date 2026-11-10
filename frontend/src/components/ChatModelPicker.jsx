import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { Brain, ChevronDown, Settings2 } from 'lucide-react'
import GlassIcon from './ui/GlassIcon'
import authHeaders from '../authHeaders'

// منتقي نموذج الشات من رأس الشات نفسه:
// - OpenRouter: قائمة OpenRouter نفسها (الداعمة للأدوات) مع بحث وسعر وسياق — بمفتاح المستخدم، أو على
//   رصيد النظام للمدير والمطوّر.
// - بقية المزوّدين الذين ربطهم المستخدم بمفاتيحه (من الإعدادات ← مزوّد النماذج).
// الاختيار يُحفظ كنموذج «الشات» فيسري على الرسالة التالية للمنسّق مباشرة.
const price = (v) => (v === null || v === undefined ? '—' : v === 0 ? '0' : v < 0.01 ? v.toFixed(3) : v < 10 ? v.toFixed(2) : v.toFixed(0))
const ctx = (n) => (!n ? '' : n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M` : `${Math.round(n / 1000)}K`)

// employeeId (اختياري): المنتقي نفسه في بطاقة الموظف — يحفظ نموذج هذا الموظف وحده
// (business_rules.llm) بدل نموذج الشات، و«الافتراضي» فيه = نموذج الموظفين العام من الإعدادات.
export default function ChatModelPicker({ employeeId = null, onChanged = null }) {
  const [data, setData] = useState(null)
  const [emp, setEmp] = useState(null)            // اختيار الموظف + ما يعمل به فعلاً
  const [open, setOpen] = useState(false)
  const [models, setModels] = useState({})        // مزوّدون آخرون: provider -> [ids]
  const [orList, setOrList] = useState(null)      // قائمة OpenRouter الغنية
  const [orInfo, setOrInfo] = useState({})
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ref = useRef(null)
  const searchRef = useRef(null)
  const navigate = useNavigate()

  const load = () => {
    if (employeeId) {
      axios.get(`/api/aeos/employees/${employeeId}/llm`, { headers: authHeaders() })
        .then((r) => setEmp(r.data)).catch(() => {})
    }
    return axios.get('/api/llm/providers', { headers: authHeaders() })
      .then((r) => { setData(r.data); return r.data }).catch(() => null)
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [employeeId])
  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const connected = (data?.providers || []).filter((p) => p.connected)
  const orConnected = connected.some((p) => p.id === 'openrouter')
  const others = connected.filter((p) => p.id !== 'openrouter')

  const openMenu = async () => {
    const next = !open
    setOpen(next)
    setError('')
    if (!next) return
    setTimeout(() => searchRef.current?.focus(), 50)
    const d = await load()
    const conn = (d?.providers || []).filter((p) => p.connected)
    if (conn.some((p) => p.id === 'openrouter') && orList === null) {
      try {
        const r = await axios.get('/api/llm/openrouter/models', { headers: authHeaders(), timeout: 30000 })
        setOrList(r.data.models || [])
        setOrInfo({ viaSystem: r.data.via_system, ownKey: r.data.has_own_key })
      } catch (e) { setOrList([]); setError(e?.response?.data?.detail || 'تعذّر جلب قائمة OpenRouter') }
    }
    for (const p of conn.filter((x) => x.id !== 'openrouter')) {
      if (models[p.id]) continue
      try {
        const r = await axios.get(`/api/llm/providers/${p.id}/models`, { headers: authHeaders(), timeout: 30000 })
        setModels((m) => ({ ...m, [p.id]: r.data.models }))
      } catch { setModels((m) => ({ ...m, [p.id]: [] })) }
    }
  }

  const choose = async (provider, model) => {
    setBusy(true)
    setError('')
    try {
      const url = employeeId ? `/api/aeos/employees/${employeeId}/llm` : '/api/llm/selection/chat'
      const r = await axios.put(url, { provider, model }, { headers: authHeaders() })
      if (employeeId) setEmp(r.data)
      await load()
      setOpen(false)
      setQuery('')
      onChanged?.(provider ? model : '')
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر تغيير النموذج') }
    setBusy(false)
  }

  const q = query.trim().toLowerCase()
  const words = q.split(/[\s/:_\-.]+/).filter(Boolean)
  const match = (text) => words.every((w) => text.toLowerCase().includes(w))
  const orShown = useMemo(() => (orList || []).filter((m) => match(`${m.id} ${m.name}`)).slice(0, q ? 80 : 40),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [orList, q])

  if (!data || (employeeId && !emp)) return null
  const sel = employeeId ? (emp.selection || {}) : (data.chat || {})
  const defaultLabel = employeeId
    ? `يتبع نموذج الموظفين العام (${emp.owner_default?.model || 'نموذج النظام'})`
    : 'نموذج النظام (OpenRouter)'
  const current = employeeId ? (emp.effective?.model || '') : (sel.provider ? sel.model : 'نموذج النظام')
  const currentLabel = sel.provider ? (data.providers.find((p) => p.id === sel.provider)?.label || '')
    : employeeId ? (emp.source === 'owner' ? 'نموذج الموظفين العام' : 'نموذج النظام') : 'OpenRouter'
  const item = 'w-full text-left px-3 py-1.5 rounded-lg text-xs hover:bg-gray-50 dark:hover:bg-background-dark'
  const muted = 'text-[10px] text-text-secondary-light dark:text-text-secondary-dark'

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={openMenu} disabled={busy}
        className="moj-pill moj-hover flex items-center gap-2 h-10 px-3 rounded-xl text-sm font-medium text-text-primary-light dark:text-text-primary-dark max-w-[240px] transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-1"
        title={`${employeeId ? 'نموذج هذا الوكيل' : 'نموذج النظام'}: ${currentLabel} — ${current}`}>
        <GlassIcon size="sm"><Brain size={14} /></GlassIcon>
        <span className="truncate" dir="ltr">{current}</span>
        {employeeId && !sel.provider && <span className="shrink-0 text-[10px] opacity-60">(عام)</span>}
        <ChevronDown size={14} className="shrink-0 opacity-60" aria-hidden />
      </button>
      {open && (
        <div className="moj-glass-strong absolute z-50 mt-2 w-[22rem] max-h-[28rem] flex flex-col rounded-2xl text-sm" style={{ insetInlineStart: 0 }}>
          <div className="p-2 border-b border-border-light dark:border-border-dark">
            <input ref={searchRef} value={query} onChange={(e) => setQuery(e.target.value)} dir="ltr"
              placeholder="ابحث: claude، gemini flash، gpt-4o…"
              className="w-full px-3 py-2 rounded-lg text-xs bg-background-light dark:bg-background-dark border border-border-light dark:border-border-dark text-text-primary-light dark:text-text-primary-dark focus:outline-none focus:ring-2 focus:ring-primary" />
          </div>
          <div className="overflow-y-auto p-2">
            {!q && (
              <button type="button" onClick={() => choose('', '')}
                className={`w-full text-start px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-background-dark ${!sel.provider ? 'font-bold text-primary' : ''}`}>
                {!sel.provider ? '✓ ' : ''}{defaultLabel}
              </button>
            )}

            {orConnected && (
              <div className="mt-1">
                <p className="px-3 pt-2 pb-1 text-[11px] font-semibold text-text-secondary-light dark:text-text-secondary-dark flex items-center justify-between gap-2">
                  <span>OpenRouter {orList ? `· ${orList.length} نموذجاً يدعم الأدوات` : ''}</span>
                  {orInfo.viaSystem && <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">على رصيد النظام</span>}
                </p>
                {orList === null && <p className="px-3 py-1 text-xs opacity-60">جارٍ تحميل قائمة OpenRouter…</p>}
                {orList && orShown.length === 0 && <p className="px-3 py-1 text-xs opacity-60">لا نتائج لـ «{query}»</p>}
                {orShown.map((m) => {
                  const active = sel.provider === 'openrouter' && sel.model === m.id
                  return (
                    <button key={m.id} type="button" onClick={() => choose('openrouter', m.id)} dir="ltr" disabled={busy}
                      className={`${item} ${active ? 'bg-primary/5' : ''}`}>
                      <div className={`flex items-center justify-between gap-2 ${active ? 'font-bold text-primary' : 'text-text-primary-light dark:text-text-primary-dark'}`}>
                        <span className="truncate">{active ? '✓ ' : ''}{m.name}</span>
                        {m.free
                          ? <span className="shrink-0 text-[10px] px-1.5 rounded bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">مجاني</span>
                          : <span className={`shrink-0 ${muted}`}>${price(m.price_in)} / ${price(m.price_out)}</span>}
                      </div>
                      <div className={`flex items-center justify-between gap-2 ${muted}`}>
                        <span className="truncate">{m.id}</span>
                        <span className="shrink-0">{ctx(m.context)}</span>
                      </div>
                    </button>
                  )
                })}
                {orList && orShown.length > 0 && <p className={`px-3 pt-1 ${muted}`}>السعر بالدولار لكل مليون رمز (إدخال / إخراج)</p>}
              </div>
            )}

            {others.map((p) => {
              const list = (models[p.id] || []).filter((m) => match(m))
              return (
                <div key={p.id} className="mt-2">
                  <p className="px-3 pt-2 pb-1 text-[11px] font-semibold text-text-secondary-light dark:text-text-secondary-dark">{p.label}</p>
                  {models[p.id] === undefined && <p className="px-3 py-1 text-xs opacity-60">جارٍ تحميل النماذج…</p>}
                  {models[p.id]?.length === 0 && <p className="px-3 py-1 text-xs text-red-500">تعذّر جلب النماذج — اختبر المفتاح من الإعدادات</p>}
                  {list.map((m) => {
                    const active = sel.provider === p.id && sel.model === m
                    return (
                      <button key={m} type="button" onClick={() => choose(p.id, m)} dir="ltr" disabled={busy}
                        className={`${item} ${active ? 'font-bold text-primary' : 'text-text-primary-light dark:text-text-primary-dark'}`}>
                        {active ? '✓ ' : ''}{m}
                      </button>
                    )
                  })}
                </div>
              )
            })}

            {!orConnected && others.filter((p) => p.id !== 'ollama').length === 0 && (
              <p className="px-3 py-2 text-xs text-text-secondary-light dark:text-text-secondary-dark">
                لم تربط مزوّداً بمفتاحك بعد. اربط مفتاح OpenRouter لتختار من كل نماذجه.
              </p>
            )}
            {error && <p className="px-3 py-2 text-xs text-red-500">{error}</p>}
          </div>
          <div className="p-2 border-t border-border-light dark:border-border-dark">
            <button type="button" onClick={() => navigate('/profile#model-provider')}
              className="moj-hover w-full px-3 py-2 rounded-lg border border-primary/30 text-primary text-xs hover:bg-primary/5 flex items-center justify-center gap-1.5">
              <Settings2 size={14} aria-hidden /> إدارة مزوّدي النماذج
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
