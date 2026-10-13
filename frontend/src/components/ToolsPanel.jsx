import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import authHeaders from '../authHeaders'

/**
 * أدوات الموظف على مستويين:
 * 1) **أدوات النظام**: خوادم ثبّتها المطوّر (لوحة التحكم) وأتاحها للموظفين — المالك يربط ويفصل.
 * 2) **أدوات هذا الموظف الخاصة**: خوادم MCP يضيفها المالك لموظفه وحده (HTTP عام فقط —
 *    stdio والحزم تشغّل كوداً على الجهاز فهي أدوات نظام للمطوّر).
 */
const inputCls = 'w-full text-xs px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark'
const parseKV = (text) => Object.fromEntries(
  (text || '').split('\n').map(l => l.trim()).filter(l => l.includes('='))
    .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
)
const emptyForm = { name: '', url: '', headers: '', description: '', research_tool: '' }

const ToolsPanel = ({ employeeId, onNotify }) => {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [trial, setTrial] = useState({})   // server -> {tool, args, out}
  const [form, setForm] = useState(null)
  const fileRef = useRef(null)

  const addPrivate = async () => {
    setBusy('add'); setError('')
    try {
      const res = await axios.post(`/api/aeos/employees/${employeeId}/tools/private`, {
        name: form.name.trim(), url: form.url.trim(), headers: parseKV(form.headers),
        description: form.description.trim(), research_tool: form.research_tool.trim(),
      }, { headers: authHeaders(), timeout: 90000 })
      onNotify?.(`🔌 أُضيفت أداة «${res.data.server.label}» (${res.data.server.tools.length} أداة) لهذا الموظف فقط.`)
      setForm(null)
      await load()
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر الاتصال بالخادم') }
    setBusy('')
  }

  const uploadPrivate = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    const fd = new FormData()
    fd.append('file', file)
    setBusy('upload'); setError('')
    try {
      const res = await axios.post(`/api/aeos/employees/${employeeId}/tools/private/upload`, fd,
        { headers: authHeaders(), timeout: 120000 })
      const bad = res.data.errors.map(e => `${e.name}: ${e.error}`).join('؛ ')
      onNotify?.(`🔌 أُضيف ${res.data.saved.length} خادم لهذا الموظف${bad ? ` — تعذّر: ${bad}` : ''}`)
      fileRef.current.value = ''
      await load()
    } catch (e) { setError(e?.response?.data?.detail || 'فشل الرفع') }
    setBusy('')
  }

  const removePrivate = async (s) => {
    if (!window.confirm(`حذف الأداة الخاصة «${s.label}» من هذا الموظف؟`)) return
    setBusy(s.name)
    try { await axios.delete(`/api/aeos/employees/${employeeId}/tools/private/${s.name}`, { headers: authHeaders() }); await load() }
    catch (e) { setError(e?.response?.data?.detail || 'تعذّر الحذف') }
    setBusy('')
  }

  const load = useCallback(async () => {
    if (!employeeId) return
    try {
      const res = await axios.get('/api/aeos/tools', { headers: authHeaders(), params: { employee_id: employeeId } })
      setData(res.data)
      setError('')
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر تحميل الأدوات') }
  }, [employeeId])

  useEffect(() => { load() }, [load])

  const attach = async (server, on) => {
    setBusy(server)
    try {
      await axios.post(`/api/aeos/employees/${employeeId}/tools`, { server, attach: on }, { headers: authHeaders() })
      await load()
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر الحفظ') }
    setBusy('')
  }

  const runTrial = async (server) => {
    const t = trial[server] || {}
    let args = {}
    try { args = t.args ? JSON.parse(t.args) : {} } catch {
      setTrial(p => ({ ...p, [server]: { ...t, out: '❌ الوسائط يجب أن تكون JSON صالحاً' } })); return
    }
    setBusy(`call:${server}`)
    try {
      const res = await axios.post(`/api/aeos/employees/${employeeId}/tools/call`,
        { server, tool: t.tool, arguments: args }, { headers: authHeaders(), timeout: 60000 })
      const r = res.data?.result || {}
      setTrial(p => ({ ...p, [server]: { ...t, out: r.text || JSON.stringify(r.results || r, null, 2) } }))
    } catch (e) {
      setTrial(p => ({ ...p, [server]: { ...t, out: `❌ ${e?.response?.data?.detail || 'فشل'}` } }))
    }
    setBusy('')
  }

  if (!employeeId) return null
  const chip = (on) => `text-xs px-2.5 py-1 rounded-full border transition-colors disabled:opacity-50 ${on ? 'bg-primary text-white border-primary' : 'border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark'}`

  return (
    <div className="space-y-2">
      {error && <div className="text-[11px] text-red-500">{error}</div>}
      {!data ? <div className="text-[11px] text-text-secondary-light">جارٍ التحميل…</div> : (
        <>
          <div className="flex flex-wrap gap-1.5">
            {data.builtin.map(b => (
              <button key={b.name} type="button" disabled={!b.available || busy === b.name}
                onClick={() => attach(b.name, !b.attached)} className={chip(b.attached)}
                title={b.available ? '' : 'أضف TAVILY_API_KEY في .env'}>
                {b.label}{!b.available ? ' — غير مُهيّأ' : ''}
              </button>
            ))}
          </div>
          {!data.search.configured && (
            <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
              البحث على الويب يعمل تلقائياً قبل كتابة كل منشور متى أضفت مفتاح Tavily (TAVILY_API_KEY) إلى ملف .env.
            </p>
          )}

          <div className="text-[11px] font-semibold text-text-primary-light dark:text-text-primary-dark pt-1">🧩 أدوات النظام</div>
          {data.external.length === 0 && (
            <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
              لا توجد خوادم نظام متاحة للموظفين بعد. يثبّتها المطوّر من لوحة التحكم.
            </p>
          )}
          {[...data.external, ...(data.private || []).map(p => ({ ...p, _private: true }))].map((s, i, all) => (
            <div key={s.name}>
            {s._private && !all[i - 1]?._private && (
              <div className="text-[11px] font-semibold text-text-primary-light dark:text-text-primary-dark pt-2 pb-1">🔒 أدوات هذا الموظف الخاصة</div>
            )}
            <div key={s.name} className="rounded-lg border border-border-light dark:border-border-dark p-2 space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-medium text-text-primary-light dark:text-text-primary-dark">
                  🔌 {s.label} <span className="text-text-secondary-light dark:text-text-secondary-dark">({s.transport} · {s.tools.length} أداة{s.research_tool ? ` · بحث: ${s.research_tool}` : ''})</span>
                  {s.description && <div className="text-[10px] font-normal text-text-secondary-light dark:text-text-secondary-dark">{s.description}</div>}
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <button type="button" disabled={busy === s.name} onClick={() => attach(s.name, !s.attached)} className={chip(s.attached)}>
                    {s.attached ? 'مربوط' : 'غير مربوط'}
                  </button>
                  {s._private && (
                    <button type="button" disabled={busy === s.name} onClick={() => removePrivate(s)}
                      className="text-[11px] px-2 py-1 rounded-full border border-red-400/60 text-red-500">حذف</button>
                  )}
                </div>
              </div>
              <div className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
                {s.tools.map(t => t.name).join(' · ')}
              </div>
              {s.attached && (
                <div className="flex flex-wrap gap-1.5 items-start">
                  <select value={trial[s.name]?.tool || ''} onChange={(e) => setTrial(p => ({ ...p, [s.name]: { ...p[s.name], tool: e.target.value } }))}
                    className="text-xs px-2 py-1 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark">
                    <option value="">جرّب أداة…</option>
                    {s.tools.map(t => <option key={t.name} value={t.name}>{t.name}</option>)}
                  </select>
                  <input placeholder='{"query": "..."}' value={trial[s.name]?.args || ''}
                    onChange={(e) => setTrial(p => ({ ...p, [s.name]: { ...p[s.name], args: e.target.value } }))}
                    className="flex-1 min-w-[140px] text-xs px-2 py-1 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" dir="ltr" />
                  <button type="button" disabled={!trial[s.name]?.tool || busy === `call:${s.name}`} onClick={() => runTrial(s.name)}
                    className="text-xs px-2.5 py-1 rounded-lg bg-indigo-600 text-white disabled:opacity-50">تشغيل</button>
                  {trial[s.name]?.out && (
                    <pre className="w-full text-[10px] whitespace-pre-wrap max-h-40 overflow-auto rounded bg-gray-50 dark:bg-gray-800 p-1.5 text-text-secondary-light dark:text-text-secondary-dark" dir="auto">{trial[s.name].out}</pre>
                  )}
                </div>
              )}
            </div>
            </div>
          ))}

          {!(data.private || []).length && (
            <div className="text-[11px] font-semibold text-text-primary-light dark:text-text-primary-dark pt-2">🔒 أدوات هذا الموظف الخاصة</div>
          )}
          {!form ? (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => setForm({ ...emptyForm })}
                className="text-xs px-3 py-1.5 rounded-lg border border-dashed border-primary/50 text-primary">➕ إضافة أداة MCP لهذا الموظف</button>
              <label className="text-xs px-3 py-1.5 rounded-lg border border-dashed border-border-light dark:border-border-dark text-text-secondary-light dark:text-text-secondary-dark cursor-pointer">
                {busy === 'upload' ? 'جارٍ الاتصال…' : '📄 رفع ملف إعداد JSON'}
                <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={uploadPrivate} />
              </label>
            </div>
          ) : (
            <div className="rounded-lg border border-primary/40 p-2 space-y-1.5">
              <div className="grid grid-cols-2 gap-1.5">
                <input placeholder="اسم الأداة (مثال: weather)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} />
                <input placeholder="https://example.com/mcp" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} className={inputCls} dir="ltr" />
              </div>
              <input placeholder="وصف مختصر (اختياري)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={inputCls} />
              <textarea rows={2} placeholder="ترويسات سرية (Authorization=Bearer ... سطر لكل ترويسة)" value={form.headers} onChange={(e) => setForm({ ...form, headers: e.target.value })} className={inputCls} dir="ltr" />
              <input placeholder="أداة البحث قبل الكتابة (اختياري، اسم الأداة)" value={form.research_tool} onChange={(e) => setForm({ ...form, research_tool: e.target.value })} className={inputCls} dir="ltr" />
              <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
                خادم MCP عام عبر HTTP، لهذا الموظف وحده. يتصل النظام به ويكتشف أدواته قبل الحفظ، والأسرار لا تُعرض مرة أخرى.
                العناوين الداخلية (هذا الجهاز أو الشبكة المحلية) وخوادم stdio غير مسموحة هنا — يثبّتها المطوّر كأدوات للنظام.
              </p>
              <div className="flex gap-2">
                <button type="button" onClick={addPrivate} disabled={busy === 'add' || !form.name || !form.url}
                  className="px-3 py-1.5 rounded-lg bg-green-600 hover:bg-green-700 text-white text-xs font-medium disabled:opacity-50">
                  {busy === 'add' ? 'جارٍ الاتصال…' : 'اتصال وحفظ'}
                </button>
                <button type="button" onClick={() => setForm(null)}
                  className="px-3 py-1.5 rounded-lg border border-border-light dark:border-border-dark text-xs text-text-secondary-light dark:text-text-secondary-dark">إلغاء</button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default ToolsPanel
