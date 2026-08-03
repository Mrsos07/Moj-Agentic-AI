import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'pinned_account'

/**
 * إدارة "الحساب المثبّت" الذي تُنفّذ عليه كل إجراءات الشات.
 * - يُحفظ في localStorage فيبقى بعد تحديث الصفحة.
 * - يتزامن بين التبويبات عبر حدث 'storage'.
 */
export default function usePinnedAccount() {
  const [pinned, setPinnedState] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) || null
    } catch {
      return null
    }
  })

  // مزامنة عبر التبويبات
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === STORAGE_KEY) setPinnedState(e.newValue || null)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const setPinned = useCallback((label) => {
    try {
      if (label) localStorage.setItem(STORAGE_KEY, label)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
    setPinnedState(label || null)
  }, [])

  const clearPinned = useCallback(() => setPinned(null), [setPinned])

  return { pinned, setPinned, clearPinned }
}
