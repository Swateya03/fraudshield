import { Component } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center gap-4 p-12 text-center h-full">
          <AlertTriangle size={28} className="text-block-text" />
          <div>
            <div className="text-sm font-medium text-slate-200">This panel crashed</div>
            <div className="text-xs text-slate-500 mt-1 font-mono max-w-sm break-all">
              {this.state.error?.message}
            </div>
          </div>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="flex items-center gap-2 px-4 py-2 text-xs border border-border rounded-lg text-slate-400 hover:text-slate-200 hover:border-muted transition-colors"
          >
            <RefreshCw size={12} />
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
