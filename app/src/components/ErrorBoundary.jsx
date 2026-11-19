import React from 'react'

// Un guasto in una pagina non deve spegnere tutta la console: il resto (menu, altre pagine) resta usabile.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Errore di pagina:', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="card" style={{ maxWidth: 560, margin: '48px auto', padding: 24, textAlign: 'center' }}>
        <h2 style={{ marginTop: 0 }}>Questa pagina ha avuto un problema</h2>
        <p style={{ color: 'var(--text-3)' }}>
          Il resto della console funziona: puoi usare il menu in alto per aprire un'altra pagina.
        </p>
        <p style={{ fontSize: 12, color: 'var(--text-3)', wordBreak: 'break-word' }}>{String(this.state.error?.message || this.state.error).slice(0, 240)}</p>
        <button className="btn btn-primary btn-sm" onClick={() => this.setState({ error: null })}>Riprova</button>
      </div>
    )
  }
}
