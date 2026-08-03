import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import {
  MdManageAccounts,
  MdClose,
  MdRefresh,
  MdPerson,
  MdPushPin,
  MdRadioButtonUnchecked,
} from 'react-icons/md'

/**
 * درج "حساباتي" — يعرض حسابات X ويسمح بتثبيت حساب.
 * أي إجراء لاحق في الشات يُنفّذ على الحساب المثبّت.
 *
 * Props:
 *  - open: boolean
 *  - onClose: () => void
 *  - pinned: string | null
 *  - onPin: (label|null) => void
 */
const AccountsDrawer = ({ open, onClose, pinned, onPin }) => {
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const token = localStorage.getItem('token')
      const res = await axios.get('/api/x/accounts', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        timeout: 8000,
      })
      setAccounts(Array.isArray(res.data?.accounts) ? res.data.accounts : [])
    } catch (e) {
      setError('تعذّر تحميل الحسابات')
      setAccounts([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) loadAccounts()
  }, [open, loadAccounts])

  const togglePin = (label) => {
    onPin(label === pinned ? null : label)
  }

  return (
    <>
      {/* Overlay */}
      <div
        className={`fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity duration-300 ${
          open ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />

      {/* Drawer */}
      <aside
        className={`fixed top-0 right-0 h-full w-full sm:w-[400px] bg-card-light dark:bg-sidebar-dark border-l border-border-light dark:border-border-dark shadow-2xl z-50 flex flex-col transition-transform duration-300 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-light dark:border-border-dark shrink-0">
          <div className="flex items-center gap-2">
            <MdManageAccounts className="text-primary" size={22} />
            <h3 className="text-base font-bold text-text-primary-light dark:text-text-primary-dark">
              حساباتي
            </h3>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={loadAccounts}
              className="size-8 flex items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark"
              title="تحديث"
            >
              <MdRefresh size={20} />
            </button>
            <button
              onClick={onClose}
              className="size-8 flex items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-card-dark text-text-secondary-light dark:text-text-secondary-dark"
              title="إغلاق"
            >
              <MdClose size={20} />
            </button>
          </div>
        </div>

        {/* Hint + status */}
        <div className="px-5 py-3 border-b border-border-light dark:border-border-dark bg-gray-50 dark:bg-card-dark/40 shrink-0">
          <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark leading-relaxed">
            اضغط على حساب <b>لتثبيته</b> — بعدها أي إجراء تطلبه (نشر، رد، لايك،
            متابعة...) يُنفّذ تلقائياً على الحساب المثبّت دون ذكر اسمه.
          </p>
          <div className="mt-2 text-xs font-medium">
            {pinned ? (
              <span className="text-text-secondary-light dark:text-text-secondary-dark">
                المثبّت حالياً:{' '}
                <span className="text-primary font-bold">@{pinned}</span>
              </span>
            ) : (
              <span className="text-text-secondary-light dark:text-text-secondary-dark">
                لا يوجد حساب مثبّت — سيُستخدم الحساب الافتراضي.
              </span>
            )}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {loading ? (
            <div className="flex items-center justify-center py-10">
              <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="text-center text-sm text-red-500 py-10">{error}</div>
          ) : accounts.length === 0 ? (
            <div className="text-center text-sm text-text-secondary-light dark:text-text-secondary-dark py-10">
              لا توجد حسابات. ارفع ملف كوكيز أولاً.
            </div>
          ) : (
            accounts.map((acc) => {
              const label = acc.label
              const isPinned = label === pinned
              return (
                <button
                  key={label}
                  onClick={() => togglePin(label)}
                  className={`w-full flex items-center justify-between gap-3 rounded-xl border p-3 text-right transition-colors ${
                    isPinned
                      ? 'border-primary bg-primary/5'
                      : 'border-border-light dark:border-border-dark hover:bg-gray-50 dark:hover:bg-card-dark/60'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className={`size-9 rounded-full flex items-center justify-center shrink-0 ${
                        isPinned ? 'bg-primary/20' : 'bg-gray-100 dark:bg-card-dark'
                      }`}
                    >
                      <MdPerson
                        className={isPinned ? 'text-primary' : 'text-text-secondary-light dark:text-text-secondary-dark'}
                        size={20}
                      />
                    </div>
                    <div className="min-w-0 text-right">
                      <p className="text-sm font-bold text-text-primary-light dark:text-text-primary-dark truncate">
                        @{label}
                      </p>
                      <p className={`text-xs ${isPinned ? 'text-primary' : 'text-text-secondary-light dark:text-text-secondary-dark'}`}>
                        {isPinned ? 'مثبّت — الإجراءات تُنفّذ عليه' : 'اضغط للتثبيت'}
                      </p>
                    </div>
                  </div>
                  {isPinned ? (
                    <MdPushPin className="text-primary shrink-0" size={20} />
                  ) : (
                    <MdRadioButtonUnchecked className="text-gray-300 dark:text-gray-600 shrink-0" size={20} />
                  )}
                </button>
              )
            })
          )}
        </div>
      </aside>
    </>
  )
}

export default AccountsDrawer
