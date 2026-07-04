import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ── Mocks ────────────────────────────────────────────────────────────────

// It's cleaner to mock at module level for this page since it's self-contained.
// We manually mock next/navigation for the App Router.
vi.mock('next/navigation', () => ({
  useParams: () => ({ botId: 'test-bot' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => ({ get: vi.fn(), has: vi.fn() }),
  usePathname: () => '/app/test-bot',
}));

// ── Dynamic import of page (must be after mocks) ────────────────────────
import PublishedBotPage from '../../app/app/[botId]/page';

describe('PublishedBotPage', () => {
  beforeEach(() => {
    // Mock fetch with a default successful response
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        botId: 'test-bot',
        theme_color: '#6366f1',
        welcome_message: '你好！我是 Test Bot。有什么可以帮助你的？',
        suggestions: ['帮我分析一个问题', '介绍一下你自己'],
        display_name: 'Test Bot',
        logo_url: '',
      }),
    }) as any;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Render smoke test ──────────────────────────────────────────

  it('renders the header with bot title', async () => {
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      expect(screen.getByText('Test Bot')).toBeInTheDocument();
    });
  });

  it('renders the "Powered by AgentHub" footer text', async () => {
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      expect(screen.getByText('AgentHub')).toBeInTheDocument();
    });
  });

  it('renders the composer textarea', async () => {
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });
  });

  it('renders the send button', async () => {
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      // The send button has a send icon
      const sendBtn = document.querySelector('.material-symbols-outlined');
      expect(sendBtn).toBeInTheDocument();
    });
  });

  it('renders suggestion buttons', async () => {
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      expect(screen.getByText('帮我分析一个问题')).toBeInTheDocument();
      expect(screen.getByText('介绍一下你自己')).toBeInTheDocument();
    });
  });

  it('shows the welcome message', async () => {
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      expect(screen.getByText('你好！我是 Test Bot。有什么可以帮助你的？')).toBeInTheDocument();
    });
  });

  // ── Interaction tests ──────────────────────────────────────────

  it('accepts text input in the composer', async () => {
    const user = userEvent.setup();
    await act(async () => {
      render(<PublishedBotPage />);
    });
    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Hello bot');
    expect(textarea).toHaveValue('Hello bot');
  });

  it('clicking a suggestion fills the input', async () => {
    const user = userEvent.setup();
    await act(async () => {
      render(<PublishedBotPage />);
    });

    // Wait for suggestions to appear
    await waitFor(() => {
      expect(screen.getByText('帮我分析一个问题')).toBeInTheDocument();
    });

    await user.click(screen.getByText('帮我分析一个问题'));
    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveValue('帮我分析一个问题');
  });

  it('clears input after sending', async () => {
    const user = userEvent.setup();
    // Mock the chat send fetch call too
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          botId: 'test-bot',
          theme_color: '#6366f1',
          suggestions: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        body: {
          getReader: () => ({
            read: () => Promise.resolve({ done: true, value: undefined }),
          }),
        },
      }) as any;

    await act(async () => {
      render(<PublishedBotPage />);
    });

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Test message');
    expect(textarea).toHaveValue('Test message');

    // Submit
    const sendBtn = document.querySelector('button span.material-symbols-outlined')?.closest('button');
    await act(async () => {
      await user.click(sendBtn!);
    });

    // Input should be cleared
    await waitFor(() => {
      expect(textarea).toHaveValue('');
    });
  });

  // ── Graceful degradation ───────────────────────────────────────

  it('renders with fallback when fetch fails', async () => {
    // Override fetch to reject
    global.fetch = vi.fn().mockRejectedValue(new Error('Network Error')) as any;

    await act(async () => {
      render(<PublishedBotPage />);
    });

    // Should still render with defaults (no crash)
    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });
    // The header should show the botId (from useParams)
    expect(screen.getByText('test-bot')).toBeInTheDocument();
  });

  it('renders fallback avatar when no logo_url', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ botId: 'test-bot', suggestions: [] }),
    }) as any;

    await act(async () => {
      render(<PublishedBotPage />);
    });

    await waitFor(() => {
      // Fallback: first letter of botId in a colored circle
      // first char of 'test-bot' → .toUpperCase() → 'T'
      expect(screen.getByText('T')).toBeInTheDocument();
    });
  });
});
