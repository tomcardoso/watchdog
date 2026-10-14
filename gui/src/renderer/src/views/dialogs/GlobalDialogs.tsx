// App-wide dialogs opened from menus, the palette or the drop zone: Add documents (with the
// public-records gate), New investigation, Fetch links. Listens for `wd:command` events.

import { AddDialog } from './AddDialog'
import { FetchLinksDialog, NewInvestigationDialog } from './SmallDialogs'
import './dialogs.css'

export function GlobalDialogs() {
  return (
    <>
      <AddDialog />
      <NewInvestigationDialog />
      <FetchLinksDialog />
    </>
  )
}
