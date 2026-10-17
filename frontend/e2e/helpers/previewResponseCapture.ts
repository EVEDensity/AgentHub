import type { Page } from '@playwright/test';

const MAX_CAPTURE_BYTES = 2_097_152;
type CaptureResult = { url: string; status: number; bytes: number[]; error?: string };
type CaptureEntry = { generation: number; path: string; result: Promise<CaptureResult> };
type CaptureWindow = Window & { __previewResponseCapture?: { generation: number; entries: CaptureEntry[] } };

/** Test-only observation of the same chunks the application reads, without a tee or another GET. */
export async function installPreviewResponseCapture(page: Page) {
  await page.addInitScript(({ limit }) => {
    if (window.top !== window) return;
    const captures = { generation: 0, entries: [] as CaptureEntry[] };
    (window as CaptureWindow).__previewResponseCapture = captures;
    const nativeFetch = window.fetch;
    window.fetch = (...args: Parameters<typeof fetch>) => {
      const pending = Reflect.apply(nativeFetch, window, args) as Promise<Response>;
      const input = args[0];
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
      const method = args[1]?.method ?? (input instanceof Request ? input.method : 'GET');
      if (method.toUpperCase() !== 'GET' || url.origin !== window.location.origin
        || !/^\/api\/v1\/missions\/[^/]+\/artifacts\/[^/]+\/preview$/.test(url.pathname)) return pending;
      let finish!: (result: CaptureResult) => void;
      let settled = false;
      let responseUrl = url.href;
      let status = 0;
      const result = new Promise<CaptureResult>(resolve => { finish = resolve; });
      const complete = (value: { bytes: number[]; error?: string }) => {
        if (!settled) { settled = true; finish({ url: responseUrl, status, ...value }); }
      };
      const fail = (error: unknown) => complete({ bytes: [], error: String(error) });
      captures.entries.push({ generation: ++captures.generation, path: url.pathname, result });
      if (captures.entries.length > 8) captures.entries.shift();
      // Register before the caller awaits fetch, and return its original promise.
      void pending.then(response => {
        responseUrl = response.url; status = response.status;
        try {
          const body = response.body;
          if (!body) { fail('Preview response has no body.'); return; }
          const getReader = body.getReader.bind(body);
          body.getReader = ((...readerArgs: []) => {
            const reader = getReader(...readerArgs);
            const nativeRead = reader.read;
            const nativeCancel = reader.cancel;
            const chunks: Uint8Array[] = [];
            let length = 0;
            reader.read = (...readArgs: []) => {
              const read = Reflect.apply(nativeRead, reader, readArgs) as ReturnType<typeof nativeRead>;
              void read.then(chunk => {
                if (settled) return;
                if (chunk.done) {
                  const bytes = new Uint8Array(length);
                  let offset = 0;
                  for (const value of chunks) { bytes.set(value, offset); offset += value.byteLength; }
                  complete({ bytes: Array.from(bytes) });
                } else if ((length += chunk.value.byteLength) > limit) fail('Preview capture exceeds 2 MiB.');
                else chunks.push(chunk.value.slice());
              }, fail);
              return read;
            };
            reader.cancel = (...cancelArgs: Parameters<typeof nativeCancel>) => {
              fail('Application cancelled preview body before EOF.');
              return Reflect.apply(nativeCancel, reader, cancelArgs);
            };
            return reader;
          }) as typeof body.getReader;
        } catch (error) { fail(error); }
      }, fail);
      return pending;
    };
  }, { limit: MAX_CAPTURE_BYTES });
}

export async function previewCaptureGeneration(page: Page) {
  return page.evaluate(() => {
    const captures = (window as CaptureWindow).__previewResponseCapture;
    if (!captures) throw new Error('Preview response observer was not installed.');
    return captures.generation;
  });
}

export async function capturedPreviewResponse(page: Page, path: string, generation: number) {
  await page.waitForFunction(({ path, generation }) => (window as CaptureWindow).__previewResponseCapture?.entries
    .some(entry => entry.path === path && entry.generation > generation), { path, generation });
  const result = await page.evaluate(async ({ path, generation }) => {
    const matches = (window as CaptureWindow).__previewResponseCapture!.entries
      .filter(entry => entry.path === path && entry.generation > generation);
    if (matches.length !== 1) throw new Error('Expected exactly one application preview read after the explicit click.');
    return matches[0].result;
  }, { path, generation });
  if (result.error) throw new Error(`Application preview body capture failed: ${result.error}`);
  if (result.bytes.length > MAX_CAPTURE_BYTES) throw new Error('Preview capture exceeds 2 MiB.');
  return result;
}
