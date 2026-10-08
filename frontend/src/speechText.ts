// Read the rendered answer, preserving paragraph/cell boundaries and omitting
// citation controls. This also handles Markdown tables repaired for display.
export function speechText(element: HTMLElement): string {
  const blocks = new Set(['P', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'TR', 'BLOCKQUOTE'])
  function read(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? ''
    if (!(node instanceof HTMLElement)) return ''
    if (node.matches('button, img, script, style, [aria-hidden="true"]')) return ''
    if (node.tagName === 'BR') return '\n'
    const content = Array.from(node.childNodes, read).join('')
    if (node.tagName === 'TH' || node.tagName === 'TD') return `${content}；`
    return blocks.has(node.tagName) ? `${content}\n` : content
  }
  return read(element).replace(/[ \t]+/g, ' ').replace(/\n\s*\n/g, '\n').trim()
}
