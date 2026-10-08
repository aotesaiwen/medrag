import test from 'node:test'
import assert from 'node:assert/strict'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import { normalizeCollapsedTables } from '../src/markdown.ts'

const parser = unified().use(remarkParse).use(remarkGfm)
type SemanticNode = { type: string; value?: string; children?: SemanticNode[]; url?: string; align?: (string | null)[] | null }

function semanticTree(markdown: string): unknown {
  function content(node: SemanticNode): unknown {
    return { type: node.type, value: node.value, url: node.url, align: node.align, children: node.children?.map(content) }
  }
  return content(parser.parse(markdown))
}
const collapsed = '| 维度 | 世界 | 荒野 | |------|------|------| | 内容量 | 本体+冰原非常庞大，成熟完整 | 新作，后续更新和资料片还在展开 | | 画面与技术 | 当年顶尖，现在依然能打 | 更新，生态和天气系统更复杂 | | 上手难度 | 相对友好 | 机制更多，需要适应新系统 | | 稳定性/优化 | 经过多年打磨，较成熟 | 新作，早期可能存在优化问题 | | 价格 | 通常更便宜，常有折扣 | 新作价格较高 |'

function tableLines(markdown: string): string[] {
  return markdown.split(/\r?\n/).filter(line => line.trim().startsWith('|'))
}

test('repairs the complete Chinese comparison into a header, delimiter, and five data rows', () => {
  const result = normalizeCollapsedTables(`哪个更好？\n这其实取决于你在意什么：\n${collapsed}\n最后仍然取决于你的偏好。`)
  const rows = tableLines(result)
  assert.equal(rows.length, 7)
  assert.deepEqual(rows.map(row => row.split('|').slice(1, -1).map(cell => cell.trim())), [
    ['维度', '世界', '荒野'], ['------', '------', '------'],
    ['内容量', '本体+冰原非常庞大，成熟完整', '新作，后续更新和资料片还在展开'],
    ['画面与技术', '当年顶尖，现在依然能打', '更新，生态和天气系统更复杂'],
    ['上手难度', '相对友好', '机制更多，需要适应新系统'],
    ['稳定性/优化', '经过多年打磨，较成熟', '新作，早期可能存在优化问题'],
    ['价格', '通常更便宜，常有折扣', '新作价格较高'],
  ])
  assert.match(result, /取决于你在意什么：\n\n\|/)
  assert.match(result, /新作价格较高 \|\n\n最后/)
  const document = parser.parse(result)
  assert.deepEqual(document.children.map(node => node.type), ['paragraph', 'table', 'paragraph'])
  const table = document.children.find(node => node.type === 'table')!
  assert.equal(table.children.length, 6)
  assert.ok(table.children.every(row => row.children.length === 3))
})

test('preserves cell formatting, citation markers, escaped pipes, code spans, and alignment', () => {
  const result = normalizeCollapsedTables('| 特征 | 说明 | | :--- | ---: | | **条件** [1] | A\\|B 与 `x\\|y` | | *例外* [2] | ``a`\\|b`` 和 [法规](https://example.org) |')
  const rows = tableLines(result)
  assert.equal(rows.length, 4)
  assert.ok(rows[1].includes(':---') && rows[1].includes('---:'))
  assert.ok(rows[2].includes('**条件** [1]'))
  assert.ok(rows[2].includes('A\\|B 与 `x\\|y`'))
  assert.ok(rows[3].includes('``a`\\|b`` 和 [法规](https://example.org)'))
  const table = parser.parse(result).children.find(node => node.type === 'table')!
  assert.deepEqual(table.align, ['left', 'right'])
  assert.ok(table.children.every(row => row.children.length === 2))
  assert.equal(table.children[1].children[0].children[0].type, 'strong')
  const code = table.children.flatMap(row => row.children.flatMap(cell => cell.children))
    .filter(node => node.type === 'inlineCode').map(node => node.value)
  assert.deepEqual(code, ['x|y', 'a`|b'])
})

test('empty cells stay within their inferred rows instead of becoming row boundaries', () => {
  const result = normalizeCollapsedTables('| 名称 | | 备注 | | --- | --- | --- | | A | | 待补充 | | | B | |')
  const rows = tableLines(result)
  assert.equal(rows.length, 4)
  assert.deepEqual(rows.map(row => row.split('|').slice(1, -1).map(cell => cell.trim())), [
    ['名称', '', '备注'], ['---', '---', '---'], ['A', '', '待补充'], ['', 'B', ''],
  ])
  const table = parser.parse(result).children.find(node => node.type === 'table')!
  assert.deepEqual(table.children.map(row => row.children.map(cell => cell.children.length)), [
    [1, 0, 1], [1, 0, 1], [0, 1, 0],
  ])
})

test('leaves prose, malformed widths, absent row boundaries, and incomplete separators alone', () => {
  const examples = [
    'Choose A | B; neither is a table.',
    '| A | B | | words | words | | C | D |',
    '| A | B | | -- | --- | | C | D |',
    '| A | B | | --- | --- | | C | D | E |',
    '| A | B | prose | --- | --- | | C | D |',
    '| A | B || --- | --- || C | D |',
    '| A | B | | --- | --- |',
    '| A | B | | --- | --- | | `unclosed | D |',
    '| A | B | | --- | --- | | `x|y` | D |',
    `Explanation: ${collapsed}`,
  ]
  for (const example of examples) {
    const result = normalizeCollapsedTables(example)
    assert.ok(parser.parse(result).children.every(node => node.type !== 'table'))
    assert.deepEqual(semanticTree(result), semanticTree(example))
  }
})

test('does not rewrite fenced, indented, inline, or multiline code spans', () => {
  const examples = [
    `\`\`\`markdown\n${collapsed}\n\`\`\``,
    `~~~~\n${collapsed}\n~~~\n${collapsed}\n~~~~`,
    `    ${collapsed}`,
    `\t${collapsed}`,
    `\`${collapsed}\``,
    `Before \`\`a code span\n${collapsed}\nends here\`\` after.`,
  ]
  for (const example of examples) assert.deepEqual(semanticTree(normalizeCollapsedTables(example)), semanticTree(example))
  const afterFence = normalizeCollapsedTables(`\`\`\`\n${collapsed}\n\`\`\`\n\n${collapsed}`)
  assert.equal(tableLines(afterFence).length, 8)
})

test('preserves normal multiline tables even when one row resembles a collapsed table', () => {
  const normal = '| A | B |\n| --- | --- |\n| One | Two |'
  assert.deepEqual(semanticTree(normalizeCollapsedTables(normal)), semanticTree(normal))
  const wideHeader = '| A | B | | --- | --- | | C | D |'
  const wideTable = `${wideHeader}\n${'| --- '.repeat(8)}|\n| A | B |`
  assert.deepEqual(semanticTree(normalizeCollapsedTables(wideTable)), semanticTree(wideTable))
  const embedded = `A | B\n--- | ---\n${collapsed}`
  assert.deepEqual(semanticTree(normalizeCollapsedTables(embedded)), semanticTree(embedded))
})

test('repeated normalization is stable and CRLF documents retain separate rows', () => {
  const result = normalizeCollapsedTables(`Intro\r\n${collapsed}\r\nAfter`)
  assert.equal(tableLines(result).length, 7)
  assert.deepEqual(semanticTree(normalizeCollapsedTables(result)), semanticTree(result))
  assert.match(result, /Intro\r\n\r\n\|/)
  assert.match(result, /新作价格较高 \|\r\n\r\nAfter/)
})
