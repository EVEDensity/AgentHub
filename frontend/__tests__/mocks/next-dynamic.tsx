/**
 * Mock for next/dynamic — replaces all dynamic imports with a simple
 * pass-through div that renders children (for smoke tests).
 */
import { type JSX } from 'react';

export default function dynamicMock(
  _importFn: () => Promise<any>,
  _opts?: { ssr?: boolean; loading?: () => JSX.Element | null }
): React.ComponentType<any> {
  // Return a simple placeholder component
  const Placeholder = (props: any) => {
    const Loading = _opts?.loading;
    if (Loading) {
      return <div data-testid="dynamic-loading"><Loading /></div>;
    }
    return null;
  };
  Placeholder.displayName = 'DynamicMock';
  return Placeholder;
}
