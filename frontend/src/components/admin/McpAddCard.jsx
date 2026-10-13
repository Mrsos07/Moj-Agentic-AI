import { useRef, useState } from 'react'
import axios from 'axios'
import authHeaders from '../../authHeaders'

/**
 * إضافة خادم MCP للنظام — ثلاث طرق، وكل ما يُضاف يصير مدمجاً في النظام ككل:
 * 1) إضافة سريعة: الاسم + الاتصال (رابط HTTP أو أمر stdio) + المفتاح وطريقة إرساله،
 *    مع «اختبار الاتصال» قبل الحفظ واختيار الأدوات المسموحة.
 * 2) خادم داخلي تبنيه: رفع server.py أو .zip (+ مفاتيحه كمتغيّرات بيئة)، مع قالب جاهز.
 * 3) ملف إعداد JSON بصيغة mcpServers (Claude Desktop / Cursor).
 */
const muted = 'text-text-secondary-light dark:text-text-secondary-dark'
const strong = 'text-text-primary-light dark:text-text-primary-dark'
const input = 'w-full text-sm px-3 py-2 rounded-lg border border-border-light dark:border-border-dark bg-transparent ' + strong
const label = `block text-xs mb-1 ${muted}`

const parseKV = (text) => Object.fromEntries(
  (text || '').split('\n').map(l => l.trim()).filter(l => l.includes('='))
    .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
)

const AUTH = {
  none: { label: 'بلا مفتاح', hint: '' },
  bearer: { label: 'Bearer (ترويسة Authorization)', hint: 'الأشيع: Authorization: Bearer <المفتاح>' },
  header: { label: 'ترويسة باسم مخصّص', hint: 'مثل X-API-Key', needName: true, placeholder: 'X-API-Key' },
  query: { label: 'معامل في الرابط', hint: 'مثل ?apiKey=… — يُحفظ كسرّ ولا يبقى في الرابط', needName: true, placeholder: 'apiKey' },
  env: { label: 'متغيّر بيئة (stdio)', hint: 'يقرؤه الخادم المحلي من بيئته', needName: true, placeholder: 'API_KEY' },
}

const emptyQuick = {
  name: '', description: '', transport: 'http', url: '', command: '', args: '',
  authMode: 'bearer', authName: '', authKey: '', extra: '', coordinator: true, employees: true,
}

const Toggle = ({ on, onChange, text }) => (
  <label className="flex items-center gap-2 text-xs cursor-pointer">
    <input type="checkbox" checked={!!on} onChange={(e) => onChange(e.target.checked)} />
    <span className={muted}>{text}</span>
  </label>
)

const McpAddCard = ({ notify, onAdded }) => {
  const [mode, setMode] = useState('quick')
  const [q, setQ] = useState(emptyQuick)
  const [found, setFound] = useState(null)        // نتيجة اختبار الاتصال {tools, server_info}
  const [allowed, setAllowed] = useState({})      // tool -> bool
  const [research, setResearch] = useState('')
  const [busy, setBusy] = useState('')
  const [pkg, setPkg] = useState({ name: '', description: '', env: '', coordinator: true, employees: true })
  const [snippet, setSnippet] = useState('')
  const [snipInfo, setSnipInfo] = useState(null)   // {detected, warnings, forms, index}

  const DETECTED = { curl: 'أمر curl', python: 'كود Python', json: 'إعداد JSON', 'claude-cli': 'أمر claude mcp add',
    'mcp-remote': 'أمر mcp-remote (يُتصل بالرابط مباشرة)', stdio: 'أمر تشغيل محلي (stdio)', url: 'رابط' }

  const applyForm = (f) => {
    setQ({
      ...emptyQuick, name: f.name, description: f.description || '', transport: f.transport,
      url: f.url || '', command: f.command || '', args: (f.args || []).join('\n'),
      authMode: f.auth.mode, authName: f.auth.name || '', authKey: f.auth.key || '',
      extra: Object.entries(f.transport === 'http' ? f.headers || {} : f.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    })
    setFound(null)
  }

  const importSnippet = async () => {
    setBusy('snippet')
    try {
      const res = await axios.post('/api/admin/system/mcps/parse-snippet', { text: snippet }, { headers: authHeaders() })
      setSnipInfo({ ...res.data, index: 0 })
      applyForm(res.data.forms[0])
      const f = res.data.forms[0]
      notify(`✅ تعرّفت على ${DETECTED[res.data.detected] || res.data.detected} — ${res.data.forms.length > 1 ? `${res.data.forms.length} خوادم، ` : ''}راجع الحقول${f.needs_key ? ' وأدخل المفتاح' : ''} ثم «اختبار الاتصال»`, 'success')
    } catch (e) { notify(e?.response?.data?.detail || 'تعذّر قراءة المقتطف', 'error') }
    setBusy('')
  }
  const pkgRef = useRef(null)
  const cfgRef = useRef(null)

  const set = (patch) => { setQ({ ...q, ...patch }); setFound(null) }

  const body = () => {
    const extra = parseKV(q.extra)
    return {
      name: q.name.trim(), description: q.description.trim(), transport: q.transport,
      url: q.url.trim(), command: q.command.trim(),
      args: q.args.split('\n').map(a => a.trim()).filter(Boolean),
      auth: { mode: q.authMode, name: q.authName.trim(), key: q.authKey.trim() },
      headers: q.transport === 'http' ? extra : {}, env: q.transport === 'stdio' ? extra : {},
      scope: { coordinator: q.coordinator, employees: q.employees },
      ...(found ? { allowed_tools: Object.keys(allowed).filter(t => allowed[t]), research_tool: research } : {}),
    }
  }

  const testConn = async () => {
    setBusy('test')
    try {
      const res = await axios.post('/api/admin/system/mcps/test-connection', body(), { headers: authHeaders(), timeout: 90000 })
      setFound(res.data)
      setAllowed(Object.fromEntries(res.data.tools.map(t => [t.name, true])))
      notify(`✅ الاتصال ناجح — ${res.data.tools.length} أداة${res.data.server_info?.name ? ` (${res.data.server_info.name})` : ''}`, 'success')
    } catch (e) { setFound(null); notify(e?.response?.data?.detail || 'فشل الاتصال', 'error') }
    setBusy('')
  }

  const save = async () => {
    setBusy('save')
    try {
      const res = await axios.post('/api/admin/system/mcps', body(), { headers: authHeaders(), timeout: 90000 })
      notify(`✅ ثُبّت «${res.data.server.label}» في النظام (${res.data.server.tools.length} أداة) — يتصل به منسّق كل مالك الآن`, 'success')
      setQ(emptyQuick); setFound(null)
      await onAdded()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل الحفظ', 'error') }
    setBusy('')
  }

  const upload = async (ref, extra) => {
    const file = ref.current?.files?.[0]
    if (!file) return notify('اختر ملفاً أولاً', 'error')
    const fd = new FormData()
    fd.append('file', file)
    Object.entries(extra).forEach(([k, v]) => fd.append(k, String(v)))
    setBusy('upload')
    try {
      const res = await axios.post('/api/admin/system/mcps/upload', fd, { headers: authHeaders(), timeout: 120000 })
      const ok = res.data.saved.map(s => `${s.label} (${s.tools.length} أداة)`).join('، ')
      const bad = res.data.errors.map(e => `${e.name}: ${e.error}`).join('؛ ')
      notify(`✅ ثُبّت في النظام: ${ok}${bad ? ` — ⚠️ تعذّر: ${bad}` : ''}`, bad ? 'error' : 'success')
      ref.current.value = ''
      setPkg({ name: '', description: '', env: '', coordinator: true, employees: true })
      await onAdded()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل الرفع', 'error') }
    setBusy('')
  }

  const downloadTemplate = async () => {
    try {
      const res = await axios.get('/api/admin/system/mcps/template', { headers: authHeaders(), responseType: 'blob' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(res.data)
      a.download = 'server.py'
      a.click()
      URL.revokeObjectURL(a.href)
    } catch { notify('تعذّر تنزيل القالب', 'error') }
  }

  const auth = AUTH[q.authMode]
  const tabs = [['quick', '⚡ إضافة سريعة'], ['package', '🛠️ خادم داخلي أبنيه'], ['config', '📄 ملف إعداد JSON']]

  return (
    <div className="bg-card-light dark:bg-card-dark rounded-xl p-5 shadow-sm">
      <h3 className={`text-lg font-semibold mb-1 ${strong}`}>➕ تثبيت خادم MCP في النظام</h3>
      <p className={`text-xs mb-4 ${muted}`}>
        أي خادم يُضاف هنا يصير <b>مدمجاً في النظام ككل</b> (<code dir="ltr">system/mcps/</code>): يتصل به منسّق كل مالك تلقائياً،
        ويُتاح للموظفين إن فعّلت ذلك. المفاتيح تُحفظ خارج المستودع ولا تُعرض مرة أخرى.
      </p>
      <div className="flex flex-wrap gap-2 mb-4">
        {tabs.map(([id, text]) => (
          <button key={id} type="button" onClick={() => setMode(id)}
            className={`text-xs px-3 py-1.5 rounded-full border ${mode === id ? 'bg-primary text-white border-primary' : `border-border-light dark:border-border-dark ${muted}`}`}>
            {text}
          </button>
        ))}
      </div>

      {mode === 'quick' && (
        <div className="space-y-3">
          <div className="rounded-lg border border-dashed border-primary/40 p-3 space-y-2">
            <p className={`text-xs ${strong}`}>📋 المزوّد يعطيك طريقة الاتصال كأمر <b>curl</b> أو كود <b>Python</b>؟ الصقه هنا وتُملأ الحقول تلقائياً</p>
            <textarea rows={4} dir="ltr" value={snippet} onChange={(e) => setSnippet(e.target.value)}
              placeholder={'curl -X POST https://mcp.example.com/mcp -H "Authorization: Bearer ..."\n— أو —\nasync with streamablehttp_client("https://...", headers={...})\n— أو —\nclaude mcp add --transport http name https://... --header "..."  |  npx -y pkg  |  {"mcpServers": {...}}'}
              className={`${input} font-mono text-xs`} />
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={importSnippet} disabled={busy === 'snippet' || !snippet.trim()}
                className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-50">
                {busy === 'snippet' ? 'جارٍ القراءة…' : 'استخراج الإعداد'}
              </button>
              <span className={`text-[11px] ${muted}`}>قراءة فقط — لا يُنفَّذ أي أمر أو كود من المقتطف.</span>
              {snipInfo?.forms?.length > 1 && (
                <select value={snipInfo.index} onChange={(e) => { const i = Number(e.target.value); setSnipInfo({ ...snipInfo, index: i }); applyForm(snipInfo.forms[i]) }}
                  className="text-xs px-2 py-1 rounded-lg border border-border-light dark:border-border-dark bg-transparent">
                  {snipInfo.forms.map((f, i) => <option key={i} value={i}>{f.name || `خادم ${i + 1}`} ({f.transport})</option>)}
                </select>
              )}
            </div>
            {snipInfo?.warnings?.map((w, i) => <p key={i} className="text-[11px] text-amber-600 dark:text-amber-400">⚠️ {w}</p>)}
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            <div><span className={label}>الاسم</span>
              <input placeholder="مثال: tavily" value={q.name} onChange={(e) => set({ name: e.target.value })} className={input} /></div>
            <div><span className={label}>نوع الاتصال</span>
              <select value={q.transport} onChange={(e) => set({ transport: e.target.value, authMode: e.target.value === 'stdio' && q.authMode === 'bearer' ? 'env' : q.authMode })} className={input}>
                <option value="http">رابط بعيد (Remote — Streamable HTTP)</option>
                <option value="stdio">أمر محلي (stdio — npx / python / uvx)</option>
              </select></div>
          </div>

          {q.transport === 'http' ? (
            <div><span className={label}>رابط الخادم</span>
              <input placeholder="https://mcp.example.com/mcp" value={q.url} onChange={(e) => set({ url: e.target.value })} className={input} dir="ltr" />
              <p className={`text-[11px] mt-1 ${muted}`}>إن كان المفتاح داخل الرابط (?apiKey=…) يُنزع تلقائياً ويُحفظ كسرّ.</p></div>
          ) : (
            <div className="grid md:grid-cols-2 gap-3">
              <div><span className={label}>الأمر</span>
                <input placeholder="npx" value={q.command} onChange={(e) => set({ command: e.target.value })} className={input} dir="ltr" /></div>
              <div><span className={label}>الوسائط (سطر لكل وسيط)</span>
                <textarea rows={2} placeholder={'-y\n@modelcontextprotocol/server-brave-search'} value={q.args} onChange={(e) => set({ args: e.target.value })} className={input} dir="ltr" /></div>
            </div>
          )}

          <div className="grid md:grid-cols-3 gap-3">
            <div><span className={label}>المفتاح يُرسل عبر</span>
              <select value={q.authMode} onChange={(e) => set({ authMode: e.target.value })} className={input}>
                {Object.entries(AUTH).filter(([k]) => q.transport === 'stdio' ? k !== 'bearer' && k !== 'header' && k !== 'query' : k !== 'env')
                  .map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select></div>
            {auth.needName && (
              <div><span className={label}>اسم {q.authMode === 'header' ? 'الترويسة' : q.authMode === 'query' ? 'المعامل' : 'المتغيّر'}</span>
                <input placeholder={auth.placeholder} value={q.authName} onChange={(e) => set({ authName: e.target.value })} className={input} dir="ltr" /></div>
            )}
            {q.authMode !== 'none' && (
              <div className={auth.needName ? '' : 'md:col-span-2'}><span className={label}>المفتاح (API Key)</span>
                <input type="password" autoComplete="off" placeholder="••••••••" value={q.authKey} onChange={(e) => set({ authKey: e.target.value })} className={input} dir="ltr" /></div>
            )}
          </div>
          {auth.hint && <p className={`text-[11px] -mt-1 ${muted}`}>{auth.hint}</p>}

          <details>
            <summary className={`text-xs cursor-pointer ${muted}`}>خيارات إضافية</summary>
            <div className="grid md:grid-cols-2 gap-3 mt-2">
              <div><span className={label}>وصف مختصر</span>
                <input placeholder="ماذا يقدّم هذا الخادم؟" value={q.description} onChange={(e) => set({ description: e.target.value })} className={input} /></div>
              <div><span className={label}>{q.transport === 'http' ? 'ترويسات إضافية' : 'متغيّرات بيئة إضافية'} (KEY=value سطر لكل واحد)</span>
                <textarea rows={2} value={q.extra} onChange={(e) => set({ extra: e.target.value })} className={input} dir="ltr" /></div>
            </div>
          </details>

          <div className="flex flex-wrap gap-5">
            <Toggle on={q.coordinator} onChange={(v) => setQ({ ...q, coordinator: v })} text="مرتبط بالمنسّق العام لكل مالك" />
            <Toggle on={q.employees} onChange={(v) => setQ({ ...q, employees: v })} text="متاح للربط بالموظفين الأذكياء" />
          </div>

          {found && (
            <div className="rounded-lg border border-green-300 dark:border-green-800 p-3 space-y-2">
              <p className={`text-xs ${strong}`}>الأدوات المكتشفة — اختر المسموح منها:</p>
              <div className="flex flex-wrap gap-2">
                {found.tools.map(t => (
                  <label key={t.name} title={t.description}
                    className={`text-[11px] px-2 py-1 rounded-full border flex items-center gap-1 ${allowed[t.name] ? 'border-primary text-primary' : `border-border-light dark:border-border-dark ${muted}`}`}>
                    <input type="checkbox" checked={!!allowed[t.name]} onChange={(e) => setAllowed({ ...allowed, [t.name]: e.target.checked })} />
                    <span dir="ltr">{t.name}</span>
                  </label>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-xs ${muted}`}>أداة البحث قبل الكتابة للموظفين (اختياري):</span>
                <select value={research} onChange={(e) => setResearch(e.target.value)} className="text-xs px-2 py-1 rounded-lg border border-border-light dark:border-border-dark bg-transparent">
                  <option value="">—</option>
                  {found.tools.filter(t => allowed[t.name]).map(t => <option key={t.name} value={t.name}>{t.name}</option>)}
                </select>
              </div>
            </div>
          )}

          <div className="flex gap-2">
            <button type="button" onClick={testConn} disabled={!!busy || !q.name || !(q.url || q.command)}
              className="px-4 py-2 rounded-lg border border-indigo-400 text-indigo-600 dark:text-indigo-400 text-sm disabled:opacity-50">
              {busy === 'test' ? 'جارٍ الاتصال…' : 'اختبار الاتصال'}
            </button>
            <button type="button" onClick={save} disabled={!!busy || !q.name || !(q.url || q.command)}
              className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50">
              {busy === 'save' ? 'جارٍ التثبيت…' : 'تثبيت في النظام'}
            </button>
          </div>
        </div>
      )}

      {mode === 'package' && (
        <div className="space-y-3">
          <p className={`text-xs ${muted}`}>
            خادم MCP تكتبه بنفسك: ملف <code>server.py</code> واحد، أو <code>.zip</code> فيه <code>server.py</code> (أو <code>mcp.json</code> يحدّد <code>entry</code>).
            يعمل بمفسّر Python الخاص بالمشروع (stdio). مفاتيحه تصله كمتغيّرات بيئة.{' '}
            <button type="button" onClick={downloadTemplate} className="text-primary underline">تنزيل قالب خادم جاهز</button>
          </p>
          <div className="grid md:grid-cols-2 gap-3">
            <input ref={pkgRef} type="file" accept=".py,.zip" className={input} />
            <input placeholder="الاسم (اختياري — من اسم الملف)" value={pkg.name} onChange={(e) => setPkg({ ...pkg, name: e.target.value })} className={input} />
            <input placeholder="وصف مختصر" value={pkg.description} onChange={(e) => setPkg({ ...pkg, description: e.target.value })} className={input} />
            <textarea rows={2} placeholder={'مفاتيح الخادم (اختياري)\nMY_API_KEY=...'} value={pkg.env} onChange={(e) => setPkg({ ...pkg, env: e.target.value })} className={input} dir="ltr" />
          </div>
          <div className="flex flex-wrap items-center gap-5">
            <Toggle on={pkg.coordinator} onChange={(v) => setPkg({ ...pkg, coordinator: v })} text="مرتبط بالمنسّق العام لكل مالك" />
            <Toggle on={pkg.employees} onChange={(v) => setPkg({ ...pkg, employees: v })} text="متاح للربط بالموظفين الأذكياء" />
            <button type="button" disabled={busy === 'upload'}
              onClick={() => upload(pkgRef, { name: pkg.name, description: pkg.description, env: pkg.env, coordinator: pkg.coordinator, employees: pkg.employees })}
              className="mr-auto px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50">
              {busy === 'upload' ? 'جارٍ التشغيل والاتصال…' : 'رفع وتثبيت'}
            </button>
          </div>
        </div>
      )}

      {mode === 'config' && (
        <div className="space-y-3">
          <p className={`text-xs ${muted}`}>
            ملف بصيغة <code dir="ltr">{'{"mcpServers": {"name": {"command": …} | {"type": "http", "url": …}}}'}</code> — نفس صيغة Claude Desktop وCursor، وقد يحوي عدة خوادم.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <input ref={cfgRef} type="file" accept=".json" className={`${input} md:w-auto`} />
            <button type="button" disabled={busy === 'upload'} onClick={() => upload(cfgRef, { coordinator: true, employees: true })}
              className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50">
              {busy === 'upload' ? 'جارٍ الاتصال…' : 'رفع وتثبيت'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default McpAddCard
