import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { WorkflowConflictDialog } from '../../../components/flow/WorkflowConflictDialog';
import { normalizeWorkflowDocument } from '../../../lib/workflowContract';

describe('WorkflowConflictDialog', () => {
  it('shows graph comparison and exposes reload and overwrite actions', () => {
    const onReload = vi.fn();
    const onOverwrite = vi.fn();
    render(
      <WorkflowConflictDialog
        conflict={{
          kind: 'workflow',
          local: normalizeWorkflowDocument({ name: 'Local', version: 2, nodes: [], edges: [] }),
          remote: normalizeWorkflowDocument({ name: 'Remote', version: 3, nodes: [], edges: [] }),
          expectedVersion: 2,
          currentVersion: 3,
        }}
        onClose={vi.fn()}
        onReload={onReload}
        onOverwrite={onOverwrite}
      />,
    );

    expect(screen.getByText(/当前编辑基于版本/)).toHaveTextContent('2');
    expect(screen.getByText(/名称：本地/)).toHaveTextContent('Local');
    fireEvent.click(screen.getByRole('button', { name: /载入服务器版本/ }));
    fireEvent.click(screen.getByRole('button', { name: /以本地版本覆盖/ }));
    expect(onReload).toHaveBeenCalledOnce();
    expect(onOverwrite).toHaveBeenCalledOnce();
  });
});
