import { Html, Head, Main, NextScript } from 'next/document';

/**
 * Custom _document — injects a blocking script that reads the stored theme
 * from localStorage and applies `data-theme` to <html> BEFORE the first paint.
 *
 * This eliminates the need for a "hidden div" flash-prevention pattern in
 * _app.tsx, which was causing a client-side hydration crash (empty SSR tree
 * replaced by the full component tree after mount).
 */
export default function Document() {
  return (
    <Html lang="zh-CN">
      <Head />
      <body>
        {/* Blocking script: runs synchronously before any rendering.
            Reads stored theme and applies it to <html> to prevent FOUC. */}
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
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
