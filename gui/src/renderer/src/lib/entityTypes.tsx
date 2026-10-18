// The six canonical entity types (pipeline/entity_type.py): label, icon and colour token.

import { Briefcase, Building2, Gavel, Landmark, LucideIcon, MapPin, User, Shapes } from 'lucide-react'

export interface TypeMeta { label: string; plural: string; icon: LucideIcon; color: string; blurb: string }

export const TYPE_META: Record<string, TypeMeta> = {
  person: { label: 'Person', plural: 'People', icon: User, color: 'var(--person)', blurb: 'People' },
  organization: { label: 'Organization', plural: 'Organizations', icon: Building2, color: 'var(--organization)', blurb: 'Companies, banks, unions, funds, non-profits' },
  'public-body': { label: 'Public body', plural: 'Public bodies', icon: Landmark, color: 'var(--public-body)', blurb: 'Governments, regulators, courts, agencies' },
  place: { label: 'Place', plural: 'Places', icon: MapPin, color: 'var(--place)', blurb: 'Addresses, properties, locations' },
  asset: { label: 'Asset', plural: 'Assets', icon: Briefcase, color: 'var(--asset)', blurb: 'Vehicles, accounts, domains, shares' },
  proceeding: { label: 'Proceeding', plural: 'Proceedings', icon: Gavel, color: 'var(--proceeding)', blurb: 'Lawsuits, insolvencies, inquiries' }
}

const FALLBACK: TypeMeta = { label: 'Entity', plural: 'Entities', icon: Shapes, color: 'var(--text-3)', blurb: '' }

export function typeMeta(type: string | null | undefined): TypeMeta {
  return (type && TYPE_META[type]) || FALLBACK
}
