import { useState } from 'react'
import {
  ListChecks, ChevronDown, Clock, Loader2, CheckCircle2, XCircle, Ban,
  Info, AlertTriangle, FlaskConical, Square,
} from 'lucide-react'

/**
 * @typedef {'queued'|'running'|'succeeded'|'failed'|'cancelled'} TaskStatus
 * @typedef {'info'|'success'|'warning'|'error'} EventLevel
 * @typedef {{ id:string, title:string, status:TaskStatus, progress?:number, startedAt?:string, finishedAt?:string }} TaskView
 * @typedef {{ id:string, taskId:string, title:string, detail?:string, timestamp?:string, level?:EventLevel }} TaskEventView
 * @param {{ task?: TaskView|null, events?: TaskEventView[], sourceState?: 'unbound'|'loading'|'ready'|'disconnected'|'error', isDemo?: boolean, defaultOpen?: boolean, onStartDemo?: () => void, onEndDemo?: () => void }} props
 */

const STATUS = {
  queued:    { label: 'بانتظار التنفيذ', Icon: Clock, cls: 'text-text-secondary-light dark:text-text-secondary-dark' },
  running:   { label: 'جارٍ التنفيذ',   Icon: Loader2, cls: 'text-primary dark:text-blue-300' },
  succeeded: { label: 'اكتملت',        Icon: CheckCircle2, cls: 'text-green-600 dark:text-green-400' },
  failed:    { label: 'تعذّر التنفيذ',  Icon: XCircle, cls: 'text-red-600 dark:text-red-400' },
  cancelled: { label: 'أُلغيت',        Icon: Ban, cls: 'text-text-secondary-light dark:text-text-secondary-dark' },
}

const LEVEL_ICONS = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
}
const LEVEL_CLS = {
  info: 'text-text-secondary-light dark:text-text-secondary-dark',
  success: 'text-green-600 dark:text-green-400',
  warning: 'text-amber-600 dark:text-amber-400',
  error: 'text-red-600 dark:text-red-400',
}

const fmtTime = (ts) => {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleTimeString('ar-SA', { hour: '2-digit', minute: '2-digit' })
}

export default function TaskActivityPanel({
  task = null,
  events = [],
  sourceState = 'unbound',
  isDemo = false,
  defaultOpen = true,
  onStartDemo = null,
  onEndDemo = null,
}) {
  const [open, setOpen] = useState(defaultOpen)

  const badge = (() => {
    if (sourceState === 'unbound') return 'غير مربوط'
    if (sourceState === 'loading') return 'جارٍ التحميل'
    if (sourceState === 'disconnected') return 'انقطع مصدر البيانات'
    if (sourceState === 'error') return 'تعذّر تحميل السجل'
    return task ? STATUS[task.status]?.label || task.status : 'لا توجد مهمة حالياً'
  })()

  const TaskIcon = task ? STATUS[task.status]?.Icon : null

  return (
    <section className="moj-glass rounded-[22px] p-4 shrink-0" aria-label="سجل التنفيذ">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        <ListChecks size={18} className="text-primary shrink-0" aria-hidden />
        <h3 className="text-sm font-bold whitespace-nowrap text-text-primary-light dark:text-text-primary-dark">سجل التنفيذ</h3>

        <span className="moj-pill inline-flex items-center gap-1 h-6 px-2 rounded-lg text-[11px] font-medium whitespace-nowrap text-text-primary-light dark:text-text-primary-dark">
          {TaskIcon && (
            <TaskIcon
              size={12}
              aria-hidden
              className={`${STATUS[task.status].cls} ${task.status === 'running' ? 'animate-spin motion-reduce:animate-none' : ''}`}
            />
          )}
          {badge}
        </span>

        {isDemo && (
          <span className="inline-flex items-center gap-1 h-6 px-2 rounded-lg text-[11px] font-medium whitespace-nowrap bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
            <FlaskConical size={12} aria-hidden />
            بيانات تجريبية<span className="hidden sm:inline"> — ليست حالة فعلية</span>
          </span>
        )}

        <div className="ms-auto flex items-center gap-2 shrink-0">
          {sourceState === 'unbound' && !isDemo && onStartDemo && (
            <button
              type="button"
              onClick={onStartDemo}
              className="moj-pill moj-hover h-8 px-3 rounded-xl text-xs font-medium text-text-primary-light dark:text-text-primary-dark"
            >
              عرض مثال تجريبي
            </button>
          )}
          {isDemo && onEndDemo && (
            <button
              type="button"
              onClick={onEndDemo}
              className="moj-pill moj-hover inline-flex items-center gap-1 h-8 px-3 rounded-xl text-xs font-medium text-text-primary-light dark:text-text-primary-dark"
            >
              <Square size={11} aria-hidden />
              إنهاء المعاينة
            </button>
          )}
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="moj-pill moj-hover size-8 inline-flex items-center justify-center rounded-xl text-text-secondary-light dark:text-text-secondary-dark"
            aria-expanded={open}
            aria-controls="task-activity-body"
            aria-label={open ? 'طيّ سجل التنفيذ' : 'فتح سجل التنفيذ'}
          >
            <span className="moj-icon inline-flex"><ChevronDown size={16} className={`transition-transform ${open ? '' : 'rotate-180'}`} aria-hidden /></span>
          </button>
        </div>
      </div>

      {open && (
        <div id="task-activity-body" className="mt-3">
          {sourceState === 'unbound' && (
            <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark">
              سجل التنفيذ جاهز. ستظهر حالة المهمة وأحداثها بعد ربط مصدر البيانات.
            </p>
          )}

          {sourceState === 'disconnected' && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              انقطع الاتصال بمصدر السجل. ستُستأنف الأحداث عند عودته.
            </p>
          )}

          {sourceState === 'error' && (
            <p className="text-xs text-red-600 dark:text-red-400">
              تعذّر تحميل سجل التنفيذ.
            </p>
          )}

          {sourceState === 'loading' && (
            <div className="flex flex-col gap-2" aria-hidden>
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-9 rounded-xl bg-black/[0.04] dark:bg-white/[0.06] animate-pulse" />
              ))}
            </div>
          )}

          {sourceState === 'ready' && (
            <>
              {task && (
                <div className="mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">{task.title}</span>
                  </div>
                  {typeof task.progress === 'number' && (
                    <div
                      className="mt-2 h-1.5 rounded-full bg-black/[0.06] dark:bg-white/[0.08] overflow-hidden"
                      role="progressbar"
                      aria-valuenow={Math.max(0, Math.min(100, task.progress))}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label="تقدّم المهمة"
                    >
                      <div
                        className="h-full rounded-full bg-primary transition-[width] duration-300"
                        style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }}
                      />
                    </div>
                  )}
                </div>
              )}

              {events.length === 0 ? (
                <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark">لا توجد أحداث بعد.</p>
              ) : (
                <ol
                  role="log"
                  aria-live="polite"
                  aria-relevant="additions"
                  className="max-h-[260px] overflow-y-auto flex flex-col gap-2"
                >
                  {events.map((ev) => {
                    const LvlIcon = LEVEL_ICONS[ev.level || 'info'] || Info
                    const time = fmtTime(ev.timestamp)
                    return (
                      <li key={ev.id} className="flex items-start gap-2">
                        <LvlIcon size={16} aria-hidden className={`mt-0.5 shrink-0 ${LEVEL_CLS[ev.level || 'info']}`} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">{ev.title}</span>
                            {time && (
                              <span dir="ltr" className="text-[11px] text-text-secondary-light dark:text-text-secondary-dark shrink-0">
                                {time}
                              </span>
                            )}
                          </div>
                          {ev.detail && (
                            <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark break-words leading-relaxed">
                              {ev.detail}
                            </p>
                          )}
                        </div>
                      </li>
                    )
                  })}
                </ol>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}
