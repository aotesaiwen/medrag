import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Quotation, Source } from './model'
import { Composer } from './Composer'

const FRAME_DOCUMENT = `<!doctype html><html lang="en"><head><meta charset="utf-8"><link rel="stylesheet" href="/fonts.css"><link rel="stylesheet" href="/source.css"></head><body><div id="source-root"></div></body></html>`
const PASSAGE_HIGHLIGHT = 'quoted-passage'

function Passage({ source, turnId, onAsk, reduced, disabled, ragEnabled, onRagChange }: {
  source: Source; turnId: string; reduced: boolean; disabled: boolean; onAsk: (question: string, quote: Quotation) => boolean | void
  ragEnabled: boolean; onRagChange: (enabled: boolean) => void
}) {
  const root = useRef<HTMLDivElement>(null)
  const passage = useRef<HTMLDivElement>(null)
  const composer = useRef<HTMLElement>(null)
  const selecting = useRef(false)
  const [selected, setSelected] = useState<{ text: string; range: Range; top: number; left: number } | null>(null)
  function captureSelection() {
    const doc = root.current?.ownerDocument
    const selection = doc?.getSelection()
    if (!selection || selection.isCollapsed || !selection.rangeCount || !passage.current) {
      setSelected(null)
      return
    }
    const original = selection.getRangeAt(0)
    if (!original.intersectsNode(passage.current)) return
    const bounds = doc!.createRange()
    bounds.selectNodeContents(passage.current)
    const range = original.cloneRange()
    if (range.compareBoundaryPoints(0, bounds) < 0) range.setStart(bounds.startContainer, bounds.startOffset)
    if (range.compareBoundaryPoints(2, bounds) > 0) range.setEnd(bounds.endContainer, bounds.endOffset)
    const text = range.toString().trim()
    if (!text) return
    const rects = range.getClientRects()
    const rect = rects[rects.length - 1] ?? range.getBoundingClientRect()
    const win = doc!.defaultView!
    setSelected({ text, range, top: rect.bottom + 12 + win.scrollY, left: rect.left })
  }
  useLayoutEffect(() => {
    const win = root.current?.ownerDocument.defaultView as (Window & typeof globalThis) | null
    if (!selected || !win?.CSS.highlights || !win.Highlight) return
    // Preserve the source range when focus moves into the question field,
    // without changing passage nodes or the browser's native selection.
    win.CSS.highlights.set(PASSAGE_HIGHLIGHT, new win.Highlight(selected.range))
    return () => { win.CSS.highlights.delete(PASSAGE_HIGHLIGHT) }
  }, [selected])
  useLayoutEffect(() => {
    const element = composer.current
    const win = element?.ownerDocument.defaultView
    if (!selected || !element || !win) return
    function position() {
      if (!element || !win || !selected) return
      const { width, height } = element.getBoundingClientRect()
      // Size the quote naturally first, then shift the complete composer into
      // view. Only the quote scrolls once the viewport height is exhausted.
      const maxTop = Math.max(12, win.innerHeight - height - 12)
      element.style.top = `${Math.max(12, Math.min(selected.top - win.scrollY, maxTop)) + win.scrollY}px`
      element.style.left = `${Math.max(16, Math.min(selected.left, win.innerWidth - width - 16))}px`
    }
    position()
    const observer = new ResizeObserver(position)
    observer.observe(element)
    win.addEventListener('resize', position)
    win.addEventListener('scroll', position, { passive: true })
    return () => {
      observer.disconnect()
      win.removeEventListener('resize', position)
      win.removeEventListener('scroll', position)
    }
  }, [selected])
  useEffect(() => {
    const doc = root.current?.ownerDocument
    if (!doc) return
    const parentDoc = doc.defaultView?.parent.document
    function pointerDown(event: PointerEvent) {
      const target = event.target as Node | null
      selecting.current = !!target && !!passage.current?.contains(target)
      if (target && composer.current?.contains(target)) return
      clear()
    }
    function pointerUp() {
      // Only a gesture begun in the passage may open the composer. A later
      // click elsewhere must not reopen it from a stale iframe selection.
      if (!selecting.current) return
      selecting.current = false
      captureSelection()
    }
    function pointerCancel() { selecting.current = false }
    // A source drag may end in the parent document. Its selection remains
    // confined to the iframe; the answer component never receives state updates.
    for (const targetDoc of [doc, parentDoc]) {
      targetDoc?.addEventListener('pointerdown', pointerDown, true)
      targetDoc?.addEventListener('pointerup', pointerUp)
      targetDoc?.addEventListener('pointercancel', pointerCancel)
    }
    return () => {
      for (const targetDoc of [doc, parentDoc]) {
        targetDoc?.removeEventListener('pointerdown', pointerDown, true)
        targetDoc?.removeEventListener('pointerup', pointerUp)
        targetDoc?.removeEventListener('pointercancel', pointerCancel)
      }
    }
  }, [source.id])
  function clear() { setSelected(null); root.current?.ownerDocument.getSelection()?.removeAllRanges() }
  return <div ref={root} className="source-content" data-reduced={reduced} onKeyDown={event => {
    if (event.key === 'Escape') clear()
  }}>
    <div className="passage" ref={passage} tabIndex={0} aria-label="Selectable source passage"
      onKeyUp={captureSelection}>
      {source.paragraphs.map((text, index) => <p key={index}>{text}</p>)}
    </div>
    {selected && <aside ref={composer} className="selection-composer" aria-label="Ask about this passage"
      style={{ top: selected.top, left: selected.left }}>
      <header><span>ask about this passage</span><button type="button" onClick={clear} aria-label="Close selection composer">×</button></header>
      <blockquote>{selected.text}</blockquote>
      <Composer compact disabled={disabled} ragEnabled={ragEnabled} onRagChange={onRagChange} onSend={question => {
        if (onAsk(question, { text: selected.text, source, turnId }) === false) return false
        clear()
      }} />
    </aside>}
  </div>
}

export function SourcePane({ source, turnId, theme, reduced, onAsk, disabled = false, ragEnabled, onRagChange }: {
  source: Source; turnId: string; theme: 'light' | 'dark'; reduced: boolean
  onAsk: (question: string, quote: Quotation) => boolean | void; disabled?: boolean
  ragEnabled: boolean; onRagChange: (enabled: boolean) => void
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [target, setTarget] = useState<HTMLElement | null>(null)
  useLayoutEffect(() => {
    const doc = frame.current?.contentDocument
    if (doc?.documentElement) doc.documentElement.dataset.theme = theme
  }, [theme, target])
  return <>
    <iframe ref={frame} title="Source passage" className="source-frame" srcDoc={FRAME_DOCUMENT}
      sandbox="allow-same-origin" onLoad={() => {
        const doc = frame.current?.contentDocument
        if (doc?.documentElement) { doc.documentElement.dataset.theme = theme; setTarget(doc.getElementById('source-root')) }
      }} />
    {target && createPortal(<Passage key={`${turnId}:${source.id}`} source={source} turnId={turnId}
      reduced={reduced} onAsk={onAsk} disabled={disabled} ragEnabled={ragEnabled} onRagChange={onRagChange} />, target)}
  </>
}
