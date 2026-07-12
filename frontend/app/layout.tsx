import type { Metadata, Viewport } from 'next';
import type { JSX, ReactNode } from 'react';
import '../styles/globals.css';
import { PageTransitionProvider } from '../lib/animations/pageTransitionProvider';

export const metadata: Metadata = {
  title: 'AgentHub — Collaborative AI Development Platform',
  description: 'AgentHub is a collaborative AI development platform powered by multi-agent orchestration.',
  icons: {
    icon: '/logo.png',
    shortcut: '/logo.png',
    apple: '/logo.png',
  },
};

// Next.js 14: viewport must be a separate export (not inside metadata).
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }): JSX.Element {
  return (
    <html lang="zh-CN">
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20,400,0,0&display=block"
        />
        {/* Theme FOUC prevention: read stored theme from localStorage and apply
            data-theme to <html> BEFORE first paint. Must run synchronously
            (inlined, not via useEffect) to prevent flash of unstyled content.
            Migrated from pages/_document.tsx during App Router migration. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var t = localStorage.getItem('agenthub_theme');
                  if (!t) t = localStorage.getItem('agenthub_theme_legacy');
                  if (t === 'dark' || t === 'light' || t === 'warm') {
                    document.documentElement.setAttribute('data-theme', t);
                    document.documentElement.style.colorScheme = t === 'dark' ? 'dark' : 'light';
                  }
                } catch(e) {}
              })();
            `,
          }}
        />
        {/* Reduced motion: respect OS-level accessibility preference */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              try {
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                  document.documentElement.classList.add('reduce-motion');
                }
                window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', function(e) {
                  document.documentElement.classList.toggle('reduce-motion', e.matches);
                });
              } catch(e) {}
            `,
          }}
        />
      </head>
      <body className="bg-[#121418] text-[#E4E7EC] antialiased">
        {/* Skip-to-content link for keyboard navigation (WCAG 2.4.1) */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-toast focus:px-4 focus:py-2 focus:bg-primary-500 focus:text-white focus:rounded-lg focus:outline-none focus:shadow-lg"
        >
          跳转到内容
        </a>

        <PageTransitionProvider>
          <div id="main-content" tabIndex={-1}>
            {children}
          </div>
        </PageTransitionProvider>
      </body>
    </html>
  );
}
