import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import authHeaders from '../../authHeaders'
import McpAddCard from './McpAddCard'

/**
 * خوادم MCP العامة للنظام (system/mcps).
 * العرض للمسؤول والمطوّر؛ الرفع/التعديل/الربط/الحذف للمطوّر فقط (يُفرض في الخادم أيضاً).
 */
const card = 'bg-card-light dark:bg-card-dark rounded-xl p-5 shadow-sm'
const muted = 'text-text-secondary-light dark:text-text-secondary-dark'
const strong = 'text-text-primary-light dark:text-text-primary-dark'
const input = 'w-full text-sm px-3 py-2 rounded-lg border border-border-light dark:border-border-dark bg-transparent ' + strong

const Toggle = ({ on, disabled, onChange, label }) => (
  <label className={`flex items-center gap-2 text-xs ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
    <input type="checkbox" checked={!!on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    <span className={muted}>{label}</span>
  </label>
)

const McpAdmin = ({ canManage, notify }) => {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [secrets, setSecrets] = useState({})      // slug -> {"kind:KEY": value}

  const load = useCallback(async () => {
    try {
      const res = await axios.get('/api/admin/system/mcps', { headers: authHeaders() })
      setData(res.data)
    } catch (e) { notify(e?.response?.data?.detail || 'تعذّر تحميل الخوادم', 'error') }
  }, [notify])

  useEffect(() => { load() }, [load])

  const patch = async (slug, body, msg) => {
    setBusy(slug)
    try {
      await axios.patch(`/api/admin/system/mcps/${slug}`, body, { headers: authHeaders(), timeout: 90000 })
      if (msg) notify(msg, 'success')
      await load()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل التعديل', 'error') }
    setBusy('')
  }

  const test = async (slug) => {
    setBusy(slug)
    try {
      const res = await axios.post(`/api/admin/system/mcps/${slug}/test`, {}, { headers: authHeaders(), timeout: 90000 })
      notify(`✅ الخادم يعمل — ${res.data.server.tools.length} أداة`, 'success')
      await load()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل الاتصال', 'error') }
    setBusy('')
  }

  const remove = async (s) => {
    if (!window.confirm(`حذف «${s.label}» من النظام؟ سيُفصل عن ${s.employees_attached} موظف، وتختفي أدواته من كل المنسّقين.`)) return
    setBusy(s.slug)
    try {
      await axios.delete(`/api/admin/system/mcps/${s.slug}`, { headers: authHeaders() })
      notify('تم الحذف', 'success')
      await load()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل الحذف', 'error') }
    setBusy('')
  }

  const toggleTool = (s, tool, on) => {
    const all = s.tools.map(t => t.name)
    const cur = s.allowed_tools ?? all
    const next = on ? [...new Set([...cur, tool])] : cur.filter(t => t !== tool)
    patch(s.slug, { allowed_tools: next })
  }

  if (!data) return <div className={`${card} ${muted}`}>جارٍ التحميل…</div>

  return (
    <div className="space-y-6">
      {canManage ? (
        <McpAddCard notify={notify} onAdded={load} />
      ) : (
        <div className={`${card} text-sm ${muted}`}>🔒 إضافة الخوادم وتعديلها وحذفها لصلاحية <b>المطوّر</b> فقط. لديك صلاحية العرض.</div>
      )}

      <div className={card}>
        <h3 className={`text-lg font-semibold mb-1 ${strong}`}>🔌 خوادم النظام المدمجة ({data.servers.length})</h3>
        <p className={`text-xs mb-3 ${muted}`}>
          كل الخوادم في المجلد الرئيسي <code dir="ltr">system/mcps/</code> — الأصلية والمرفوعة سواء: تُحمَّل عند الإقلاع،
          ويتصل بها منسّق كل مالك حسب نطاقها. خوادم الفعل على X وذاكرة الموظفين للموظفين فقط.
        </p>
        <div className="space-y-3">
          {data.servers.filter(s => s.kind === 'builtin').map(s => (
            <div key={s.slug} className="rounded-lg border border-border-light dark:border-border-dark p-4 space-y-2">
              <div className={`font-semibold ${strong}`}>
                {s.label} <span className={`text-xs font-normal ${muted}`} dir="ltr">(system/mcps/{s.slug})</span>
                <span className="mr-2 text-[11px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-900/20 dark:text-indigo-300">مدمج · أصلي</span>
                {s.core && <span className="mr-1 text-[11px] px-2 py-0.5 rounded-full bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300">أساسي</span>}
                <span className={`mr-1 text-[11px] px-2 py-0.5 rounded-full ${s.connected ? 'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400' : 'bg-amber-100 text-amber-700'}`}>
                  {s.connected ? 'يعمل' : 'غير مهيأ'}
                </span>
              </div>
              <p className={`text-xs ${muted}`}>{s.description}</p>
              <p className={`text-[11px] ${muted}`}>
                المنسّق: {s.scope?.coordinator ? `✅ ${s.coordinator_tools.join('، ') || 'عبر أدواته الخاصة'}` : '— للموظفين فقط'}
                {' · '}الموظفون: {s.scope?.employees ? '✅' : '—'} · {s.tools.length} أداة
              </p>
              {s.note && <p className={`text-[11px] ${muted}`}>ℹ️ {s.note}</p>}
            </div>
          ))}
          {data.system.map(s => (
            <div key={s.slug} className="rounded-lg border border-border-light dark:border-border-dark p-4 space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className={`font-semibold ${strong}`}>
                    {s.label} <span className={`text-xs font-normal ${muted}`} dir="ltr">(system/mcps/{s.slug})</span>
                    <span className="mr-2 text-[11px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-900/20 dark:text-indigo-300">مدمج · مرفوع</span>
                    <span className={`mr-2 text-[11px] px-2 py-0.5 rounded-full ${s.enabled ? 'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400' : 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}`}>
                      {s.enabled ? (s.connected ? 'مفعّل ومتصل' : 'مفعّل') : 'معطّل'}
                    </span>
                  </div>
                  {s.description && <p className={`text-xs mt-1 ${muted}`}>{s.description}</p>}
                  <p className={`text-[11px] mt-1 ${muted}`} dir="ltr">
                    {s.source === 'package' ? `package: system/mcps/${s.slug}/${s.entry}` : s.transport === 'http' ? s.url : `${s.command} ${(s.args || []).join(' ')}`}
                  </p>
                  <p className={`text-[11px] ${muted}`}>
                    {s.source === 'package' ? '📦 حزمة' : '📄 إعداد'} · {s.transport} · مربوط بـ {s.employees_attached} موظف
                    {s.added_by ? ` · أضافه ${s.added_by}` : ''}{s.server_info?.name ? ` · ${s.server_info.name} ${s.server_info.version || ''}` : ''}
                  </p>
                </div>
                {canManage && (
                  <div className="flex gap-2 shrink-0">
                    <button type="button" disabled={busy === s.slug} onClick={() => patch(s.slug, { enabled: !s.enabled }, s.enabled ? 'عُطّل الخادم' : 'فُعّل الخادم')}
                      className="text-xs px-3 py-1.5 rounded-lg border border-border-light dark:border-border-dark">{s.enabled ? 'تعطيل' : 'تفعيل'}</button>
                    <button type="button" disabled={busy === s.slug} onClick={() => test(s.slug)}
                      className="text-xs px-3 py-1.5 rounded-lg border border-indigo-400 text-indigo-600 dark:text-indigo-400">اختبار</button>
                    <button type="button" disabled={busy === s.slug} onClick={() => remove(s)}
                      className="text-xs px-3 py-1.5 rounded-lg border border-red-400 text-red-600 dark:text-red-400">حذف</button>
                  </div>
                )}
              </div>

              <div className="flex flex-wrap gap-5">
                <Toggle on={s.scope?.coordinator} disabled={!canManage || busy === s.slug}
                  onChange={(v) => patch(s.slug, { scope: { coordinator: v } })} label="المنسّق العام (كل المستخدمين)" />
                <Toggle on={s.scope?.employees} disabled={!canManage || busy === s.slug}
                  onChange={(v) => patch(s.slug, { scope: { employees: v } })} label="متاح للموظفين" />
              </div>

              <div>
                <p className={`text-xs mb-1 ${muted}`}>الأدوات (المؤشَّر عليها فقط مسموح):</p>
                <div className="flex flex-wrap gap-2">
                  {s.tools.map(t => {
                    const allowed = s.allowed_tools == null || s.allowed_tools.includes(t.name)
                    return (
                      <label key={t.name} title={t.description}
                        className={`text-[11px] px-2 py-1 rounded-full border flex items-center gap-1 ${allowed ? 'border-primary text-primary' : 'border-border-light dark:border-border-dark ' + muted}`}>
                        <input type="checkbox" checked={allowed} disabled={!canManage || busy === s.slug}
                          onChange={(e) => toggleTool(s, t.name, e.target.checked)} />
                        <span dir="ltr">{t.name}</span>
                      </label>
                    )
                  })}
                </div>
              </div>

              {canManage && (s.env_keys.length > 0 || s.header_keys.length > 0 || (s.query_keys || []).length > 0) && (
                <div>
                  <p className={`text-xs mb-1 ${muted}`}>تحديث المفاتيح والإعدادات (اتركه فارغاً لإبقاء القيمة الحالية؛ يُعاد الاتصال بالخادم بالقيم الجديدة):</p>
                  <div className="grid md:grid-cols-2 gap-2">
                    {[...s.env_keys.map(k => ['env', k]), ...s.header_keys.map(k => ['headers', k]),
                      ...(s.query_keys || []).map(k => ['query_secrets', k])].map(([kind, k]) => {
                      // إعداد غير سري (اسم موديل مثلاً): يُعرض نصاً بقيمته الحالية؛ ما يبدو مفتاحاً يبقى مخفياً
                      const isSecret = kind !== 'env' || /key|token|secret|password|passwd|auth|sig|credential/i.test(k)
                      const current = !isSecret ? (s.env_values?.[k] || '') : ''
                      return (
                        <input key={`${kind}:${k}`} type={isSecret ? 'password' : 'text'} autoComplete="off" dir="ltr"
                          placeholder={`${k} (${kind === 'env' ? (isSecret ? 'متغيّر بيئة' : 'إعداد') : kind === 'headers' ? 'ترويسة' : 'معامل رابط'})${current ? ` — الحالي: ${current}` : ''}`}
                          value={secrets[s.slug]?.[`${kind}:${k}`] || ''}
                          onChange={(e) => setSecrets(p => ({ ...p, [s.slug]: { ...p[s.slug], [`${kind}:${k}`]: e.target.value } }))}
                          className={input} />
                      )
                    })}
                  </div>
                  <button type="button" disabled={busy === s.slug}
                    onClick={() => {
                      const vals = secrets[s.slug] || {}
                      const body = { env: {}, headers: {}, query_secrets: {} }
                      Object.entries(vals).forEach(([key, v]) => { if (v) { const [kind, k] = key.split(':'); body[kind][k] = v } })
                      patch(s.slug, body, 'حُدّثت الأسرار وأُعيد الاتصال').then(() => setSecrets(p => ({ ...p, [s.slug]: {} })))
                    }}
                    className="mt-2 text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-50">حفظ الأسرار</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}

export default McpAdmin
