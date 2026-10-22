import { Component, type ErrorInfo, type ReactNode } from "react";
import { CircleAlert, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Full-page crash UI (root) vs inline panel recovery. */
  variant?: "page" | "panel";
  title?: string;
}

interface State {
  error: Error | null;
}

/** Catches render errors so a single component bug does not blank the whole app. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("UI error:", error, info.componentStack);
  }

  private reload = (): void => {
    window.location.reload();
  };

  private retry = (): void => {
    this.setState({ error: null });
  };

  render() {
    if (!this.state.error) return this.props.children;

    const variant = this.props.variant ?? "page";
    if (variant === "panel") {
      return (
        <div className="panel-error" role="alert">
          <CircleAlert size={16} />
          <div className="panel-error-copy">
            <strong>{this.props.title ?? "This section failed to render"}</strong>
            <p>{this.state.error.message}</p>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={this.retry}>
            <RefreshCw size={12} />
            Try again
          </button>
        </div>
      );
    }

    return (
      <div className="fatal-error" role="alert">
        <CircleAlert size={22} />
        <h1>Something went wrong</h1>
        <p>The interface hit an unexpected error. You can reload and continue your session.</p>
        <pre>{this.state.error.message}</pre>
        <button type="button" className="btn btn-primary" onClick={this.reload}>
          <RefreshCw size={14} />
          Reload page
        </button>
      </div>
    );
  }
}
