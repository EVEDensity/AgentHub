import * as https from "https";
import * as http from "http";
import { URL } from "url";
import { EventEmitter } from "events";

export type CreateMissionResult =
  | { status: 'pending'; pendingId: string; ruleDescription?: string }
  | { status: 'accepted'; missionId: string; sessionId?: string; streamUrl: string };

export interface SessionEvent {
  id: string;
  type: string;
  timestamp: string;
  actor?: { type: string; id: string; displayName?: string };
  payload?: Record<string, unknown>;
}

/** HTTP adapter for the versioned chat admission and Mission command contracts. */
export class AgentHubClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string = "", private workspaceId: string = "local-admin", private requestTimeoutMs = 30_000) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  // ── Health ─────────────────────────────────────────────────────────

  async health(): Promise<{ status: string; version?: string }> {
    return this._get("/api/health");
  }

  // ── Agents ────────────────────────────────────────────────────────

  async listAgents(): Promise<Array<{ id: string; name: string; adapter: string }>> {
    const data = await this._get<{ registry: Array<{ id: string; name: string; adapter: string }> }>(
      "/api/v1/agent/registry"
    );
    return data.registry ?? [];
  }

  // ── Chat Mission ──────────────────────────────────────────────────

  async createMission(message: string): Promise<CreateMissionResult> {
    const data = await this._post<Record<string, unknown>>(
      "/api/v1/chat/mission",
      JSON.stringify({ message, workspaceId: this.workspaceId, stream: true }), "application/json"
    );
    return this._admission(data);
  }

  async confirmMission(pendingId: string): Promise<CreateMissionResult> {
    const data = await this._post<Record<string, unknown>>(
      "/api/v1/chat/confirm", JSON.stringify({ pendingId }), "application/json"
    );
    return this._admission(data);
  }

  async cancelPending(pendingId: string): Promise<unknown> {
    return this._post("/api/v1/chat/cancel", JSON.stringify({ pendingId }), "application/json");
  }

  async cancelMission(missionId: string): Promise<unknown> {
    return this._post(`/api/v1/missions/${encodeURIComponent(missionId)}/cancel`, "{}", "application/json");
  }

  private _admission(data: Record<string, unknown>): CreateMissionResult {
    if (data.status === 'pending' && typeof data.pendingId === 'string') {
      return { status: 'pending', pendingId: data.pendingId, ruleDescription: typeof data.ruleDescription === 'string' ? data.ruleDescription : undefined };
    }
    if (typeof data.missionId !== 'string' || typeof data.streamUrl !== 'string') {
      throw new Error('Invalid Mission admission response');
    }
    return { status: 'accepted', missionId: data.missionId, streamUrl: data.streamUrl, sessionId: typeof data.sessionId === 'string' ? data.sessionId : undefined };
  }

  // ── Git ────────────────────────────────────────────────────────────

  async gitStatus(): Promise<{ status: string; branch?: string }> {
    return this._get("/api/v1/git/status");
  }

  async gitDiff(): Promise<{ diff: string; exitCode: number }> {
    return this._get("/api/v1/git/diff");
  }

  // ── SSE ─────────────────────────────────────────────────────────────

  /** Subscribe to Mission event stream.  Returns an EventEmitter that
   *  emits 'event' (parsed SessionEvent) and 'error' (stream errors).
   *  Call .close() to tear down the connection. */
  subscribeMissionEvents(missionId: string): SSESubscription {
    const url = new URL(`${this.baseUrl}/api/v1/missions/${missionId}/events/stream`);
    return this._openSSE(url.toString());
  }

  subscribeSessionEvents(sessionId: string): SSESubscription {
    const url = new URL(`${this.baseUrl}/api/v1/sessions/${sessionId}/events/stream`);
    return this._openSSE(url.toString());
  }

  // ── Internals ──────────────────────────────────────────────────────

  private async _get<T>(path: string): Promise<T> {
    return this._fetch("GET", path);
  }

  private async _post<T>(path: string, body: string, contentType: string): Promise<T> {
    return this._fetch("POST", path, body, contentType);
  }

  private async _fetch<T>(
    method: string,
    path: string,
    body?: string,
    contentType?: string
  ): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`);
    return new Promise<T>((resolve, reject) => {
      let settled = false;
      const complete = (error?: Error, value?: T) => {
        if (settled) return;
        settled = true;
        clearTimeout(deadline);
        if (error) reject(error);
        else resolve(value as T);
      };
      const lib = url.protocol === "https:" ? https : http;
      const headers: Record<string, string> = {
        Accept: "application/json",
      };
      if (this.apiKey) headers["Authorization"] = `Bearer ${this.apiKey}`;
      if (body) headers["Content-Type"] = contentType ?? "application/json";

      const req = lib.request(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname + url.search,
          method,
          headers,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => {
            if (!res.complete) {
              complete(new Error('AgentHub HTTP response was interrupted'));
              return;
            }
            const raw = Buffer.concat(chunks).toString("utf-8");
            if (!res.statusCode || res.statusCode < 200 || res.statusCode >= 300) {
              complete(new Error(`${res.statusCode} ${res.statusMessage}: ${raw.slice(0, 200)}`));
              return;
            }
            if (!raw) {
              complete(undefined, {} as T);
              return;
            }
            try {
              complete(undefined, JSON.parse(raw) as T);
            } catch {
              complete(new Error('AgentHub returned an invalid JSON response'));
            }
          });
          res.on('aborted', () => complete(new Error('AgentHub HTTP response was interrupted')));
          res.on('error', error => complete(error));
        }
      );
      const deadline = setTimeout(() => {
        const error = new Error('AgentHub HTTP request timed out');
        complete(error);
        req.destroy(error);
      }, this.requestTimeoutMs);
      req.on("error", error => complete(error));
      if (body) req.write(body);
      req.end();
    });
  }

  private _openSSE(url: string): SSESubscription {
    const sub = new SSESubscription(url, this.apiKey);
    sub.connect();
    return sub;
  }
}

/** Server-Sent Events client over raw http/https.
 *  We parse the ``data: {...}`` lines and emit parsed JSON events. */
export class SSESubscription extends EventEmitter {
  private url: string;
  private apiKey: string;
  private req?: http.ClientRequest;
  private buffer = "";
  private closed = false;
  private reconnectTimer?: NodeJS.Timeout;

  constructor(url: string, apiKey: string) {
    super();
    this.url = url;
    this.apiKey = apiKey;
    this.setMaxListeners(20);
  }

  connect() {
    if (this.closed) return;
    let disconnected = false;
    const disconnect = (error: Error, reconnect = true) => {
      if (this.closed || disconnected) return;
      disconnected = true;
      // A partial frame belongs to the lost connection and cannot be joined
      // to the next response. Transport EOF never means Mission completion.
      this.buffer = '';
      this.emit('error', error);
      if (reconnect && !this.closed) this._scheduleReconnect();
    };
    const urlObj = new URL(this.url);
    const lib = urlObj.protocol === "https:" ? https : http;
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
      "Cache-Control": "no-cache",
    };
    if (this.apiKey) headers["Authorization"] = `Bearer ${this.apiKey}`;

    this.req = lib.request(
      {
        hostname: urlObj.hostname,
        port: urlObj.port || (urlObj.protocol === "https:" ? 443 : 80),
        path: urlObj.pathname + urlObj.search,
        method: "GET",
        headers,
      },
      (res) => {
        // Even rejected responses can be interrupted while their body drains.
        // Attach terminal handlers before either validation branch can return.
        res.on('aborted', () => disconnect(new Error('Mission event connection was interrupted; reconnecting')));
        res.on('error', error => disconnect(error));
        res.on('end', () => disconnect(new Error('Mission event connection closed; reconnecting')));
        if (res.statusCode !== 200 || !res.headers['content-type']?.includes('text/event-stream')) {
          res.resume();
          disconnect(new Error(`Mission stream unavailable (HTTP ${res.statusCode})`), false);
          return;
        }
        res.setEncoding("utf-8");
        res.on("data", (chunk) => {
          this.buffer += chunk;
          this._drain();
        });
      }
    );
    this.req.on("error", error => disconnect(error));
    this.req.end();
  }

  private _drain() {
    this.buffer = this.buffer.replace(/\r\n/g, "\n");
    // SSE frames are separated by blank lines (double \n)
    let idx: number;
    while ((idx = this.buffer.indexOf("\n\n")) !== -1) {
      const frame = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      this._parseFrame(frame);
    }
  }

  private _parseFrame(frame: string) {
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0) return;
    const payload = dataLines.join("\n");
    try {
      const evt = JSON.parse(payload);
      this.emit("event", evt);
    } catch {
      this.emit("raw", payload);
    }
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      if (!this.closed) this.connect();
    }, 2000);
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.req) {
      try {
        this.req.destroy();
      } catch {
        // ignore
      }
      this.req = undefined;
    }
    this.removeAllListeners();
  }
}
