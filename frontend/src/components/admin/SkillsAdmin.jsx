import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import authHeaders from '../../authHeaders'

/**
 * مهارات النظام (system/skills — معيار SKILL.md): تظهر لمنسّق كل مستخدم تلقائياً.
 * العرض للمسؤول والمطوّر؛ الرفع/التعديل/التعطيل/الحذف للمطوّر فقط.
 */
const card = 'bg-card-light dark:bg-card-dark rounded-xl p-5 shadow-sm'
const muted = 'text-text-secondary-light dark:text-text-secondary-dark'
const strong = 'text-text-primary-light dark:text-text-primary-dark'
const input = 'w-full text-sm px-3 py-2 rounded-lg border border-border-light dark:border-border-dark bg-transparent ' + strong

const TEMPLATE = `---
name: my-skill
description: متى يستخدم المنسّق هذه المهارة وماذا تفعل
allowed-tools: list_employees, employee_report
---
# التعليمات
1. ...
`

const SkillsAdmin = ({ canManage, notify }) => {
  const [skills, setSkills] = useState(null)
  const [busy, setBusy] = useState('')
  const [editing, setEditing] = useState(null)    // {slug, content}
  const [overwrite, setOverwrite] = useState(false)
  const fileRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const res = await axios.get('/api/admin/system/skills', { headers: authHeaders() })
      setSkills(res.data.skills)
    } catch (e) { notify(e?.response?.data?.detail || 'تعذّر تحميل المهارات', 'error') }
  }, [notify])

  useEffect(() => { load() }, [load])

  const doUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return notify('اختر ملف SKILL.md أو حزمة .zip', 'error')
    const form = new FormData()
    form.append('file', file)
    form.append('overwrite', String(overwrite))
    setBusy('upload')
    try {
      const res = await axios.post('/api/admin/system/skills/upload', form, { headers: authHeaders() })
      notify(`✅ أُضيفت المهارة «${res.data.skill.name}» — صارت متاحة لمنسّق كل مستخدم`, 'success')
      fileRef.current.value = ''
      await load()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل الرفع', 'error') }
    setBusy('')
  }

  const openEditor = async (slug) => {
    try {
      const res = await axios.get(`/api/admin/system/skills/${slug}`, { headers: authHeaders() })
      setEditing({ slug, content: res.data.content })
    } catch (e) { notify(e?.response?.data?.detail || 'تعذّر فتح المهارة', 'error') }
  }

  const patch = async (slug, body, msg) => {
    setBusy(slug)
    try {
      await axios.patch(`/api/admin/system/skills/${slug}`, body, { headers: authHeaders() })
      notify(msg, 'success')
      await load()
      return true
    } catch (e) { notify(e?.response?.data?.detail || 'فشل التعديل', 'error'); return false }
    finally { setBusy('') }
  }

  const remove = async (s) => {
    if (!window.confirm(`حذف المهارة «${s.name}» من النظام؟`)) return
    setBusy(s.slug)
    try {
      await axios.delete(`/api/admin/system/skills/${s.slug}`, { headers: authHeaders() })
      notify('تم الحذف', 'success')
      await load()
    } catch (e) { notify(e?.response?.data?.detail || 'فشل الحذف', 'error') }
    setBusy('')
  }

  if (!skills) return <div className={`${card} ${muted}`}>جارٍ التحميل…</div>

  return (
    <div className="space-y-6">
      {canManage ? (
        <div className={card}>
          <h3 className={`text-lg font-semibold mb-1 ${strong}`}>➕ إضافة مهارة للنظام</h3>
          <p className={`text-xs mb-3 ${muted}`}>
            ملف <code>SKILL.md</code> (ترويسة YAML فيها <code>name</code> و<code>description</code> ثم التعليمات)، أو <code>.zip</code> لمجلد مهارة فيه ملفات مرجعية.
            المنسّق يرى الاسم والوصف دائماً، ويحمّل التعليمات الكاملة فقط حين يطابق الطلب وصفها.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <input ref={fileRef} type="file" accept=".md,.markdown,.zip" className={`${input} md:w-auto`} />
            <label className={`flex items-center gap-2 text-xs ${muted}`}>
              <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> استبدال إن وُجدت بنفس الاسم
            </label>
            <button type="button" onClick={doUpload} disabled={busy === 'upload'}
              className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50">{busy === 'upload' ? 'جارٍ الرفع…' : 'رفع'}</button>
            <button type="button" onClick={() => setEditing({ slug: '', content: TEMPLATE })}
              className="text-xs px-3 py-2 rounded-lg border border-border-light dark:border-border-dark">عرض قالب SKILL.md</button>
          </div>
        </div>
      ) : (
        <div className={`${card} text-sm ${muted}`}>🔒 إضافة المهارات وتعديلها وحذفها لصلاحية <b>المطوّر</b> فقط. لديك صلاحية العرض.</div>
      )}

      <div className={card}>
        <h3 className={`text-lg font-semibold mb-1 ${strong}`}>🧠 مهارات النظام ({skills.length})</h3>
        <p className={`text-xs mb-3 ${muted}`}>
          كل المهارات في المجلد الرئيسي <code dir="ltr">system/skills/</code>: مهارات <b>المنسّق</b> (تعليمات SKILL.md تصل لمنسّق كل مالك)
          ومهارات <b>الموظفين</b> (كود skill.py يقترح به الموظف أفعاله في كل دورة — للعرض هنا وتُعدَّل في الكود).
        </p>
        <div className="space-y-3">
          {[...skills].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'employee' ? 1 : -1)).map(s => (
            <div key={s.slug} className="rounded-lg border border-border-light dark:border-border-dark p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className={`font-semibold ${strong}`} dir="ltr">
                    {s.name}
                    <span className={`ml-2 text-[11px] px-2 py-0.5 rounded-full ${s.error ? 'bg-red-100 text-red-700' : s.enabled ? 'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400' : 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300'}`}>
                      {s.error ? 'تالفة' : s.enabled ? 'مفعّلة' : 'معطّلة'}
                    </span>
                    <span className="ml-1 text-[11px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-900/20 dark:text-indigo-300">
                      مدمجة · {s.kind === 'employee' ? 'للموظفين' : 'للمنسّق'}{s.builtin ? '' : ' · مرفوعة'}
                    </span>
                  </div>
                  <p className={`text-xs mt-1 ${s.error ? 'text-red-500' : muted}`}>{s.error || s.description}</p>
                  {!s.error && (
                    <p className={`text-[11px] mt-1 ${muted}`}>
                      {s.kind === 'employee'
                        ? `الكود: system/skills/${s.slug}/${s.entry} · الأفعال: ${(s.uses_capabilities || []).join('، ') || '—'} · الترتيب ${s.order}`
                        : `${s.allowed_tools ? `الأدوات: ${s.allowed_tools.join('، ')}` : 'كل أدوات المنسّق'} · ${s.chars} حرف`}
                      {s.resources?.length ? ` · ${s.resources.length} ملف مرجعي` : ''}{s.added_by ? ` · أضافها ${s.added_by}` : ''}
                    </p>
                  )}
                </div>
                <div className="flex gap-2 shrink-0">
                  <button type="button" onClick={() => openEditor(s.slug)}
                    className="text-xs px-3 py-1.5 rounded-lg border border-border-light dark:border-border-dark">{canManage && s.kind !== 'employee' ? 'تعديل' : 'عرض'}</button>
                  {canManage && !s.error && s.kind !== 'employee' && (
                    <button type="button" disabled={busy === s.slug}
                      onClick={() => patch(s.slug, { enabled: !s.enabled }, s.enabled ? 'عُطّلت المهارة' : 'فُعّلت المهارة')}
                      className="text-xs px-3 py-1.5 rounded-lg border border-border-light dark:border-border-dark">{s.enabled ? 'تعطيل' : 'تفعيل'}</button>
                  )}
                  {canManage && s.kind !== 'employee' && (
                    <button type="button" disabled={busy === s.slug} onClick={() => remove(s)}
                      className="text-xs px-3 py-1.5 rounded-lg border border-red-400 text-red-600 dark:text-red-400">حذف</button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {editing && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setEditing(null)}>
          <div className="bg-card-light dark:bg-card-dark rounded-xl max-w-3xl w-full p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
            <h3 className={`font-semibold ${strong}`} dir="ltr">{editing.slug ? `${editing.slug}/SKILL.md` : 'قالب SKILL.md'}</h3>
            <textarea rows={20} dir="auto" value={editing.content}
              readOnly={!canManage || !editing.slug || skills.find(x => x.slug === editing.slug)?.kind === 'employee'}
              onChange={(e) => setEditing({ ...editing, content: e.target.value })}
              className={`${input} font-mono text-xs`} />
            <div className="flex gap-2 justify-end">
              {canManage && editing.slug && skills.find(x => x.slug === editing.slug)?.kind !== 'employee' && (
                <button type="button" disabled={busy === editing.slug}
                  onClick={async () => { if (await patch(editing.slug, { content: editing.content }, 'حُفظت المهارة')) setEditing(null) }}
                  className="px-4 py-2 rounded-lg bg-primary text-white text-sm disabled:opacity-50">حفظ</button>
              )}
              <button type="button" onClick={() => setEditing(null)}
                className="px-4 py-2 rounded-lg border border-border-light dark:border-border-dark text-sm">إغلاق</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default SkillsAdmin
