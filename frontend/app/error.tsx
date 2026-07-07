'use client';

import type { JSX } from 'react';

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Root error boundary — catches unhandled errors thrown anywhere in the App
 * Router tree (root route and all nested segments). Must be a client component.
 *
 * Replaces the class-based ErrorBoundary previously in pages/_app.tsx. Enhanced
 * with full-stack display (message + stack + digest) for root-route diagnostics,
 * since this is the main application entry point.
 */
export default function RootError({ error, reset }: ErrorProps): JSX.Element {
  return (
    <div style={{
      padding: 40,
      fontFamily: 'monospace',
      background: '#121418',
      minHeight: '100vh',
      color: '#F87272',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 16,
    }}>
      <span className="material-symbols-outlined" style={{ fontSize: 64, color: '#F87272' }}>
        error_outline
      </span>
      <h1 style={{ fontSize: 24, marginBottom: 16, color: '#E4E7EC' }}>
        应用发生错误
      </h1>
      <div style={{
        background: '#191C22',
        border: '1px solid #F87272',
        padding: 20,
        marginBottom: 16,
        maxWidth: 800,
        width: '100%',
      }}>
        <strong>错误信息：</strong> {error.message || '未知错误'}
        {error.digest && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#9CA3AF' }}>
            错误 ID: {error.digest}
          </div>
        )}
      </div>
      <div style={{ maxWidth: 800, width: '100%' }}>
        <strong>调用栈：</strong>
        <pre style={{
          background: '#191C22',
          color: '#E4E7EC',
          padding: 16,
          borderRadius: 6,
          overflow: 'auto',
          maxHeight: 300,
          fontSize: 12,
          lineHeight: 1.5,
        }}>
          {error.stack}
        </pre>
      </div>
      <button
        onClick={reset}
        style={{
          marginTop: 8,
          padding: '10px 24px',
          borderRadius: 8,
          background: '#3B82F6',
          color: 'white',
          border: 'none',
          cursor: 'pointer',
          fontSize: 14,
        }}
      >
        重试
      </button>
    </div>
  );
}
