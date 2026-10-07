import { Construction } from 'lucide-react'
import { Empty } from '@renderer/components/ui'

export default function GraphView() {
  return (
    <div className="page">
      <div className="page-inner">
        <Empty icon={Construction} title="GraphView">This view is being built.</Empty>
      </div>
    </div>
  )
}
