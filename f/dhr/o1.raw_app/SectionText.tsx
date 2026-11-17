import React, { useEffect, useState } from 'react'
import { backend } from './wmill'
import { message } from './shared'

// What f/dhr/o1_sections returns about a review's sections and blocks.
export type Section = {
  section_id: string
  parent_id: string | null
  level: number
  heading: string
  position: number
  first_page: number
  last_page: number
}
export type GroupedSection = Section & {
  last_position: number
  n_blocks: number // directly in it
  n_inside: number // everything inside it, its subsections too
}
export type Block = {
  position: number
  page: number
  block_type: string
  section_id: string | null
  level: number | null // the level of the section the block sits directly in
  heading: string | null
  text: string
}

// Pages count from 0 in the database (as in the block ids); people count from 1.
export function pages(first: number, last: number): string {
  return first === last ? `page ${first + 1}` : `pages ${first + 1}–${last + 1}`
}

// The headings a section sits under, outermost first.
export function parents<S extends Section>(section: S, byId: Map<string, S>): S[] {
  const result: S[] = []
  let parent = section.parent_id ? byId.get(section.parent_id) : undefined
  while (parent) {
    result.unshift(parent)
    parent = parent.parent_id ? byId.get(parent.parent_id) : undefined
  }
  return result
}

// Datalab writes a table's text as one row per line, cells split by " | ".
function TableBlock({ text }: { text: string }) {
  const rows = text.split('\n').map((line) => line.split(' | '))
  const [head, ...body] = rows
  return (
    <div className="block table-block">
      <table>
        <thead>
          <tr>
            {head.map((cell, i) => (
              <th key={i}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r}>
              {row.map((cell, i) => (
                <td key={i}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OneBlock({ block, top }: { block: Block; top: number }) {
  switch (block.block_type) {
    case 'SectionHeader': {
      const depth = Math.max(0, Math.min(3, (block.level ?? top) - top))
      return <div className={`block block-heading depth-${depth}`}>{block.text}</div>
    }
    case 'Table':
      return <TableBlock text={block.text} />
    case 'Footnote':
      return <p className="block footnote">{block.text}</p>
    case 'Picture':
    case 'Figure':
      return (
        <p className="block picture">
          <span className="block-type">{block.block_type}:</span> {block.text}
        </p>
      )
    case 'TableOfContents':
      return (
        <p className="block contents">
          <span className="block-type">Contents:</span> {block.text}
        </p>
      )
    default:
      return <p className="block">{block.text}</p>
  }
}

// Blocks in reading order, with a marker wherever a new page starts. Headings are sized by
// how deep they sit below the first one shown (top).
export function Blocks({ blocks, top }: { blocks: Block[]; top: number }) {
  let page = -1
  return (
    <div className="blocks">
      {blocks.map((b) => {
        const newPage = b.page !== page
        page = b.page
        return (
          <React.Fragment key={b.position}>
            {newPage && <div className="page-mark">page {b.page + 1}</div>}
            <OneBlock block={b} top={top} />
          </React.Fragment>
        )
      })}
    </div>
  )
}

// A section's text, its subsections included, fetched when shown.
export function SectionText({
  dataset,
  docId,
  section,
}: {
  dataset: string
  docId: string
  section: Section
}) {
  const [blocks, setBlocks] = useState<Block[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    setBlocks(null)
    setError(null)
    backend
      .sections({ dataset, doc_id: docId, section_id: section.section_id })
      .then((result: { blocks: Block[] }) => current && setBlocks(result.blocks))
      .catch((e: unknown) => current && setError(message(e)))
    return () => {
      current = false
    }
  }, [dataset, docId, section.section_id])

  if (error) return <p className="message error">Couldn’t read the section’s text: {error}</p>
  if (!blocks) return <p className="muted">Reading the section…</p>
  // Its own heading is already shown above it.
  const inside = blocks.filter((b) => b.position !== section.position)
  if (inside.length === 0) return <p className="muted">Nothing comes under this heading: the next heading follows it straight away.</p>
  return <Blocks blocks={inside} top={section.level} />
}
