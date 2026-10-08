import { useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'

export function Composer({ onSend, ragEnabled, onRagChange, compact = false, autoFocus = false, placeholder, disabled = false }: {
  onSend: (text: string) => boolean | void; compact?: boolean; autoFocus?: boolean; placeholder?: string; disabled?: boolean
  ragEnabled: boolean; onRagChange: (enabled: boolean) => void
}) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  function send(event?: FormEvent) {
    event?.preventDefault()
    const text = value.trim()
    if (!text || disabled) return
    if (onSend(text) === false) return
    setValue('')
    inputRef.current?.focus()
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
      event.preventDefault(); send()
    }
  }
  return <form className={`composer ${compact ? 'composer-compact' : ''}`} onSubmit={send}>
    <textarea ref={inputRef} lang="zh-CN" aria-label="Question" placeholder={placeholder ?? (compact ? 'Ask a follow-up question…' : 'Ask in Chinese, or choose an example…')} value={value}
      onChange={e => setValue(e.target.value)} onKeyDown={onKeyDown} rows={compact ? 2 : 3}
      autoFocus={autoFocus} maxLength={8000} disabled={disabled} />
    <div className="composer-tools">
      <button type="button" className="rag-toggle" role="switch" aria-label="RAG" aria-checked={ragEnabled}
        title="Use retrieved sources for the next message" onClick={() => onRagChange(!ragEnabled)}>
        RAG {ragEnabled ? 'on' : 'off'}
      </button>
      <button type="button" onClick={() => send()} className="send-button" disabled={disabled || !value.trim()} aria-label="Send question">send <span aria-hidden="true">↗</span></button>
    </div>
  </form>
}
