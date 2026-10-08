const separatorCell = /^[ \t]*:?-{3,}:?[ \t]*$/

function backtickEnd(text: string, start: number): number {
  let end = start + 1
  while (text[end] === '`') end++
  return end
}

// Backslashes are literal inside code spans. Only a run of the same length
// closes a span; a longer run is part of its contents.
function scanInline(text: string, openTicks = 0): { pipes: number[]; openTicks: number; unescapedCodePipe: boolean } {
  const pipes: number[] = []
  let unescapedCodePipe = false
  for (let index = 0; index < text.length; index++) {
    const character = text[index]
    if (!openTicks && character === '\\') { index++; continue }
    if (character === '`') {
      const end = backtickEnd(text, index)
      const length = end - index
      if (!openTicks) openTicks = length
      else if (openTicks === length) openTicks = 0
      index = end - 1
    } else if (character === '|') {
      if (!openTicks) pipes.push(index)
      else {
        let slashes = 0
        while (text[index - slashes - 1] === '\\') slashes++
        if (slashes % 2 === 0) unescapedCodePipe = true
      }
    }
  }
  return { pipes, openTicks, unescapedCodePipe }
}

function cells(text: string, pipes: number[]): string[] {
  const boundaries = [-1, ...pipes, text.length]
  const result = boundaries.slice(1).map((end, index) => text.slice(boundaries[index] + 1, end))
  if (!result[0].trim()) result.shift()
  if (result.length && !result[result.length - 1].trim()) result.pop()
  return result
}

function repairLine(line: string): string[] | null {
  const text = line.trim()
  if (!text.startsWith('|') || !text.endsWith('|')) return null
  const { pipes, openTicks, unescapedCodePipe } = scanInline(text)
  // GFM still splits an unescaped pipe inside backticks. Leave such text
  // untouched rather than repair it into a table with corrupted code cells.
  if (openTicks || unescapedCodePipe || pipes[0] !== 0 || pipes[pipes.length - 1] !== text.length - 1) return null
  const segments = pipes.slice(1).map((end, index) => text.slice(pipes[index] + 1, end))
  const candidates: string[][] = []

  // A contiguous run of delimiter cells supplies the width. Its position
  // must then fit exactly one header row followed by the delimiter row.
  for (let start = 0; start < segments.length;) {
    if (!separatorCell.test(segments[start])) { start++; continue }
    let end = start + 1
    while (end < segments.length && separatorCell.test(segments[end])) end++
    const columns = end - start
    const stride = columns + 1
    if (columns >= 2 && start === stride && (segments.length + 1) % stride === 0) {
      const rowCount = (segments.length + 1) / stride
      const boundariesValid = Array.from({ length: rowCount - 1 }, (_, row) =>
        /^[ \t]+$/.test(segments[(row + 1) * stride - 1])).every(Boolean)
      if (rowCount >= 3 && boundariesValid) {
        const indentation = line.match(/^ */)![0]
        candidates.push(Array.from({ length: rowCount }, (_, row) => {
          const first = row * stride
          return indentation + text.slice(pipes[first], pipes[first + columns] + 1)
        }))
      }
    }
    start = end
  }
  return candidates.length === 1 ? candidates[0] : null
}

/** Repair only complete, rectangular tables whose row newlines collapsed. */
export function normalizeCollapsedTables(markdown: string): string {
  const lines = markdown.split('\n')
  const eligible: boolean[] = []
  let fence: { character: string; length: number } | null = null
  let openTicks = 0

  for (const line of lines) {
    const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line.trimEnd())
    if (fence) {
      eligible.push(false)
      if (marker && marker[1][0] === fence.character && marker[1].length >= fence.length && !marker[2].trim()) fence = null
      continue
    }
    if (marker && (marker[1][0] === '~' || !marker[2].includes('`'))) {
      fence = { character: marker[1][0], length: marker[1].length }
      eligible.push(false); openTicks = 0; continue
    }
    if (/^(?: {4}| *\t)/.test(line)) { eligible.push(false); continue }
    if (!line.trim()) openTicks = 0
    eligible.push(!openTicks)
    openTicks = scanInline(line, openTicks).openTicks
  }

  // A line that happens to resemble a collapsed table may already be a cell
  // in a normal multiline table. Protect the whole existing table block.
  const existingTable = new Set<number>()
  for (let index = 1; index < lines.length; index++) {
    if (existingTable.has(index) || !eligible[index] || !eligible[index - 1]) continue
    const delimiter = scanInline(lines[index])
    const header = scanInline(lines[index - 1])
    const delimiterCells = cells(lines[index], delimiter.pipes)
    if (!delimiter.pipes.length || !header.pipes.length || delimiter.openTicks || header.openTicks
      || !delimiterCells.every(cell => separatorCell.test(cell))
      || cells(lines[index - 1], header.pipes).length !== delimiterCells.length) continue
    existingTable.add(index - 1)
    for (let row = index; row < lines.length && eligible[row] && lines[row].trim(); row++) existingTable.add(row)
  }

  const output: string[] = []
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]
    const repaired = eligible[index] && !existingTable.has(index) ? repairLine(line) : null
    if (!repaired) { output.push(line); continue }
    const carriageReturn = line.endsWith('\r') ? '\r' : ''
    const newline = carriageReturn + '\n'
    if (output.length && output[output.length - 1].trim()) output.push(carriageReturn)
    output.push(repaired.join(newline) + carriageReturn)
    if (index + 1 < lines.length && lines[index + 1].trim()) output.push(carriageReturn)
  }
  return output.join('\n')
}
