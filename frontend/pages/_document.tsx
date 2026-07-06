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
        {/* DEBUG: global error handler to surface the real error on page */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              window._ahErrors = [];
              window.addEventListener('error', function(e) {
                if (e.error) window._ahErrors.push(e.error);
                var el = document.getElementById('_ah_err');
                if (!el) {
                  el = document.createElement('div');
                  el.id = '_ah_err';
                  el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#b91c1c;color:#fff;padding:20px;font:14px/1.5 monospace;max-height:80vh;overflow:auto;white-space:pre-wrap;';
                  document.body.insertBefore(el, document.body.firstChild);
                }
                el.textContent = (e.error ? (e.error.message + '\\n\\n' + e.error.stack) : e.message) + '\\n---\\n' + el.textContent;
              });
              window.addEventListener('unhandledrejection', function(e) {
                if (e.reason) window._ahErrors.push(e.reason);
                var el = document.getElementById('_ah_err');
                if (!el) {
                  el = document.createElement('div');
                  el.id = '_ah_err';
                  el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#b91c1c;color:#fff;padding:20px;font:14px/1.5 monospace;max-height:80vh;overflow:auto;white-space:pre-wrap;';
                  document.body.insertBefore(el, document.body.firstChild);
                }
                el.textContent = 'UNHANDLED REJECTION: ' + (e.reason ? (e.reason.message || String(e.reason)) : '') + '\\n' + (e.reason && e.reason.stack ? e.reason.stack : '') + '\\n---\\n' + el.textContent;
              });
            `,
          }}
        />
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
