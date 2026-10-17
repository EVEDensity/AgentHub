import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AdminLayout from '../../../app/admin/layout';

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation }));
vi.mock('../../../components/admin/WorkspaceSelector', () => ({ WorkspaceSelector: () => null }));
vi.mock('../../../components/admin/AdminSidebar', () => ({ default: ({ onMenuClick }: { onMenuClick: (value: string) => void }) => <button onClick={() => onMenuClick('Mission Control')}>Test actual navigation</button> }));
vi.mock('../../../components/admin/CommandPalette', () => ({ default: () => null }));
vi.mock('../../../components/chat/AIChatDialog', () => ({ default: () => null }));
vi.mock('../../../components/common/ResizableDivider', () => ({ default: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); navigation.push.mockClear(); });

it('records only actual navigation, bounds it at 200 and clears without synthetic system output', () => {
  vi.stubGlobal('innerWidth', 1600);
  render(<AdminLayout><p>Actual page</p></AdminLayout>);
  const panel = screen.getByRole('complementary', { name: '前端活动' });
  expect(within(panel).getByRole('log')).toHaveTextContent('暂无前端活动');
  const navigate = screen.getByRole('button', { name: 'Test actual navigation' });
  for (let index = 0; index < 201; index++) fireEvent.click(navigate);
  expect(navigation.push).toHaveBeenCalledTimes(201);
  expect(navigation.push).toHaveBeenLastCalledWith('/admin?menu=Mission%20Control');
  expect(within(panel).getAllByText('NAV', { exact: true })).toHaveLength(200);
  expect(within(panel).getAllByText('导航至: Mission Control', { exact: true })).toHaveLength(200);
  expect(within(panel).queryByRole('textbox')).toBeNull();
  fireEvent.click(within(panel).getByTitle('清空前端活动'));
  expect(within(panel).getByRole('log')).toHaveTextContent('暂无前端活动');
  expect(within(panel).queryByText(/SYS|v5\.1\.0|控制台已就绪/)).toBeNull();
});
