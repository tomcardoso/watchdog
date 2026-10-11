// Keeps a fault in one screen from blanking the whole window. Without it, an exception while a
// view renders unmounts the entire app, sidebar included, and only a reload brings it back. The
// boundary shows the error in place of the view and clears itself when the person goes elsewhere.

import { Component, ReactNode } from 'react'
import { Button, Callout } from '@renderer/components/ui'

interface Props {
  /** Changes whenever the route does; a new route clears the error. */
  resetKey: string
  children: ReactNode
}

export class ViewBoundary extends Component<Props, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('A screen failed to render:', error)
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={{ padding: 32, maxWidth: 720 }}>
        <Callout tone="danger" title="This screen could not be shown" action={<Button size="sm" onClick={() => this.setState({ error: null })}>Try again</Button>}>
          Something went wrong while drawing it. Your investigation is not affected. Go to another screen from the sidebar, or try again.
          <div className="selectable faint" style={{ marginTop: 6 }}>{this.state.error.message}</div>
        </Callout>
      </div>
    )
  }
}
