import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'

/**
 * أدوات الموظف: البحث على الويب + الأدوات المدمجة + خوادم MCP الخارجية.
 * كل تغيير يُحفظ فوراً عبر /api/aeos/employees/{id}/tools (لا ينتظر «حفظ البرسونا»).
 */
const inputCls = 'w-full text-xs px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark'

const parseKV = (text) => Object.fromEntries(
  (text || '').split('\n').map(l => l.trim()).filter(l => l.includes('='))
    .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
)

const emptyForm = { name: '', transport: 'stdio', command: '', args: '', env: '', url: '', headers: '', research_tool: '' }

const ToolsPanel = ({ employeeId, onNotify }) => {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [form, setForm] = useState(null)
  const [trial, setTrial] = useState({})   // server -> {tool, args, out}

  const load = useCallback(async () => {
    if (!employeeId) return
    try {
      const res = await axios.get('/api/aeos/tools', { params: { employee_id: employeeId } })
      setData(res.data)
      setError('')
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر تحميل الأدوات') }
  }, [employeeId])

  useEffect(() => { load() }, [load])

  const attach = async (server, on) => {
    setBusy(server)
    try {
      await axios.post(`/api/aeos/employees/${employeeId}/tools`, { server, attach: on })
      await load()
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر الحفظ') }
    setBusy('')
  }

  const addServer = async () => {
    setBusy('add')
    setError('')
    const body = {
      name: form.name, transport: form.transport,
      ...(form.transport === 'stdio'
        ? { command: form.command.trim(), args: form.args.split('\n').map(a => a.trim()).filter(Boolean), env: parseKV(form.env) }
        : { url: form.url.trim(), headers: parseKV(form.headers) }),
      research_tool: form.research_tool.trim(),
    }
    try {
      const res = await axios.post('/api/aeos/tools/external', body, { timeout: 90000 })
      const s = res.data?.server
      await axios.post(`/api/aeos/employees/${employeeId}/tools`, { server: s.name, attach: true })
      onNotify?.(`🔌 تمّ ربط خادم MCP «${s.label}» (${s.tools.length} أداة) بالموظف.`)
      setForm(null)
      await load()
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر الاتصال بالخادم') }
    setBusy('')
  }

  const removeServer = async (name) => {
    if (!window.confirm('حذف هذا الخادم من النظام؟ سيُفصل عن كل الموظفين.')) return
    setBusy(name)
    try { await axios.delete(`/api/aeos/tools/external/${name}`); await load() } catch (e) {
      setError(e?.response?.data?.detail || 'تعذّر الحذف')
    }
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
        { server, tool: t.tool, arguments: args }, { timeout: 60000 })
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
                title={b.available ? '' : 'أضف مفتاح مزوّد بحث في .env (TAVILY_API_KEY / BRAVE_SEARCH_API_KEY / SERPAPI_KEY)'}>
                {b.label}{!b.available ? ' — غير مُهيّأ' : ''}
              </button>
            ))}
          </div>
          {!data.search.configured && (
            <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
              البحث على الويب يعمل تلقائياً قبل كتابة كل منشور متى أضفت مفتاح Tavily أو Brave أو SerpAPI إلى ملف .env.
            </p>
          )}

          {data.external.map(s => (
            <div key={s.name} className="rounded-lg border border-border-light dark:border-border-dark p-2 space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-medium text-text-primary-light dark:text-text-primary-dark">
                  🔌 {s.label} <span className="text-text-secondary-light dark:text-text-secondary-dark">({s.transport} · {s.tools.length} أداة{s.research_tool ? ` · بحث: ${s.research_tool}` : ''})</span>
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <button type="button" disabled={busy === s.name} onClick={() => attach(s.name, !s.attached)} className={chip(s.attached)}>
                    {s.attached ? 'مربوط' : 'غير مربوط'}
                  </button>
                  <button type="button" onClick={() => removeServer(s.name)}
                    className="text-[11px] px-2 py-1 rounded-full border border-red-400/60 text-red-500">حذف</button>
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
          ))}

          {!form ? (
            <button type="button" onClick={() => setForm({ ...emptyForm })}
              className="text-xs px-3 py-1.5 rounded-lg border border-dashed border-primary/50 text-primary">
              ➕ إضافة خادم MCP خارجي
            </button>
          ) : (
            <div className="rounded-lg border border-primary/40 p-2 space-y-1.5">
              <div className="grid grid-cols-2 gap-1.5">
                <input placeholder="اسم الخادم (مثال: brave)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} />
                <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })} className={inputCls}>
                  <option value="stdio">stdio (أمر محلي)</option>
                  <option value="http">HTTP (Streamable)</option>
                </select>
              </div>
              {form.transport === 'stdio' ? (
                <>
                  <input placeholder="الأمر: npx أو python أو مسار ملف تنفيذي" value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} className={inputCls} dir="ltr" />
                  <textarea rows={2} placeholder={'الوسائط (سطر لكل وسيط)\n-y\n@modelcontextprotocol/server-brave-search'} value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })} className={inputCls} dir="ltr" />
                  <textarea rows={2} placeholder="متغيرات البيئة السرية (KEY=value سطر لكل متغير)" value={form.env} onChange={(e) => setForm({ ...form, env: e.target.value })} className={inputCls} dir="ltr" />
                </>
              ) : (
                <>
                  <input placeholder="https://example.com/mcp" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} className={inputCls} dir="ltr" />
                  <textarea rows={2} placeholder="ترويسات سرية (Authorization=Bearer ...)" value={form.headers} onChange={(e) => setForm({ ...form, headers: e.target.value })} className={inputCls} dir="ltr" />
                </>
              )}
              <input placeholder="أداة البحث قبل الكتابة (اختياري، اسم الأداة)" value={form.research_tool} onChange={(e) => setForm({ ...form, research_tool: e.target.value })} className={inputCls} dir="ltr" />
              <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
                يتصل النظام بالخادم فعلاً ويكتشف أدواته قبل الحفظ. الأسرار تُحفظ على الخادم ولا تُعرض مرة أخرى.
                الموظف لا يستدعي أدوات الخادم من تلقاء نفسه إلا «أداة البحث» التي تحدّدها، وبنص الموضوع فقط.
              </p>
              <div className="flex gap-2">
                <button type="button" onClick={addServer} disabled={busy === 'add' || !form.name}
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
