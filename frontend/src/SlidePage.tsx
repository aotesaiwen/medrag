import { useEffect, useState } from 'react'
import { fetchSlidePage } from './api'
import type { Source } from './model'

export function SlidePage({ source, apiKey }: { source: Source; apiKey: string }) {
  const [preview, setPreview] = useState<{ url?: string; error?: string }>({})
  const [attempt, setAttempt] = useState(0)
  const file = source.file
  const page = source.page ?? 0
  const validPage = Number.isSafeInteger(page) && page > 0

  useEffect(() => {
    setPreview({})
    if (!apiKey || !file || !validPage) return
    const controller = new AbortController()
    let imageUrl: string | undefined
    void fetchSlidePage(file, page, apiKey, controller.signal).then(blob => {
      if (controller.signal.aborted) return
      imageUrl = URL.createObjectURL(blob)
      setPreview({ url: imageUrl })
    }).catch(error => {
      if (!controller.signal.aborted) {
        setPreview({ error: error instanceof Error ? error.message : 'The slide could not be loaded. Please retry.' })
      }
    })
    return () => {
      controller.abort()
      if (imageUrl) URL.revokeObjectURL(imageUrl)
    }
  }, [apiKey, file, page, validPage, attempt])

  return <div className="slide-viewer" role="region" aria-label="Original slide page">
    {!file || !validPage ? <p className="request-error" role="alert">The original page is unavailable for this slide.</p>
      : !apiKey ? <p className="request-status" role="status">Connect to view this slide.</p>
      : preview.error ? <div className="request-error" role="alert"><p>{preview.error}</p>
        <button type="button" onClick={() => setAttempt(value => value + 1)}>retry slide</button>
      </div>
      : preview.url ? <img className="slide-page" src={preview.url} alt={`${source.unit} — original slide page`}
        onError={() => setPreview({ error: 'The slide image could not be displayed. Please retry.' })} />
      : <p className="request-status" role="status">Loading slide…</p>}
  </div>
}
