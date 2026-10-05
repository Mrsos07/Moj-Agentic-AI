import { useState } from 'react'
import { MdChat, MdPushPin } from 'react-icons/md'
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
    { label: '📋 تقرير الفريق', text: 'وش حالة فريقي اليوم؟ مين نشط ومين نشر؟', send: true },
    { label: '📣 مهمة للفريق', text: 'خل كل الموظفين ينشرون عن ', send: false },
    { label: '🎯 وزّع حسب التخصص', text: 'وزّع على الموظفين مواضيع اليوم كل واحد حسب تخصصه وخلهم ينشرون', send: false },
    { label: '🚀 شغّل الكل الآن', text: 'شغّل دورة عمل الآن لكل الموظفين النشطين', send: true },
  ]

  return (
    <div className="px-4 pt-2">
      <div className="w-full max-w-[900px] mx-auto">
        <div className="flex items-center gap-2 overflow-x-auto pb-1.5">
          {team.length === 0 ? (
            <span className="text-xs text-text-secondary-light dark:text-text-secondary-dark">
              لا يوجد موظفون بعد — أضف حساباً من «حساباتي» ثم فعّله من «الموظفين الذكيين».
            </span>
          ) : team.map((e) => (
            <div
              key={e.handle}
              className={`shrink-0 flex items-center gap-1.5 pl-1.5 pr-2.5 py-1 rounded-full border text-xs transition-colors ${
                pinned === e.handle
                  ? 'border-primary bg-primary/10'
                  : 'border-border-light dark:border-border-dark bg-card-light dark:bg-card-dark'
              }`}
              title={`${e.role || ''}${e.counters?.posts_today != null ? ` · منشورات اليوم: ${e.counters.posts_today}` : ''}`}
            >
              <span className={`size-2 rounded-full ${e.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
              <button
                type="button"
                onClick={() => onMention(e.handle)}
                disabled={disabled}
                className="font-semibold text-text-primary-light dark:text-text-primary-dark hover:text-primary disabled:opacity-50"
                title="وجّه رسالة لهذا الموظف (@)"
              >
                {e.name}
                <span className="font-normal text-text-secondary-light dark:text-text-secondary-dark"> @{e.handle}</span>
              </button>
              {pinned === e.handle && <MdPushPin size={13} className="text-primary" title="المثبّت" />}
              <button
                type="button"
                onClick={() => onOpenChat(e.handle)}
                className="text-text-secondary-light dark:text-text-secondary-dark hover:text-primary"
                title="محادثة خاصة مع الموظف"
              >
                <MdChat size={14} />
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
              className="shrink-0 text-xs px-3 py-1.5 rounded-full border border-primary/30 bg-primary/5 hover:bg-primary/10 text-primary font-medium transition-colors disabled:opacity-40"
            >
              {q.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setMemoryOpen(o => !o)}
            className={`shrink-0 text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${memoryOpen ? 'bg-primary text-white border-primary' : 'border-primary/30 bg-primary/5 text-primary hover:bg-primary/10'}`}
            title="ما يتذكّره المنسّق عنك وعن فريقك"
          >
            🧠 ذاكرة المنسّق
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
