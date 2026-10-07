// An entity as a small type-coloured chip or avatar; clicking opens its page.

import { CSSProperties } from 'react'
import { typeMeta } from '@renderer/lib/entityTypes'
import { navigate } from '@renderer/lib/store'

export function EntityChip({ id, name, type, onClick }: { id: string; name: string; type: string; onClick?: () => void }) {
  const m = typeMeta(type)
  return (
    <button
      type="button"
      className="entity-chip"
      style={{ '--chip-color': m.color } as CSSProperties}
      onClick={(e) => {
        e.stopPropagation()
        if (onClick) onClick()
        else navigate({ view: 'entity', id })
      }}
      title={`${m.label}: ${name}`}
    >
      <span className="dot">
        <m.icon />
      </span>
      <span className="truncate">{name}</span>
    </button>
  )
}

export function EntityAvatar({ type, size = 36 }: { type: string; size?: number }) {
  const m = typeMeta(type)
  return (
    <span className="entity-avatar" style={{ '--chip-color': m.color, width: size, height: size, borderRadius: size * 0.3 } as CSSProperties}>
      <m.icon style={{ width: size * 0.48, height: size * 0.48 }} />
    </span>
  )
}

export function TypeDot({ type, size = 8 }: { type: string; size?: number }) {
  return <span style={{ display: 'inline-block', width: size, height: size, borderRadius: '50%', background: typeMeta(type).color, flex: 'none' }} />
}
