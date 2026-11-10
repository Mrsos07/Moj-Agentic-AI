import { useState } from 'react'
import { ClipboardList, Megaphone, Target, Play, Brain, MessageSquare, Pin } from 'lucide-react'
import GlassIcon from './ui/GlassIcon'
import MemoryPanel from './MemoryPanel'

/**
 * شريط الفريق في الشات الرئيسي (المنسّق العام).
 * props:
 *  - team: [{handle, name, role, enabled, status, counters}]
 *  - pinned: الموظف المثبّت (اختياري — يصبح الافتراضي للمنسّق)
 *  - onMention(handle): إدراج @handle في صندوق الكتابة
 *  - onOpenChat(handle): فتح محادثة الموظف الخاصة
 *  - onQuick(text, send): اقتراح سريع (send=true يرسل فوراً)
 */
const TeamBar = ({ team, pinned, onMention, onOpenChat, onQuick, disabled, userId }) => {
  const [memoryOpen, setMemoryOpen] = useState(false)
  const quick = [
    { label: 'تقرير الفريق', icon: ClipboardList, text: 'وش حالة فريقي اليوم؟ مين نشط ومين نشر؟', send: true },
    { label: 'مهمة للفريق', icon: Megaphone, text: 'خل كل الموظفين ينشرون عن ', send: false },
    { label: 'وزّع حسب التخصص', icon: Target, text: 'وزّع على الموظفين مواضيع اليوم كل واحد حسب تخصصه وخلهم ينشرون', send: false },
    { label: 'شغّل الكل الآن', icon: Play, text: 'شغّل دورة عمل الآن لكل الموظفين النشطين', send: true },
  ]
  const pillCls =
    'moj-pill moj-hover shrink-0 inline-flex items-center gap-2 h-8 ps-2 pe-3 rounded-xl ' +
    'text-xs font-medium text-text-primary-light ' +
    'dark:text-text-primary-dark focus-visible:ring-2 ' +
    'focus-visible:ring-primary/60 disabled:opacity-40'

  return (
    <div className="px-4 pt-2">
      <div className="w-full max-w-[900px] mx-auto">
        <div className="flex items-center gap-2 overflow-x-auto pb-1.5">
          {team.length === 0 ? (
            <span className="text-xs text-text-secondary-light dark:text-text-secondary-dark">
              لا يوجد وكلاء بعد — أضف حساباً من «حساباتي» ثم فعّله من «الوكلاء».
            </span>
          ) : team.map((e) => (
            <div
              key={e.handle}
              className={`moj-pill shrink-0 flex items-center gap-1.5 ps-2 pe-1.5 h-8 rounded-xl text-xs ${
                pinned === e.handle
                  ? 'text-primary ring-1 ring-primary/30'
                  : ''
              }`}
              title={`${e.role || ''}${e.counters?.posts_today != null ? ` · منشورات اليوم: ${e.counters.posts_today}` : ''}`}
            >
              <span className={`size-2 rounded-full ${e.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
              <button
                type="button"
                onClick={() => onMention(e.handle)}
                disabled={disabled}
                className="moj-hover rounded-md px-1 font-semibold text-text-primary-light dark:text-text-primary-dark hover:text-primary disabled:opacity-50"
                title="وجّه رسالة لهذا الوكيل (@)"
              >
                {e.name}
                <span dir="ltr" className="inline-block font-normal text-text-secondary-light dark:text-text-secondary-dark"> @{e.handle}</span>
              </button>
              {pinned === e.handle && <Pin size={13} className="text-primary" aria-label="المثبّت" />}
              <button
                type="button"
                onClick={() => onOpenChat(e.handle)}
                className="moj-hover size-6 rounded-md inline-flex items-center justify-center text-text-secondary-light dark:text-text-secondary-dark hover:text-primary"
                title="محادثة خاصة مع الوكيل"
              >
                <MessageSquare size={14} />
              </button>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2 overflow-x-auto pb-1">
          {quick.map((q) => (
            <button
              key={q.label}
              type="button"
              onClick={() => onQuick(q.text, q.send)}
              disabled={disabled || team.length === 0}
              className={pillCls}
            >
              <GlassIcon size="sm"><q.icon size={14} /></GlassIcon> {q.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setMemoryOpen(o => !o)}
            className={`${pillCls} ${memoryOpen ? 'bg-primary/10 border-primary/40 text-primary' : ''}`}
            title="ما يتذكّره المنسّق عنك وعن فريقك"
          >
            <GlassIcon size="sm"><Brain size={14} /></GlassIcon> ذاكرة المنسّق
          </button>
        </div>
        {memoryOpen && (
          <div className="mt-1 mb-1 rounded-xl border border-border-light dark:border-border-dark bg-card-light dark:bg-card-dark p-2.5 max-h-72 overflow-y-auto">
            <MemoryPanel userId={userId} compact />
          </div>
        )}
      </div>
    </div>
  )
}

export default TeamBar
