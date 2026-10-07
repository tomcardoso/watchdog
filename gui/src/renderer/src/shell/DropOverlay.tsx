// Drop files anywhere on the window to add them to the open investigation.

import { FileDown } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useApp } from '@renderer/lib/store'

export function DropOverlay() {
  const project = useApp((s) => s.project)
  const openAdd = useApp((s) => s.openAdd)
  const [over, setOver] = useState(false)
  useEffect(() => {
    let depth = 0
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files')
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth++
      setOver(true)
    }
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return
      depth = Math.max(0, depth - 1)
      if (!depth) setOver(false)
    }
    const overFn = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault()
    }
    const drop = (e: DragEvent) => {
      e.preventDefault()
      depth = 0
      setOver(false)
      if (!project) return
      const paths = Array.from(e.dataTransfer?.files ?? []).map((f) => window.watchdog.files.pathForFile(f)).filter(Boolean)
      if (paths.length) openAdd(paths)
    }
    window.addEventListener('dragenter', enter)
    window.addEventListener('dragleave', leave)
    window.addEventListener('dragover', overFn)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('dragover', overFn)
      window.removeEventListener('drop', drop)
    }
  }, [project, openAdd])
  if (!over || !project) return null
  return (
    <div className="drop-overlay">
      <div className="box">
        <FileDown />
        <div className="t">Drop to add to {project.name}</div>
        <div className="muted">Files are copied in — the originals stay where they are.</div>
      </div>
    </div>
  )
}
