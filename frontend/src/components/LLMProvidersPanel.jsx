import { useEffect, useState } from 'react'
import axios from 'axios'
import authHeaders from '../authHeaders'

// قسم «مزوّد النماذج» في الإعدادات: ربط المزوّدين بمفاتيح المستخدم (تُحفظ مشفّرة ولا تُعاد)،
// واختيار نموذج الشات ونموذج الموظفين بشكل منفصل. بلا اختيار ← إعداد النظام (OpenRouter).
const card = 'bg-card-light dark:bg-card-dark rounded-xl p-6 shadow-sm'
const input = 'w-full px-3 py-2 bg-background-light dark:bg-background-dark border border-border-light dark:border-border-dark rounded-lg text-sm text-text-primary-light dark:text-text-primary-dark focus:outline-none focus:ring-2 focus:ring-primary'
const btn = 'px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50'
const muted = 'text-xs text-text-secondary-light dark:text-text-secondary-dark'

const PURPOSES = [
  ['chat', '💬 نموذج الشات', 'المنسّق العام ومحادثاتك مع الموظفين في الشات'],
  ['employee', 'نموذج الموظفين', 'دورات الموظفين الآلية: الكتابة والقرار والتقييم'],
]

export default function LLMProvidersPanel() {
  const [data, setData] = useState(null)
  const [keys, setKeys] = useState({})          // المفتاح المكتوب الآن (لا يُعرض المحفوظ أبداً)
  const [models, setModels] = useState({})      // provider -> [models]
  const [sel, setSel] = useState({ chat: { provider: '', model: '' }, employee: { provider: '', model: '' } })
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)

  const notify = (text, ok = true) => { setMsg({ text, ok }); setTimeout(() => setMsg(null), 6000) }
  const errText = (e) => e?.response?.data?.detail || 'تعذّر تنفيذ الطلب'

  const load = async () => {
    const res = await axios.get('/api/llm/providers', { headers: authHeaders() })
    setData(res.data)
    setSel({ chat: { ...res.data.chat }, employee: { ...res.data.employee } })
    return res.data
  }

  const loadModels = async (provider) => {
    if (!provider || models[provider]) return
    try {
      const res = await axios.get(`/api/llm/providers/${provider}/models`, { headers: authHeaders(), timeout: 30000 })
      setModels((m) => ({ ...m, [provider]: res.data.models }))
    } catch (e) { notify(errText(e), false) }
  }

  useEffect(() => {
    load().then((d) => {
      ;[d.chat.provider, d.employee.provider].filter(Boolean).forEach(loadModels)
    }).catch(() => notify('تعذّر تحميل إعدادات المزوّد', false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const saveKey = async (provider, remove = false) => {
    setBusy(`key:${provider}`)
    try {
      const res = await axios.put(`/api/llm/providers/${provider}/key`, { api_key: remove ? '' : (keys[provider] || '').trim() },
        { headers: authHeaders() })
      setData(res.data)
      setSel({ chat: { ...res.data.chat }, employee: { ...res.data.employee } })
      setKeys((k) => ({ ...k, [provider]: '' }))
      setModels((m) => { const n = { ...m }; delete n[provider]; return n })
      if (!remove) { await loadModels(provider); notify('✅ حُفظ المفتاح مشفّراً — اختبره ثم اختر نموذجاً') }
      else notify('حُذف المفتاح، وأي اختيار كان يعتمد عليه رجع لإعداد النظام')
    } catch (e) { notify(errText(e), false) }
    setBusy('')
  }

  const test = async (provider) => {
    setBusy(`test:${provider}`)
    const model = sel.chat.provider === provider ? sel.chat.model : sel.employee.provider === provider ? sel.employee.model : ''
    try {
      const res = await axios.post(`/api/llm/providers/${provider}/test`, { model }, { headers: authHeaders(), timeout: 90000 })
      notify(`✅ يعمل — النموذج ${res.data.model} ردّ فعلاً`)
    } catch (e) { notify(`❌ ${errText(e)}`, false) }
    await load().catch(() => {})
    setBusy('')
  }

  const saveSelection = async (purpose) => {
    setBusy(`sel:${purpose}`)
    try {
      const res = await axios.put(`/api/llm/selection/${purpose}`, sel[purpose], { headers: authHeaders() })
      setSel((s) => ({ ...s, [purpose]: { provider: res.data.provider, model: res.data.model } }))
      notify(res.data.provider ? `✅ ${purpose === 'chat' ? 'الشات' : 'الموظفون'}: ${res.data.model}` : '✅ رجع لإعداد النظام (OpenRouter)')
      await load().catch(() => {})
    } catch (e) { notify(errText(e), false) }
    setBusy('')
  }

  if (!data) return <div className={card}><p className={muted}>جارٍ تحميل مزوّد النماذج…</p></div>

  const connected = data.providers.filter((p) => p.connected)

  return (
    <div className={card}>
      <h2 className="text-xl font-semibold text-text-primary-light dark:text-text-primary-dark mb-1">🧠 مزوّد النماذج</h2>
      <p className={`${muted} mb-5`}>
        اربط مزوّدك بمفتاحك (يُحفظ مشفّراً ولا يظهر مرة أخرى)، ثم اختر نموذجاً للشات ونموذجاً للموظفين.
        بلا اختيار يُستخدم إعداد النظام (OpenRouter).
      </p>

      {msg && (
        <div className={`mb-4 px-3 py-2 rounded-lg text-sm ${msg.ok ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400' : 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400'}`}>
          {msg.text}
        </div>
      )}

      {/* الاختيار: الشات والموظفون */}
      <div className="grid md:grid-cols-2 gap-4 mb-6">
        {PURPOSES.map(([purpose, title, hint]) => {
          const s = sel[purpose]
          const list = models[s.provider] || []
          return (
            <div key={purpose} className="rounded-lg border border-border-light dark:border-border-dark p-4 space-y-2">
              <p className="font-semibold text-sm text-text-primary-light dark:text-text-primary-dark">{title}</p>
              <p className={muted}>{hint}</p>
              <select value={s.provider} className={input}
                onChange={(e) => { const p = e.target.value; setSel((x) => ({ ...x, [purpose]: { provider: p, model: '' } })); loadModels(p) }}>
                <option value="">إعداد النظام (OpenRouter)</option>
                {connected.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
              {s.provider && (
                <>
                  <input list={`models-${purpose}`} value={s.model} dir="ltr" className={input}
                    placeholder={list.length ? 'اختر أو اكتب اسم النموذج' : 'جارٍ تحميل النماذج…'}
                    onChange={(e) => setSel((x) => ({ ...x, [purpose]: { ...x[purpose], model: e.target.value } }))} />
                  <datalist id={`models-${purpose}`}>{list.map((m) => <option key={m} value={m} />)}</datalist>
                  {list.length > 0 && <p className={muted}>{list.length} نموذجاً متاحاً بمفتاحك</p>}
                </>
              )}
              <button type="button" onClick={() => saveSelection(purpose)} disabled={busy === `sel:${purpose}` || (s.provider && !s.model)}
                className={`${btn} bg-primary hover:bg-secondary text-white`}>
                {busy === `sel:${purpose}` ? 'جارٍ الحفظ…' : 'حفظ الاختيار'}
              </button>
            </div>
          )
        })}
      </div>

      {/* المزوّدون */}
      <div className="grid md:grid-cols-2 gap-4">
        {data.providers.map((p) => (
          <div key={p.id} className="rounded-lg border border-border-light dark:border-border-dark p-4 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <p className="font-semibold text-sm text-text-primary-light dark:text-text-primary-dark">{p.label}</p>
              <span className={`text-[11px] px-2 py-0.5 rounded-full ${p.connected ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-500 dark:bg-gray-800'}`}>
                {p.connected ? (p.has_key ? `مربوط ••••${p.key_last4}` : p.via_system ? 'رصيد النظام — كل نماذجه' : 'متاح') : p.id === 'openrouter' ? 'إعداد النظام' : 'غير مربوط'}
              </span>
            </div>
            {p.note && <p className={muted}>{p.note}</p>}
            {p.last_test && (
              <p className={`text-[11px] ${p.last_test.ok ? 'text-green-600 dark:text-green-400' : 'text-red-500'}`}>
                آخر اختبار: {p.last_test.ok ? `يعمل (${p.last_test.model})` : p.last_test.error}
              </p>
            )}
            {p.needs_key && (
              <div className="flex gap-2">
                <input type="password" autoComplete="new-password" dir="ltr" className={input} placeholder={p.has_key ? 'مفتاح جديد لاستبدال المحفوظ' : p.key_hint || 'API key'}
                  value={keys[p.id] || ''} onChange={(e) => setKeys((k) => ({ ...k, [p.id]: e.target.value }))} data-lpignore="true" data-1p-ignore="true" />
                <button type="button" onClick={() => saveKey(p.id)} disabled={!(keys[p.id] || '').trim() || busy === `key:${p.id}`}
                  className={`${btn} bg-primary hover:bg-secondary text-white shrink-0`}>حفظ</button>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {p.connected && (
                <button type="button" onClick={() => test(p.id)} disabled={busy === `test:${p.id}`}
                  className={`${btn} bg-indigo-600 hover:bg-indigo-700 text-white`}>{busy === `test:${p.id}` ? 'جارٍ الاختبار…' : 'اختبار الاتصال'}</button>
              )}
              {p.has_key && (
                <button type="button" onClick={() => saveKey(p.id, true)} disabled={busy === `key:${p.id}`}
                  className={`${btn} bg-red-600 hover:bg-red-700 text-white`}>حذف المفتاح</button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
