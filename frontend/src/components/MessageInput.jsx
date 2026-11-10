import { useEffect, useRef, useState } from 'react'
import { Send, Paperclip, Image, FileText, UserPlus } from 'lucide-react'

const MessageInput = ({ inputValue, setInputValue, handleSendMessage, isConnected, onAttachImage, onAttachFile, onAttachCookies, onFocusRef, mentionOptions = [] }) => {
  const [isAttachOpen, setIsAttachOpen] = useState(false)
  const imageInputRef = useRef(null)
  const fileInputRef = useRef(null)
  const cookiesInputRef = useRef(null)
  const textareaRef = useRef(null)

  useEffect(() => {
    if (!onFocusRef) return
    onFocusRef.current = () => {
      textareaRef.current?.focus()
    }
  }, [onFocusRef])

  // اقتراح الموظفين عند كتابة @ في آخر النص
  const mentionQuery = (inputValue.match(/(?:^|\s)@([\w\u0600-\u06FF]*)$/) || [])[1]
  const mentionMatches = mentionQuery === undefined ? [] : mentionOptions.filter((e) => {
    const q = mentionQuery.toLowerCase()
    return e.handle.toLowerCase().includes(q) || (e.name || '').toLowerCase().includes(q)
  }).slice(0, 6)

  const pickMention = (handle) => {
    setInputValue(inputValue.replace(/@([\w\u0600-\u06FF]*)$/, `@${handle} `))
    textareaRef.current?.focus()
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && mentionMatches.length > 0) {
      e.preventDefault()
      pickMention(mentionMatches[0].handle)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }

  const handleChooseImage = () => {
    setIsAttachOpen(false)
    imageInputRef.current?.click()
  }

  const handleChooseFile = () => {
    setIsAttachOpen(false)
    fileInputRef.current?.click()
  }

  const handleChooseCookies = () => {
    setIsAttachOpen(false)
    cookiesInputRef.current?.click()
  }

  const handleImageSelected = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    await onAttachImage?.(file)
  }

  const handleFileSelected = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    await onAttachFile?.(file)
  }

  const handleCookiesSelected = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    await onAttachCookies?.(file)
  }

  return (
    <div className="w-full bg-transparent pt-3 pb-4 px-4 md:px-8">
      <div className="w-full max-w-[900px] mx-auto">
        {mentionMatches.length > 0 && (
          <div className="moj-glass-strong mb-2 rounded-2xl overflow-hidden">
            {mentionMatches.map((e, i) => (
              <button
                key={e.handle}
                type="button"
                onClick={() => pickMention(e.handle)}
                className={`w-full flex items-center gap-2 px-4 py-2 text-start text-sm hover:bg-gray-50 dark:hover:bg-gray-700 ${i === 0 ? 'bg-primary/5' : ''}`}
              >
                <span className={`size-2 rounded-full ${e.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                <span className="font-semibold text-text-primary-light dark:text-text-primary-dark">{e.name}</span>
                <span className="text-text-secondary-light dark:text-text-secondary-dark">@{e.handle}</span>
                {e.role && <span className="text-xs text-text-secondary-light dark:text-text-secondary-dark truncate">· {e.role}</span>}
              </button>
            ))}
          </div>
        )}
        {!isConnected && (
          <p className="text-xs text-amber-700 dark:text-amber-300 mb-2">
            جارٍ إعادة الاتصال بالخادم… رسائلك تُحفظ وتُرسل تلقائياً عند عودته.
          </p>
        )}
        <div className="moj-glass-input relative flex items-end gap-1 w-full p-2 ps-2 pe-2 rounded-[22px] focus-within:ring-2 focus-within:ring-primary/30 transition-colors duration-150">
          <button
            onClick={handleSendMessage}
            disabled={!inputValue.trim()}
            title={!inputValue.trim() ? 'اكتب رسالة أولاً' : !isConnected ? 'تُرسل عند عودة الاتصال' : 'إرسال'}
            aria-label="إرسال"
            className={`moj-hover size-10 mb-0.5 rounded-xl transition-colors duration-150 shrink-0 flex items-center justify-center ${
              inputValue.trim()
                ? 'moj-primary text-white'
                : 'bg-black/5 dark:bg-white/10 text-text-secondary-light dark:text-text-secondary-dark cursor-not-allowed'
            }`}
          >
            <Send size={18} />
          </button>
          <textarea
            ref={textareaRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            className="w-full max-h-[200px] py-3 px-3 bg-transparent border-0 focus:ring-0 text-text-primary-light dark:text-text-primary-dark placeholder-text-secondary-light dark:placeholder-text-secondary-dark resize-none leading-relaxed outline-none text-sm"
            placeholder="وجّه فريقك… اكتب @ لتخاطب وكيلاً بعينه"
            rows="1"
            style={{ minHeight: '48px' }}
          />
          <div className="relative shrink-0 mb-0.5">
            <button
              type="button"
              onClick={() => setIsAttachOpen((v) => !v)}
              className="moj-pill moj-hover size-10 flex items-center justify-center text-text-secondary-light dark:text-text-secondary-dark hover:text-primary dark:hover:text-primary rounded-xl"
              title="إرفاق"
            >
              <Paperclip size={18} />
            </button>

            {isAttachOpen && (
              <div className="moj-glass-strong absolute bottom-full mb-2 z-20 w-44 rounded-2xl overflow-hidden" style={{ insetInlineEnd: 0 }}>
                <button
                  type="button"
                  onClick={handleChooseImage}
                  className="moj-hover w-full flex items-center gap-2 px-4 py-3 text-start hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                >
                  <Image size={18} className="text-text-secondary-light dark:text-text-secondary-dark" />
                  <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">إرفاق صورة</span>
                </button>
                <button
                  type="button"
                  onClick={handleChooseFile}
                  className="moj-hover w-full flex items-center gap-2 px-4 py-3 text-start hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors border-t border-border-light dark:border-border-dark"
                >
                  <FileText size={18} className="text-text-secondary-light dark:text-text-secondary-dark" />
                  <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">إرفاق ملف</span>
                </button>
                <button
                  type="button"
                  onClick={handleChooseCookies}
                  className="moj-hover w-full flex items-center gap-2 px-4 py-3 text-start hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors border-t border-border-light dark:border-border-dark"
                >
                  <UserPlus size={18} className="text-primary" />
                  <span className="text-sm font-medium text-text-primary-light dark:text-text-primary-dark">إضافة حساب (كوكيز/CSV)</span>
                </button>
              </div>
            )}

            <input
              ref={imageInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={handleImageSelected}
            />
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf,.pdf,application/msword,.doc,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx,application/vnd.ms-excel,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx"
              className="hidden"
              onChange={handleFileSelected}
            />
            <input
              ref={cookiesInputRef}
              type="file"
              accept=".json,application/json,.csv,text/csv"
              className="hidden"
              onChange={handleCookiesSelected}
            />
          </div>
        </div>
        <div className="text-center mt-2">
          <p className="text-xs text-text-secondary-light dark:text-text-secondary-dark">
            قد يخطئ موج. تحقّق من المعلومات المهمة.
          </p>
        </div>
      </div>
    </div>
  )
}

export default MessageInput
