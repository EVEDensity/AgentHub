import type { Metadata } from 'next';
import type { JSX, ReactNode } from 'react';
import '../styles/globals.css';

export const metadata: Metadata = {
  title: 'AgentHub — Collaborative AI Development Platform',
  description: 'AgentHub is a collaborative AI development platform powered by multi-agent orchestration.',
  viewport: 'width=device-width, initial-scale=1, maximum-scale=1',
};

export default function RootLayout({ children }: { children: ReactNode }): JSX.Element {
  return (
    <html lang="zh-CN">
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20,400,0,0&display=block"
        />
      </head>
      <body className="bg-warm-50 text-warm-800 antialiased">
        {children}
      </body>
    </html>
  );
}
