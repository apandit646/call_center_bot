import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Changing this (e.g. the current page path) clears the error, so moving to another page recovers. */
  resetKey?: string;
}

interface State {
  error: Error | null;
  resetKey?: string;
}

/**
 * Without this, any error while drawing a page leaves the whole screen blank. With it, the page
 * area shows what went wrong and a way to carry on, and the menu keeps working.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page crashed:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="card" role="alert">
        <h2>This page could not be shown</h2>
        <p className="muted">Something went wrong while drawing it. Your data is safe. Reload the page, or open another page from the menu.</p>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12.5 }}>{error.name}: {error.message}</pre>
        <div className="row">
          <button className="btn" onClick={() => window.location.reload()}>Reload</button>
          <button className="btn ghost" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      </div>
    );
  }
}
