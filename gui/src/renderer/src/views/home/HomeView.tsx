import { Construction } from 'lucide-react'
import { Empty } from '@renderer/components/ui'

export default function HomeView() {
  return (
    <div className="page">
      <div className="page-inner">
        <Empty icon={Construction} title="HomeView">This view is being built.</Empty>
      </div>
    </div>
  )
}
