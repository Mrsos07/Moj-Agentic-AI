import Message from './Message'
import TypingIndicator from './TypingIndicator'
import GlassIcon from './ui/GlassIcon'
import { UserPlus, Layers, LayoutGrid, IdCard, Zap } from 'lucide-react'
import logoLight from '../assets/logos/logo-light.png'

const SUGGESTION_ICONS = {
  'أضف حساب تويتر': UserPlus,
  'إدارة المحتوى': Layers,
  'إدارة الحسابات': LayoutGrid,
  'إنشاء هوية': IdCard,
  'تفعيل الحساب': Zap,
}

const MessageList = ({ messages, isTyping, messagesEndRef, showSuggestions, onSuggestionClick }) => {
  const suggestions = [
    'أضف حساب تويتر',
    'إدارة المحتوى',
    'إدارة الحسابات',
    'إنشاء هوية',
    'تفعيل الحساب'
  ]

  const hasUserMessage = messages.some((m) => m?.type === 'user')
  const shouldShowSuggestions = !!showSuggestions && !hasUserMessage && messages.length > 0

  return (
    <div className="flex-1 overflow-y-auto w-full p-4 md:p-6 pb-2">
      <div className="flex flex-col w-full max-w-[900px] mx-auto">
        <div className="moj-glass rounded-[22px] p-5 md:p-6">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="bg-primary rounded-full size-16 flex items-center justify-center text-white font-bold shadow-md mb-6">
                <img src={logoLight} alt="" className="h-8 w-auto" />
              </div>
              <h3 className="text-2xl font-bold text-text-primary-light dark:text-text-primary-dark mb-3">مرحباً بك في موج</h3>
              <p className="text-text-secondary-light dark:text-text-secondary-dark max-w-md">
                أدير فريق وكلائك على X من هنا. كيف أساعدك اليوم؟
              </p>
            </div>
          ) : (
            <>
              {messages.map((message) => (
                <Message key={message.id} message={message} />
              ))}

              {shouldShowSuggestions && (
                <div className="mt-4 mb-2">
                  <div className="text-xs font-medium text-text-secondary-light dark:text-text-secondary-dark mb-2 text-right">
                    اقتراحات سريعة
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {suggestions.map((text) => {
                      const Icon = SUGGESTION_ICONS[text]
                      return (
                        <button
                          key={text}
                          type="button"
                          onClick={() => onSuggestionClick?.(text)}
                          className="moj-pill moj-hover inline-flex items-center gap-2 h-9 ps-2 pe-3.5 rounded-xl text-sm font-medium text-text-primary-light dark:text-text-primary-dark focus-visible:ring-2 focus-visible:ring-primary/60"
                        >
                          {Icon && <GlassIcon size="sm"><Icon size={16} /></GlassIcon>}
                          {text}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {isTyping && <TypingIndicator />}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>
    </div>
  )
}

export default MessageList
