import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import authHeaders from '../authHeaders'

// منتقي نموذج الشات: نماذج المزوّدين الذين ربطهم المستخدم (من الإعدادات ← مزوّد النماذج).
// الاختيار يُحفظ في إعداداته (نموذج «الشات») فيسري على الرسالة التالية للمنسّق مباشرة.
export default function ChatModelPicker() {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(false)
  const [models, setModels] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ref = useRef(null)
  const navigate = useNavigate()

  const load = () => axios.get('/api/llm/providers', { headers: authHeaders() })
    .then((r) => setData(r.data)).catch(() => {})

  useEffect(() => { load() }, [])
  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const connected = (data?.providers || []).filter((p) => p.connected)

  const openMenu = async () => {
    setOpen((o) => !o)
    setError('')
    await load()
    for (const p of connected) {
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
      await axios.put('/api/llm/selection/chat', { provider, model }, { headers: authHeaders() })
      await load()
      setOpen(false)
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر تغيير النموذج') }
    setBusy(false)
  }

  if (!data) return null
  const current = data.chat?.provider ? data.chat.model : 'افتراضي النظام'
  const currentLabel = data.chat?.provider ? (data.providers.find((p) => p.id === data.chat.provider)?.label || '') : 'OpenRouter'

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={openMenu} disabled={busy}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-border-light dark:border-border-dark hover:bg-gray-50 dark:hover:bg-card-dark text-xs text-text-primary-light dark:text-text-primary-dark max-w-[220px]"
        title={`نموذج الشات: ${currentLabel} — ${current}`}>
        <span>🧠</span>
        <span className="truncate font-medium" dir="ltr">{current}</span>
        <span className="opacity-60">▾</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-2 w-80 max-h-96 overflow-y-auto rounded-xl border border-border-light dark:border-border-dark bg-card-light dark:bg-card-dark shadow-xl p-2 text-sm" style={{ insetInlineStart: 0 }}>
          <button type="button" onClick={() => choose('', '')}
            className={`w-full text-start px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-background-dark ${!data.chat?.provider ? 'font-bold text-primary' : ''}`}>
            افتراضي النظام (OpenRouter)
          </button>
          {connected.map((p) => (
            <div key={p.id} className="mt-1">
              <p className="px-3 pt-2 pb-1 text-[11px] font-semibold text-text-secondary-light dark:text-text-secondary-dark">{p.label}</p>
              {models[p.id] === undefined && <p className="px-3 py-1 text-xs opacity-60">جارٍ تحميل النماذج…</p>}
              {models[p.id]?.length === 0 && <p className="px-3 py-1 text-xs text-red-500">تعذّر جلب النماذج — اختبر المفتاح من الإعدادات</p>}
              {(models[p.id] || []).map((m) => {
                const active = data.chat?.provider === p.id && data.chat?.model === m
                return (
                  <button key={m} type="button" onClick={() => choose(p.id, m)} dir="ltr"
                    className={`w-full text-left px-3 py-1.5 rounded-lg text-xs hover:bg-gray-50 dark:hover:bg-background-dark ${active ? 'font-bold text-primary' : 'text-text-primary-light dark:text-text-primary-dark'}`}>
                    {active ? '✓ ' : ''}{m}
                  </button>
                )
              })}
            </div>
          ))}
          {connected.filter((p) => p.id !== 'ollama').length === 0 && (
            <p className="px-3 py-2 text-xs text-text-secondary-light dark:text-text-secondary-dark">
              لم تربط مزوّداً بمفتاحك بعد.
            </p>
          )}
          {error && <p className="px-3 py-2 text-xs text-red-500">{error}</p>}
          <button type="button" onClick={() => navigate('/profile#model-provider')}
            className="w-full mt-2 px-3 py-2 rounded-lg border border-primary/30 text-primary text-xs hover:bg-primary/5">
            ⚙️ إدارة مزوّدي النماذج
          </button>
        </div>
      )}
    </div>
  )
}
