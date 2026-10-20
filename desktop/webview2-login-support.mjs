import { childEnvironment, identityAccepted } from './webview2-native-support.mjs';

const LOOPBACK = /^http:\/\/127\.0\.0\.1:(\d+)$/;

export function debugEndpoint(port) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error('WebView2 debugging port must be an integer from 1024 to 65535.');
  }
  return `http://127.0.0.1:${port}`;
}

export function loginChildEnvironment(base, port) {
  const endpoint = new URL(debugEndpoint(port));
  const env = childEnvironment(base);
  env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-port=${endpoint.port} --remote-debugging-address=127.0.0.1`;
  return env;
}

export function classifyProductLogin({ requests, tokenWrites = [] }) {
  const logins = requests.filter(request => request.method === 'POST' && request.path === '/api/auth/login');
  if (logins.length !== 1 || logins[0].initiator !== 'page' || logins[0].status !== 200) {
    return { verified: false, reason: 'Product login requires one page-initiated HTTP 200 POST /api/auth/login.' };
  }
  const injected = tokenWrites.some(write => write.source !== 'page' || write.key !== 'agenthub_token' || write.beforeLogin === true);
  if (injected) return { verified: false, reason: 'A test-supplied or pre-login token is not product authentication.' };
  return { verified: true, reason: 'Page completed POST /api/auth/login without a pre-seeded token.' };
}

export async function submitProductLogin(page, credentials) {
  if (!credentials?.name || !credentials?.password) throw new Error('Product login requires the account name and password.');
  const existing = await page.evaluate(() => localStorage.getItem('agenthub_token'));
  if (existing) throw new Error('Refusing to log in over a pre-seeded admin token.');
  const pending = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/auth/login');
  await page.getByLabel('Username').fill(credentials.name);
  await page.getByLabel('Password').fill(credentials.password);
  await page.getByRole('button', { name: 'Login', exact: true }).click();
  const response = await pending;
  return classifyProductLogin({
    requests: [{ method: 'POST', path: '/api/auth/login', status: response.status(), initiator: 'page' }],
  });
}

export function productLoginReport(identity, observation) {
  if (identity?.status !== 'passed' || !identityAccepted(identity)) throw new Error('Product login requires a passed WebView2 process-identity report.');
  if (identity.executableSha256 !== observation.executableSha256) throw new Error('Login observation does not match the identified executable.');
  const endpoint = observation.debugEndpoint;
  if (typeof endpoint !== 'string' || !LOOPBACK.test(endpoint)) throw new Error('Login debugging endpoint must be loopback HTTP.');
  const classification = classifyProductLogin(observation);
  if (!classification.verified) throw new Error(classification.reason);
  return {
    schemaVersion: 1, scope: 'desktop-webview2-product-login', status: 'passed',
    executableSha256: identity.executableSha256, debugEndpoint: endpoint,
    nativeWebView2: true, packagedApplication: true, realLoginVerified: true,
    sharedUiVerified: false, realServicesVerified: false, chineseInputVerified: false,
    zoomVerified: false, shutdownVerified: false, checks: [classification.reason],
  };
}
