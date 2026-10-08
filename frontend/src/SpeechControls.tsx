import { useEffect, useState } from 'react'
import type { RefObject } from 'react'
import type { SpeechController } from './useSpeech'
import { speechText } from './speechText'

type SpeechIcon = 'listen' | 'play' | 'pause' | 'stop' | 'cancel'

function PixelIcon({ name }: { name: SpeechIcon }) {
  const paths: Record<SpeechIcon, string> = {
    listen: 'M2 6h3V4h2V2h2v12H7v-2H5v-2H2z M11 5h2v2h1v2h-1v2h-2V9h1V7h-1z',
    play: 'M4 2h2v2h2v2h2v1h2v2h-2v1H8v2H6v2H4z',
    pause: 'M3 3h3v10H3z M10 3h3v10h-3z',
    stop: 'M3 3h10v10H3z',
    cancel: 'M3 2h2v2h2v2h2V4h2V2h2v2h-2v2H9v4h2v2h2v2h-2v-2H9v-2H7v2H5v2H3v-2h2v-2h2V6H5V4H3z',
  }
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" shapeRendering="crispEdges" aria-hidden="true" focusable="false">
    <path d={paths[name]} />
  </svg>
}

function SpeechButton({ icon, label, onClick, preserveSelection = false }: {
  icon: SpeechIcon; label: string; onClick: () => void; preserveSelection?: boolean
}) {
  return <button type="button" className="speech-button" aria-label={label} title={label}
    onPointerDown={preserveSelection ? event => event.preventDefault() : undefined} onClick={onClick}>
    <PixelIcon name={icon} />
  </button>
}

export function SpeechControls({ turnId, number, contentRef, speech }: {
  turnId: string; number: number; contentRef: RefObject<HTMLDivElement | null>; speech: SpeechController
}) {
  const [selection, setSelection] = useState('')
  const state = speech.state?.turnId === turnId ? speech.state : null
  useEffect(() => {
    function changed() {
      const selected = window.getSelection()
      const content = contentRef.current
      if (!selected || selected.isCollapsed || !content || !content.contains(selected.anchorNode) || !content.contains(selected.focusNode)) {
        setSelection(''); return
      }
      const fragment = document.createElement('div')
      fragment.append(selected.getRangeAt(0).cloneContents())
      setSelection(speechText(fragment))
    }
    document.addEventListener('selectionchange', changed)
    return () => document.removeEventListener('selectionchange', changed)
  }, [contentRef])

  function listen() {
    const text = selection || (contentRef.current ? speechText(contentRef.current) : '')
    void speech.listen(turnId, text)
  }

  return <div className="speech-controls" data-loading={state?.status === 'loading' || undefined} role="group" aria-label={`Speech for answer ${number}`} lang="en">
    {state?.status === 'loading' && <span className="speech-feedback" role="status">{state.slow ? 'Still preparing…' : 'Preparing audio…'}</span>}
    <div className="speech-actions">
    {state?.status === 'loading' ? <SpeechButton icon="cancel" label="cancel audio" onClick={speech.stop} />
      : state?.status === 'playing' ? <><SpeechButton icon="pause" label="pause" onClick={speech.pause} /><SpeechButton icon="stop" label="stop" onClick={speech.stop} /></>
      : state?.status === 'paused' || state?.status === 'ready' ? <>
        <SpeechButton icon="play" label={state.status === 'paused' ? 'resume' : 'play again'} onClick={() => void speech.resume()} />
        <SpeechButton icon="stop" label="stop" onClick={speech.stop} />
        {selection && <SpeechButton icon="listen" label="listen to selection" preserveSelection onClick={listen} />}
      </> : <SpeechButton icon="listen" label={selection ? 'listen to selection' : 'listen'} preserveSelection onClick={listen} />}
    </div>
    {state?.status === 'error' && <span className="speech-feedback" role="alert">{state.error}</span>}
  </div>
}
