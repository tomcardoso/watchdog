// Renders a vault note's markdown the way Obsidian would, minus Obsidian: `[[wikilinks]]` become
// in-app navigation, `> [!contradiction]` callouts get their own styling, and raw HTML (the
// pipeline's `<!-- … -->` markers) is dropped, never rendered. A link to a fact's line
// (`[[documents/<slug>#^f-…|p. 4]]`, D283) is checked against the stored facts and shown as a
// fact citation: a link with the fact on hover, labelled when disputed, "source not found" when
// it names no stored fact.

import { memo, ReactNode, useCallback, useMemo } from 'react'
import ReactMarkdown, { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { call, useRpc } from '@renderer/lib/rpc'
import { FactCite, citationKey, citationKeys } from '@renderer/components/FactCite'
import { navigate, toast, useVault } from '@renderer/lib/store'
import '@renderer/styles/markdown.css'

const WIKILINK = /(!?)\[\[([^\]|#\n]+)(#[^\]|\n]*)?(?:\|([^\]\n]+))?\]\]/g

/** `[[target#frag|text]]` → `[text](wikilink:target%23frag)` so react-markdown parses it as a link. */
export function wikilinksToLinks(md: string): string {
  return md.replace(WIKILINK, (_m, _bang, target: string, frag: string | undefined, text: string | undefined) => {
    const label = (text ?? target.split('/').pop() ?? target).replace(/([[\]])/g, '\\$1')
    const href = 'wikilink:' + encodeURIComponent(target.trim() + (frag ?? ''))
    return `[${label}](${href})`
  })
}

// HTML comments (`<!-- Journalist annotations — never overwritten. -->`) are notes for whoever
// edits the file; react-markdown would otherwise print them as text.
const stripComments = (s: string) => s.replace(/<!--[\s\S]*?-->/g, '')

type MdNode = { type: string; value?: string; children?: MdNode[]; data?: Record<string, unknown> }

/** remark plugin: Obsidian callouts (`> [!kind] Title`) → blockquote with class md-callout-kind. */
function remarkCallouts() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (node.type === 'blockquote') {
        const para = node.children?.[0]
        const first = para?.children?.[0]
        if (para?.type === 'paragraph' && first?.type === 'text' && first.value) {
          const m = /^\[!([a-z-]+)\][+-]?[ \t]*/i.exec(first.value)
          if (m) {
            first.value = first.value.slice(m[0].length)
            node.data = { ...(node.data ?? {}), hProperties: { className: ['md-callout', `md-callout-${m[1].toLowerCase()}`], 'data-callout': m[1].toLowerCase() } }
          }
        }
      }
      node.children?.forEach(walk)
    }
    walk(tree)
  }
}

const urlTransform = (url: string) => (/^(wikilink:|https?:|mailto:|#)/i.test(url) ? url : '')

/** Follow a wikilink target inside the open vault: entity, document (with page), or any note. */
export function useOpenWikilink(): (target: string) => Promise<void> {
  const vault = useVault()
  return useCallback(
    async (target: string) => {
      try {
        const r = await call('vault.resolveLink', { vault, target })
        if (r.kind === 'entity' && r.path) {
          const id = r.path.replace(/\.md$/, '').split('/').pop()!
          navigate({ view: 'entity', id })
        } else if ((r.kind === 'document' || r.kind === 'original' || r.kind === 'fulltext') && r.sha) {
          navigate({ view: 'document', sha: r.sha, page: r.page ?? undefined })
        } else if (r.path) {
          navigate({ view: 'note', path: r.path })
        } else {
          toast({ kind: 'info', title: 'Note not found', body: target })
        }
      } catch (e) {
        toast({ kind: 'error', title: 'Could not open link', body: String((e as Error).message) })
      }
    },
    [vault]
  )
}

function MarkdownImpl({ text, className, onWikilink, compact }: { text: string; className?: string; onWikilink?: (target: string) => void; compact?: boolean }) {
  const open = useOpenWikilink()
  const vault = useVault()
  const follow = onWikilink ?? ((t: string) => void open(t))
  const keys = useMemo(() => citationKeys(text), [text])
  const cites = useRpc('vault.citations', vault && keys.length ? { vault, links: keys } : null, { staleTime: 30_000 })
  const components: Components = {
    a: ({ href, children }) => {
      if (href?.startsWith('wikilink:')) {
        const target = decodeURIComponent(href.slice('wikilink:'.length))
        const key = citationKey(target)
        if (key) return <FactCite label={children} status={cites.data?.[key] ?? (cites.isError ? { target, block: '', status: 'found', fact: null } : undefined)} />
        const isPage = /#page=\d+/.test(target)
        return (
          <a
            href="#"
            className={isPage ? 'wikilink cite' : 'wikilink'}
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              follow(target)
            }}
          >
            {children}
          </a>
        )
      }
      return (
        <a
          href={href}
          onClick={(e) => {
            e.preventDefault()
            if (href && /^(https?|mailto):/i.test(href)) void window.watchdog.shell.openExternal(href)
          }}
        >
          {children}
        </a>
      )
    },
    table: ({ children }) => (
      <div className="md-table-wrap">
        <table>{children}</table>
      </div>
    )
  }
  return (
    <div className={['md', 'selectable', compact && 'md-compact', className].filter(Boolean).join(' ')}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkCallouts]} urlTransform={urlTransform} components={components}>
        {wikilinksToLinks(stripComments(text))}
      </ReactMarkdown>
    </div>
  )
}

export const Markdown = memo(MarkdownImpl)

/** Strip markdown to a one-line plain preview (for cards and lists). */
export function plainText(md: string | null | undefined, max = 240): string {
  if (!md) return ''
  const s = md
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(WIKILINK, (_m, _b, target: string, _f, text?: string) => text ?? target.split('/').pop() ?? target)
    .replace(/[*_`>#]+/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  return s.length > max ? s.slice(0, max - 1).trimEnd() + '…' : s
}

export function MarkdownSection({ title, text, empty }: { title: ReactNode; text: string | null | undefined; empty?: ReactNode }) {
  return (
    <section className="md-section">
      <h3 className="md-section-title">{title}</h3>
      {text && text.trim() ? <Markdown text={text} /> : <div className="faint">{empty ?? 'Nothing here yet.'}</div>}
    </section>
  )
}
