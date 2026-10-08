import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { normalizeCollapsedTables } from './markdown'
import type { Source } from './model'

type Node = { type: string; value?: string; url?: string; children?: Node[] }

// Convert only verified citation numbers, never code or existing links.
function citations(numbers: Set<number>) {
  return () => (tree: Node) => {
    function visit(node: Node) {
      if (!node.children || ['link', 'code', 'inlineCode'].includes(node.type)) return
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) { visit(child); return [child] }
        const result: Node[] = []
        let cursor = 0
        for (const match of child.value.matchAll(/\[(\d+)\]/g)) {
          const number = Number(match[1])
          if (!numbers.has(number)) continue
          if (match.index > cursor) result.push({ type: 'text', value: child.value.slice(cursor, match.index) })
          result.push({ type: 'link', url: `#citation-${number}`, children: [{ type: 'text', value: String(number) }] })
          cursor = match.index + match[0].length
        }
        if (cursor < child.value.length) result.push({ type: 'text', value: child.value.slice(cursor) })
        return result
      })
    }
    visit(tree)
  }
}

export function AnswerBody({ text, sources, activeIndex, onCitation, answerNumber }: {
  text: string; sources: Source[]; activeIndex: number | null
  onCitation: (index: number) => void; answerNumber: number
}) {
  return <Markdown skipHtml remarkPlugins={[remarkGfm, citations(new Set(sources.map(source => source.number)))]}
    components={{
      img: () => null,
      table: ({ children }) => <div className="answer-table-scroll" role="region" aria-label="Answer table" tabIndex={0}>
        <table>{children}</table>
      </div>,
      a: ({ href, children }) => {
        const number = /^#citation-(\d+)$/.exec(href ?? '')
        const index = number ? sources.findIndex(source => source.number === Number(number[1])) : -1
        if (index >= 0) return <button type="button" className="citation-tile"
          aria-label={`Open citation ${sources[index].number}, answer ${answerNumber}`}
          aria-pressed={activeIndex === index} onClick={() => onCitation(index)}>{sources[index].number}</button>
        return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
      },
    }}>{normalizeCollapsedTables(text)}</Markdown>
}
