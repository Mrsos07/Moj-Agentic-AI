import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import authHeaders from '../authHeaders'

/**
 * الذاكرة طويلة المدى لموظف (employeeId) أو للمنسّق العام (userId):
 * عرض + إضافة يدوية + حذف. ما يقوله المالك في الشات يُحفظ هنا تلقائياً.
 */
const KIND = {
  preference: 'تفضيل', instruction: 'تعليمة', fact: 'معلومة', decision: 'قرار',
  person: 'شخص', lesson: 'درس', event: 'حدث',
}

const MemoryPanel = ({ employeeId, userId, compact }) => {
  const [items, setItems] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // ذاكرة المنسّق تُحدَّد من المستخدم الموثَّق في الخادم — لا نرسل user_id
  const params = employeeId ? { employee_id: employeeId } : {}

  const load = useCallback(async () => {
    try {
      const res = await axios.get('/api/aeos/memory', { headers: authHeaders(), params })
      setItems(res.data?.memories || [])
      setError('')
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر تحميل الذاكرة') }
  }, [employeeId, userId])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load() }, [load])

  const add = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      await axios.post('/api/aeos/memory', { ...params, content: text.trim(), kind: 'instruction', importance: 4 }, { headers: authHeaders() })
      setText('')
      await load()
    } catch (e) { setError(e?.response?.data?.detail || 'تعذّر الحفظ') }
    setBusy(false)
  }

  const remove = async (id) => {
    try { await axios.delete(`/api/aeos/memory/${id}`, { params, headers: authHeaders() }); await load() } catch {
      setError('تعذّر الحذف')
    }
  }

  const facts = (items || []).filter(m => m.kind !== 'event')
  const events = (items || []).filter(m => m.kind === 'event').slice(0, compact ? 5 : 10)

  return (
    <div className="space-y-2">
      {error && <div className="text-[11px] text-red-500">{error}</div>}
      <div className="flex gap-1.5">
        <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder={employeeId ? 'شيء يجب أن يتذكّره هذا الموظف دائماً…' : 'شيء يجب أن يتذكّره المنسّق عنك وعن فريقك…'}
          className="flex-1 text-xs px-2 py-1.5 rounded-lg border border-border-light dark:border-border-dark bg-transparent text-text-primary-light dark:text-text-primary-dark" />
        <button type="button" onClick={add} disabled={busy || !text.trim()}
          className="text-xs px-3 py-1.5 rounded-lg bg-primary text-white disabled:opacity-50">حفظ</button>
      </div>
      {items === null ? (
        <div className="text-[11px] text-text-secondary-light">جارٍ التحميل…</div>
      ) : facts.length === 0 && events.length === 0 ? (
        <div className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark">
          لا ذكريات بعد — ما تقوله في الشات من تفضيلات وتعليمات دائمة يُحفظ هنا تلقائياً.
        </div>
      ) : (
        <>
          {facts.map(m => (
            <div key={m.id} className="flex items-start gap-2 rounded-lg border border-border-light dark:border-border-dark px-2 py-1.5">
              <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary">{KIND[m.kind] || m.kind}</span>
              <span className="flex-1 text-[11px] text-text-primary-light dark:text-text-primary-dark">{m.content}</span>
              <span className="shrink-0 text-[10px] text-amber-500" title="الأهمية">{'★'.repeat(m.importance || 1)}</span>
              <button type="button" onClick={() => remove(m.id)} className="shrink-0 text-[11px] text-red-500" title="انسَ هذه الذكرى">✕</button>
            </div>
          ))}
          {events.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold text-text-secondary-light dark:text-text-secondary-dark mb-0.5">آخر الإسنادات</div>
              {events.map(e => (
                <div key={e.id} className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">• {e.content}</div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default MemoryPanel
