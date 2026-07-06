import React from 'react';
import type { AppProps } from 'next/app';
import '../styles/globals.css';

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
}

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    this.setState({ errorInfo });
    console.error('[ErrorBoundary] caught:', error, errorInfo);
  }

  render(): React.ReactNode {
    if (this.state.hasError && this.state.error) {
      return (
        <div style={{
          padding: 40,
          fontFamily: 'monospace',
          background: '#1C1A18',
          minHeight: '100vh',
          color: '#C4675A',
        }}>
          <h1 style={{ fontSize: 24, marginBottom: 16, color: '#EBE8E3' }}>Client Error Caught</h1>
          <div style={{
            background: '#24221F',
            border: '1px solid #C4675A',
            padding: 20,
            marginBottom: 16,
          }}>
            <strong>Message:</strong> {this.state.error.message}
          </div>
          <div style={{ marginBottom: 16 }}>
            <strong>Stack:</strong>
            <pre style={{
              background: '#1a1a2e',
              color: '#eee',
              padding: 16,
              borderRadius: 8,
              overflow: 'auto',
              maxHeight: 400,
              fontSize: 12,
              lineHeight: 1.5,
            }}>
              {this.state.error.stack}
            </pre>
          </div>
          {this.state.errorInfo && (
            <div>
              <strong>Component Stack:</strong>
              <pre style={{
                background: '#1a1a2e',
                color: '#eee',
                padding: 16,
                borderRadius: 8,
                overflow: 'auto',
                maxHeight: 300,
                fontSize: 12,
                lineHeight: 1.5,
              }}>
                {this.state.errorInfo.componentStack}
              </pre>
            </div>
          )}
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App({ Component, pageProps }: AppProps): React.ReactElement {
  return (
    <ErrorBoundary>
      <Component {...pageProps} />
    </ErrorBoundary>
  );
}
