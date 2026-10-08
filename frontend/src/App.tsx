import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Composer } from './Composer'
import { PixelField } from './PixelField'
import { SourcePane } from './SourcePane'
import { SlidePage } from './SlidePage'
import { AnswerBody } from './AnswerBody'
import { SpeechControls } from './SpeechControls'
import { useSpeech } from './useSpeech'
import type { SpeechController } from './useSpeech'
import { APIError, ask, connect } from './api'
import { EXAMPLE_QUESTIONS, PHRASE, STORAGE_KEY, completeTurn, makeTurn, readSession } from './model'
import type { Quotation, Turn } from './model'

function Slogan({ reduced }: { reduced: boolean }) {
  const [count, setCount] = useState(0)
  useEffect(() => {
    if (reduced) return
    let next = 0
    let cancelled = false
    let timer: number | undefined
    setCount(0)
    function start() {
      if (cancelled) return
      timer = window.setInterval(() => { next += 1; setCount(next); if (next >= PHRASE.length) clearInterval(timer) }, 35)
    }
    void document.fonts.load('22px "Departure Mono"').then(start, start)
    return () => { cancelled = true; clearInterval(timer) }
  }, [reduced])
  const revealedCount = reduced ? PHRASE.length : count
  const cursorIndex = Math.max(0, PHRASE.slice(0, revealedCount).trimEnd().length - 1)
  return <h1 className="slogan" aria-label={PHRASE}><span aria-hidden="true">
    {Array.from(PHRASE.matchAll(/\S+/g), word => <span key={word.index}>
      <span className="slogan-word">{Array.from(word[0], (character, index) => <span className="slogan-character" key={index}
        style={{ visibility: word.index + index < revealedCount ? 'visible' : 'hidden' }}>
        {character}{word.index + index === cursorIndex && <span className={`type-cursor${revealedCount === 0 ? ' is-start' : ''}${!reduced && count >= PHRASE.length ? ' is-idle' : ''}`} />}
      </span>)}</span>
      {word.index + word[0].length < PHRASE.length ? ' ' : null}
    </span>)}
  </span></h1>
}

function CitationTile({ number, label, active, onClick }: {
  number: number; label: string; active: boolean; onClick: () => void
}) {
  return <button type="button" className="citation-tile" aria-label={label} aria-pressed={active} onClick={onClick}>{number}</button>
}

function RepositoryLink({ repository, children }: { repository: string; children: ReactNode }) {
  return <a href={`https://github.com/${repository}`} target="_blank" rel="noopener noreferrer">{children}</a>
}

function InformationDocument({ section, onClose }: { section: 'instructions' | 'about'; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = dialogRef.current!
    dialog.showModal()
    return () => dialog.close()
  }, [])
  function close() { dialogRef.current?.close(); onClose() }
  return <dialog ref={dialogRef} id={`${section}-panel`} className="instructions-document" aria-labelledby={`${section}-title`}
    onCancel={event => { event.preventDefault(); close() }}
    onClick={event => {
      if (event.target !== event.currentTarget) return
      const rect = event.currentTarget.getBoundingClientRect()
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close()
    }}>
    <header><h2 id={`${section}-title`}>{section}</h2><button type="button" onClick={close} aria-label={`Close ${section}`} autoFocus>close</button></header>
    {section === 'instructions' ? <div className="instructions-copy">
      <p>Ask a question or choose an example, then continue the conversation in the follow-up box. Press Enter to send, or Shift + Enter to start a new line.</p>
      <p>RAG on searches the course materials for each answer. Switch to RAG off to answer from the conversation and model knowledge without retrieving sources. You can switch at any time; the setting applies when you send the next message. A retry keeps that message’s original setting.</p>
      <p>Click a numbered citation to read its source beside the answer. The current citation is solid green in both panes. Other citations are dimmed. Each answer keeps its own sources.</p>
      <p>Click the speaker icon at the right of an answer’s footer to prepare and play its speech. Select text within an answer to listen to that passage. Use the pixel icons to pause, resume, or stop; hover for their labels. Short passages usually take about ten seconds or less to prepare; full answers can take longer. Replaying prepared audio does not synthesize it again. Changing chats or disconnecting stops playback.</p>
      <p>Lecture sources show the original slide page, including its figures and layout. Select text within a legal source to ask about that passage. Your question includes the selected text. Click outside the small composer or press Escape to dismiss it without sending.</p>
      <p>Use chat to start a new conversation and history to return to an earlier one. Delete removes a conversation from this browser tab. Deleting the open conversation returns you to a new chat.</p>
      <p>The theme control switches between light and dark. Motion turns animations on or off. With Motion off, the background shows a static frame and the typewriter and citation animations stop.</p>
      <p>Connect with the API key supplied by the service owner. The key stays in memory for this page; reconnect after reloading. Conversations are saved only in this browser tab. Clear them with the history delete buttons.</p>
      <p>Answers use the last five completed turns. The question, recent conversation, and any retrieved passages are sent to DeepSeek. Source selection adds the quoted passage to your question in either mode. An answer may include clearly labelled model knowledge without citations.</p>
    </div> : <div className="instructions-copy">
      <p>Course RAG brings together the GDPR, selected HIPAA provisions, and lecture slides for questions about medical data privacy and ethics. Built with the following technologies; each link opens its GitHub repository.</p>
      <p>Frontend: <RepositoryLink repository="react/react">React</RepositoryLink> and <RepositoryLink repository="microsoft/TypeScript">TypeScript</RepositoryLink> power the interface, with <RepositoryLink repository="vitejs/vite">Vite</RepositoryLink> for development and production builds.</p>
      <p>API: <RepositoryLink repository="python/cpython">Python</RepositoryLink>, <RepositoryLink repository="fastapi/fastapi">FastAPI</RepositoryLink>, and <RepositoryLink repository="Kludex/uvicorn">Uvicorn</RepositoryLink> serve the application and connect the browser to retrieval, answers, and speech.</p>
      <p>Retrieval: <RepositoryLink repository="qdrant/qdrant">Qdrant</RepositoryLink> stores document vectors. <RepositoryLink repository="QwenLM/Qwen3-Embedding">Qwen3 Embedding and Reranker</RepositoryLink> find and rank relevant passages, running locally with <RepositoryLink repository="vllm-project/vllm">vLLM</RepositoryLink>.</p>
      <p>Documents: <RepositoryLink repository="opendatalab/MinerU">MinerU</RepositoryLink> parses lecture PDFs, while a local Qwen vision model describes their figures. <RepositoryLink repository="pymupdf/PyMuPDF">PyMuPDF</RepositoryLink> renders the original slide pages shown beside answers.</p>
      <p>Answers: DeepSeek’s hosted API generates answers and rewrites follow-up questions through the <RepositoryLink repository="openai/openai-python">OpenAI Python SDK</RepositoryLink>. PDF processing, embeddings, reranking, and vector storage run locally.</p>
      <p>Speech: <RepositoryLink repository="QwenAudio/CosyVoice">CosyVoice</RepositoryLink> generates spoken answers locally using the Fun-CosyVoice3 model. Audio plays in the browser.</p>
    </div>}
  </dialog>
}

function ConnectionDialog({ onClose, onConnected }: { onClose: () => void; onConnected: (key: string) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { const dialog = dialogRef.current!; dialog.showModal(); return () => dialog.close() }, [])
  return <dialog ref={dialogRef} className="instructions-document connection-document" aria-labelledby="connection-title"
    onCancel={event => { event.preventDefault(); onClose() }}>
    <header><h2 id="connection-title">connect</h2><button type="button" onClick={onClose}>close</button></header>
    <form className="connection-form" onSubmit={async event => {
      event.preventDefault()
      if (!key.trim() || busy) return
      setBusy(true); setError('')
      try { await connect(key.trim()); onConnected(key.trim()) }
      catch (error) { setError(error instanceof Error ? error.message : 'Connection failed. Please retry.') }
      finally { setBusy(false) }
    }}>
      <p>Enter the API key supplied by the service owner. It stays in memory for this page and is not saved with your conversations.</p>
      <label htmlFor="api-key">API key</label>
      <input id="api-key" type="password" value={key} onChange={event => setKey(event.target.value)} autoComplete="off" autoFocus spellCheck={false} required disabled={busy} />
      {error && <p className="request-error" role="alert">{error}</p>}
      <button className="send-button" disabled={busy || !key.trim()}>{busy ? 'connecting…' : 'connect'}</button>
    </form>
  </dialog>
}

function Answer({ turn, number, active, onCitation, onRetry, onCancel, busy, speech }: {
  turn: Turn; number: number; active: { turnId: string; index: number } | null
  onCitation: (turnId: string, index: number) => void
  onRetry: (turn: Turn) => void; onCancel: () => void; busy: boolean; speech: SpeechController
}) {
  const contentRef = useRef<HTMLDivElement>(null)
  return <article className="turn" data-turn-id={turn.id}>
    <div className="user-message" lang="zh-CN" role="group" aria-labelledby={`question-label-${turn.id}`}>
      <div className="user-message-meta" lang="en">
        <span className="user-message-label" id={`question-label-${turn.id}`}>Your question</span>
        {!turn.ragEnabled && <><span aria-hidden="true">·</span><span className="turn-mode">RAG off</span></>}
      </div>
      {turn.quotation && <blockquote className="user-quotation"><span>{turn.quotation.source.label} / {turn.quotation.source.unit}</span>{turn.quotation.text}</blockquote>}
      <p>{turn.question}</p>
    </div>
    <div className="answer">
      <div className="answer-body" lang="zh-CN" aria-label="Answer" aria-busy={turn.status === 'pending'}>
        {turn.status === 'pending' ? <div className="request-status" role="status">Preparing an answer… <button type="button" onClick={onCancel}>cancel</button></div>
          : turn.status === 'error' ? <div className="request-error" role="alert"><p>{turn.error}</p><button type="button" disabled={busy} onClick={() => onRetry(turn)}>retry</button></div>
          : <>
            <div ref={contentRef} className="answer-content"><AnswerBody text={turn.answer} sources={turn.sources} activeIndex={active?.turnId === turn.id ? active.index : null}
              onCitation={index => onCitation(turn.id, index)} answerNumber={number} /></div>
            <div className="answer-footer">
              {turn.rewrittenQuestion !== turn.requestQuestion && <details className="rewritten-question"><summary>standalone question</summary><p>{turn.rewrittenQuestion}</p></details>}
              <SpeechControls turnId={turn.id} number={number} contentRef={contentRef} speech={speech} />
            </div>
          </>}
      </div>
    </div>
  </article>
}

export default function App() {
  const [session, setSession] = useState(readSession)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [documentOpen, setDocumentOpen] = useState<'instructions' | 'about' | null>(null)
  const [activeCitation, setActiveCitation] = useState<{ turnId: string; index: number } | null>(null)
  const [storageFailed, setStorageFailed] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const speech = useSpeech(session.activeId, apiKey)
  const [connectionOpen, setConnectionOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState<boolean | null>(null)
  const [directReady, setDirectReady] = useState<boolean | null>(null)
  const [notice, setNotice] = useState('')
  const requestRef = useRef<{ conversationId: string; controller: AbortController } | null>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const historyButtonRef = useRef<HTMLButtonElement>(null)
  const historyCloseButtonRef = useRef<HTMLButtonElement>(null)
  const instructionsButtonRef = useRef<HTMLButtonElement>(null)
  const aboutButtonRef = useRef<HTMLButtonElement>(null)
  const activeConversation = session.conversations.find(c => c.id === session.activeId)
  const turns = activeConversation?.turns ?? []
  const lastTurn = turns[turns.length - 1]
  const chosenTurn = turns.find(t => t.id === activeCitation?.turnId) ?? lastTurn
  const chosenIndex = activeCitation && chosenTurn?.id === activeCitation.turnId ? activeCitation.index : 0
  const chosenSource = chosenTurn?.sources[chosenIndex]
  const isSlide = chosenSource && (chosenSource.id.startsWith('slides:') || chosenSource.label === 'SLIDES' || !!chosenSource.file)
  const hasConversation = turns.length > 0

  function changeRag(enabled: boolean) {
    setSession(previous => ({ ...previous, ragEnabled: enabled }))
  }

  useEffect(() => {
    let closed = false
    async function check() {
      try {
        const response = await fetch('/health', { cache: 'no-store', signal: AbortSignal.timeout(10000) })
        const data = await response.json()
        if (!closed) {
          setReady(response.ok && data.ready === true)
          setDirectReady(response.ok && data.llm_configured === true)
        }
      } catch { if (!closed) { setReady(false); setDirectReady(false) } }
    }
    void check()
    const timer = window.setInterval(check, 30000)
    return () => { closed = true; clearInterval(timer) }
  }, [])

  useEffect(() => () => requestRef.current?.controller.abort(), [])

  useEffect(() => {
    document.documentElement.dataset.theme = session.theme
    document.documentElement.dataset.reduced = String(session.reducedMotion)
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session)); setStorageFailed(false) }
    catch { setStorageFailed(true) }
  }, [session])

  useEffect(() => {
    const element = transcriptRef.current
    if (element) element.scrollTo({ top: element.scrollHeight, behavior: 'instant' })
  }, [turns.length, lastTurn?.status, session.activeId])

  useEffect(() => {
    if (!historyOpen) return
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setHistoryOpen(false); historyButtonRef.current?.focus()
      }
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [historyOpen])

  async function runRequest(conversationId: string, turn: Turn, previous: Turn[]) {
    const controller = new AbortController()
    requestRef.current = { conversationId, controller }
    setBusy(true); setNotice('')
    let completed: Turn
    try { completed = completeTurn(turn, await ask(turn, previous, apiKey, controller.signal)) }
    catch (error) {
      completed = { ...turn, status: 'error', error: controller.signal.aborted
        ? 'Request cancelled. You can retry when ready.'
        : error instanceof Error ? error.message : 'The request failed. Please retry.' }
      if (error instanceof APIError && error.status === 401) { setApiKey(''); setConnectionOpen(true) }
    }
    // A deleted conversation stays deleted, and navigation cannot move an answer to another chat.
    setSession(previous => ({ ...previous, conversations: previous.conversations.map(conversation =>
      conversation.id === conversationId ? { ...conversation, turns: conversation.turns.map(item => item.id === turn.id ? completed : item) } : conversation) }))
    if (requestRef.current?.controller === controller) { requestRef.current = null; setBusy(false) }
  }

  function submit(question: string, quotation?: Quotation): boolean {
    if (requestRef.current) return false
    if (!apiKey) { setConnectionOpen(true); return false }
    let turn: Turn
    try { turn = makeTurn(question, quotation, session.ragEnabled) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Please shorten the question.'); return false }
    const conversationId = activeConversation?.id ?? crypto.randomUUID()
    setSession(previous => {
      const existing = previous.conversations.find(c => c.id === conversationId)
      if (existing) return { ...previous, conversations: previous.conversations.map(c => c.id === existing.id ? { ...c, turns: [...c.turns, turn] } : c) }
      const conversation = { id: conversationId, createdAt: Date.now(), turns: [turn] }
      return { ...previous, activeId: conversation.id, conversations: [conversation, ...previous.conversations] }
    })
    setActiveCitation({ turnId: turn.id, index: 0 })
    setHistoryOpen(false)
    setDocumentOpen(null)
    void runRequest(conversationId, turn, turns)
    return true
  }
  function retry(turn: Turn) {
    if (!activeConversation || requestRef.current) return
    if (!apiKey) { setConnectionOpen(true); return }
    const pending: Turn = { ...turn, status: 'pending', error: undefined }
    setSession(previous => ({ ...previous, conversations: previous.conversations.map(c => c.id === activeConversation.id
      ? { ...c, turns: c.turns.map(item => item.id === turn.id ? pending : item) } : c) }))
    setActiveCitation({ turnId: turn.id, index: 0 })
    void runRequest(activeConversation.id, pending, turns)
  }
  function newChat() {
    setSession(previous => ({ ...previous, activeId: null }))
    setActiveCitation(null); setHistoryOpen(false); setDocumentOpen(null)
  }
  function deleteConversation(id: string, button: HTMLButtonElement) {
    if (requestRef.current?.conversationId === id) requestRef.current.controller.abort()
    const row = button.closest('li')
    const nextRow = row?.nextElementSibling ?? row?.previousElementSibling
    const nextButton = nextRow?.querySelector<HTMLButtonElement>('.history-delete')
    setSession(previous => ({
      ...previous,
      conversations: previous.conversations.filter(conversation => conversation.id !== id),
      activeId: previous.activeId === id ? null : previous.activeId,
    }))
    if (session.activeId === id) setActiveCitation(null)
    ;(nextButton ?? historyCloseButtonRef.current)?.focus()
  }
  function openCitation(turnId: string, index: number) {
    const source = turns.find(turn => turn.id === turnId)?.sources[index]
    if (!source) return
    setActiveCitation({ turnId, index })
    setNotice('')
  }

  const connectionControls = <div className="connection-controls">
    <span role="status">{(session.ragEnabled ? ready : directReady) === false ? 'Service unavailable' : apiKey ? busy ? 'Answer in progress' : 'Connected' : 'API key required'}</span>
    <button type="button" disabled={busy} onClick={() => setConnectionOpen(true)}>{apiKey ? 'change key' : 'connect'}</button>
    {apiKey && <button type="button" disabled={busy} onClick={() => setApiKey('')}>disconnect</button>}
  </div>

  return <div className={`app-shell ${hasConversation ? 'conversation-mode' : ''}`}>
    <header className="masthead">
      <button type="button" className="brand" onClick={newChat} aria-label="Medical Data Privacy and Ethics in the Age of Artificial Intelligence — New chat"><span className="brand-title">Medical Data Privacy and Ethics<br />in the Age of Artificial Intelligence</span></button>
      <nav aria-label="Main navigation">
        <button type="button" onClick={newChat}><span className="nav-number">[1]</span> chat</button>
        <button type="button" ref={historyButtonRef} onClick={() => { setHistoryOpen(value => !value); setDocumentOpen(null) }} aria-expanded={historyOpen} aria-controls="history-panel"><span className="nav-number">[2]</span> history</button>
        <button type="button" onClick={() => setSession(previous => ({ ...previous, theme: previous.theme === 'light' ? 'dark' : 'light' }))} aria-label={`Switch to ${session.theme === 'light' ? 'dark' : 'light'} mode`}><span className="nav-number">[3]</span> {session.theme}</button>
        <button type="button" onClick={() => setSession(previous => ({ ...previous, reducedMotion: !previous.reducedMotion }))} aria-pressed={!session.reducedMotion}><span className="nav-number">[4]</span> Motion</button>
        <button type="button" ref={instructionsButtonRef} onClick={() => { setDocumentOpen('instructions'); setHistoryOpen(false) }} aria-haspopup="dialog" aria-expanded={documentOpen === 'instructions'} aria-controls="instructions-panel"><span className="nav-number">[5]</span> instructions</button>
        <button type="button" ref={aboutButtonRef} onClick={() => { setDocumentOpen('about'); setHistoryOpen(false) }} aria-haspopup="dialog" aria-expanded={documentOpen === 'about'} aria-controls="about-panel"><span className="nav-number">[6]</span> about</button>
      </nav>
    </header>

    <main className="workspace">
      <section className="left-panel" aria-label={hasConversation ? 'Conversation' : 'Introduction'}>
        {hasConversation ? <>
          <div className="transcript" ref={transcriptRef}>
            {turns.map((turn, index) => <Answer key={turn.id} turn={turn} number={index + 1}
              active={activeCitation ?? (chosenTurn ? { turnId: chosenTurn.id, index: chosenIndex } : null)}
              onCitation={openCitation} onRetry={retry} onCancel={() => requestRef.current?.controller.abort()} busy={busy} speech={speech} />)}
          </div>
          <div className="follow-up"><Composer compact onSend={submit} disabled={busy} ragEnabled={session.ragEnabled} onRagChange={changeRag} />{connectionControls}</div>
        </> : <>
          <div className="intro-body">
            <Slogan reduced={session.reducedMotion} />
          </div>
          <ol className="reference-list" aria-label="Course materials">
            {[
              ['GDPR', 'Articles and recitals'],
              ['HIPAA', 'Selected privacy and security provisions'],
              ['Lecture slides', 'Materials organised by lecture'],
            ].map(([label, description], index) => <li key={label}><span className="list-number">0{index + 1}</span><div><span>{label}</span><p>{description}</p></div></li>)}
          </ol>
        </>}
      </section>

      <section className="right-panel" aria-label={hasConversation ? 'Source workspace' : 'Chat workspace'}>
        <div className={`center-card ${hasConversation ? 'source-card' : 'welcome-card'}`}>
          {hasConversation && chosenSource && chosenTurn ? <>
            <header className="source-header"><div><span className="eyebrow">{isSlide ? 'Slides / original page' : `${chosenSource.label} / source passage`}</span><h2>{chosenSource.unit}</h2></div>
              {!isSlide && chosenSource.url && <a className="source-link" href={chosenSource.url} target="_blank" rel="noreferrer">{chosenSource.label === 'GDPR' ? 'EUR-Lex' : 'eCFR'} ↗</a>}
            </header>
            <div className="source-body"><div className="source-rail" aria-label="Sources for selected answer">{chosenTurn.sources.map((source, index) => <CitationTile key={source.id} number={source.number} label={`View source ${source.number}: ${source.unit}`} active={chosenIndex === index} onClick={() => openCitation(chosenTurn.id, index)} />)}</div>
              {isSlide ? <SlidePage key={`${chosenTurn.id}:${chosenSource.id}`} source={chosenSource} apiKey={apiKey} />
                : <SourcePane source={chosenSource} turnId={chosenTurn.id} theme={session.theme} reduced={session.reducedMotion} onAsk={submit} disabled={busy} ragEnabled={session.ragEnabled} onRagChange={changeRag} />}
            </div>
            <PixelField compact reduced={session.reducedMotion} dark={session.theme === 'dark'} />
          </> : hasConversation ? <>
            <div className="empty-source" role="status">{chosenTurn?.status === 'pending'
              ? chosenTurn.ragEnabled ? 'Sources will appear with the answer.' : 'RAG is off. Answering without retrieving sources.'
              : chosenTurn?.status === 'error' ? 'Retry the question to complete the answer.'
              : chosenTurn?.ragEnabled === false ? 'RAG was off for this answer. No sources were retrieved.'
              : 'This answer has no cited sources.'}</div>
            <PixelField reduced={session.reducedMotion} dark={session.theme === 'dark'} />
          </> : <>
            <div className="welcome-content">
              <Composer onSend={submit} disabled={busy} ragEnabled={session.ragEnabled} onRagChange={changeRag} />
              {connectionControls}
              <div className="example-questions" aria-label="Example questions">{EXAMPLE_QUESTIONS.map(question => <button type="button" key={question} disabled={busy} onClick={() => submit(question)}><span className="example-question-text" lang="zh-CN">{question}</span><span aria-hidden="true">↗</span></button>)}</div>
            </div>
            <PixelField reduced={session.reducedMotion} dark={session.theme === 'dark'} />
          </>}
        </div>
      </section>
    </main>

    {historyOpen && <><button type="button" className="history-backdrop" aria-label="Close history" onClick={() => setHistoryOpen(false)} />
      <aside id="history-panel" className="history-panel" aria-label="Conversation history"><header><button type="button" ref={historyCloseButtonRef} onClick={() => { setHistoryOpen(false); historyButtonRef.current?.focus() }} aria-label="Close history">×</button></header>
        {session.conversations.length === 0 ? <p className="history-empty">No conversations yet</p> : <ol>{session.conversations.map((conversation, index) => <li key={conversation.id}><button type="button" className="history-open" aria-current={session.activeId === conversation.id ? 'true' : undefined} onClick={() => {
          setSession(previous => ({ ...previous, activeId: conversation.id })); setActiveCitation(null); setHistoryOpen(false)
        }}><span className="history-index">{String(index + 1).padStart(2, '0')}</span><span className="history-title">{conversation.turns[0]?.question ?? 'New conversation'}</span></button>
          <button type="button" className="history-delete" aria-label={`Delete conversation: ${conversation.turns[0]?.question ?? 'New conversation'}`} onClick={event => deleteConversation(conversation.id, event.currentTarget)}>delete</button>
        </li>)}</ol>}
      </aside></>}
    {documentOpen && <InformationDocument section={documentOpen} onClose={() => {
      setDocumentOpen(null)
      ;(documentOpen === 'instructions' ? instructionsButtonRef : aboutButtonRef).current?.focus()
    }} />}
    {connectionOpen && <ConnectionDialog onClose={() => setConnectionOpen(false)} onConnected={key => { setApiKey(key); setConnectionOpen(false); setNotice('') }} />}
    {notice && <div role="alert" className="notice">{notice}<button type="button" onClick={() => setNotice('')} aria-label="Dismiss notice">×</button></div>}
    {storageFailed && <div role="status" className="storage-status">Conversation history cannot be saved in this tab.</div>}
  </div>
}
