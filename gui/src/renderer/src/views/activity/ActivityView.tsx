import { Construction } from 'lucide-react'
import { Empty } from '@renderer/components/ui'

export default function ActivityView() {
  return (
    <div className="page">
      <div className="page-inner">
        <Empty icon={Construction} title="ActivityView">This view is being built.</Empty>
      </div>
    </div>
  )
}
