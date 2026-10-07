import { Construction } from 'lucide-react'
import { Empty } from '@renderer/components/ui'

export default function DocumentView() {
  return (
    <div className="page">
      <div className="page-inner">
        <Empty icon={Construction} title="DocumentView">This view is being built.</Empty>
      </div>
    </div>
  )
}
