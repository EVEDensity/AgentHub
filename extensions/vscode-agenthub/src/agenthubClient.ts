import * as https from "https";
import * as http from "http";
import { URL } from "url";
import { EventEmitter } from "events";

export interface MissionSummary {
  id: string;
  objective: string;
  status: string;
  agentId?: string;
  createdAt?: string;
}

export interface CreateMissionResult {
  missionId: string;
  assignedAgentId: string;
  sessionId: string;
}

export interface SessionEvent {
  id: string;
  type: string;
  timestamp: string;
  actor?: { type: string; id: string; displayName?: string };
  payload?: Record<string, unknown>;
}

export interface MissionCreatedResponse {
  missionId: string;
  sessionId: string;
  assignedAgentId: string;
  streamUrl: string;
}

/**
 * Minimal AgentHub backend client.
 *
 * Covers:
 * - GET  /system/health                     (server liveness)
 * - GET  /v1/agent/registry                 (agent list for @mention)
 * - POST /v1/chat_mission/mission           (create chat mission)
 * - POST /v1/chat_mission/confirm           (rule confirm)
 * - GET  /v1/sessions/{id}/events/stream    (SSE — real-time chat)
 * - GET  /v1/missions/{id}/events/stream    (SSE — mission timeline)
 * - GET  /v1/git/status                     (repo status)
 * - GET  /v1/git/diff                       (diff)
 */
export class AgentHubClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string = "") {
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

  async createMission(objective: string): Promise<CreateMissionResult> {
    // The backend accepts either { message: "... }" or { objective: "...", message: "..." }.
    // We send both to be forgiving.
    const body = JSON.stringify({ message: objective, objective });
    const data = await this._post<MissionCreatedResponse>(
      "/api/v1/chat/mission",
      body,
      "application/json"
    );
    // Note: the initial response does NOT include sessionId — the extension
    // learns it from the first SSE event.  This is intentional (smaller
    // create-mission payload, full context on the stream).
    return {
      missionId: data.missionId,
      assignedAgentId: "",  // not in response; will come via SSE
      sessionId: "",         // not in response; will come via SSE
    };
  }

  async confirmMission(missionId: string, confirmId: string): Promise<{ missionId: string }> {
    return this._post<{ missionId: string }>(
      "/api/v1/chat/confirm",
      JSON.stringify({ missionId, confirmId }),
      "application/json"
    );
  }

  async cancelMission(missionId: string): Promise<{ missionId: string }> {
    return this._post<{ missionId: string }>(
      "/api/v1/chat/cancel",
      JSON.stringify({ missionId }),
      "application/json"
    );
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
            const raw = Buffer.concat(chunks).toString("utf-8");
            if (res.statusCode && res.statusCode >= 400) {
              reject(new Error(`${res.statusCode} ${res.statusMessage}: ${raw.slice(0, 200)}`));
              return;
            }
            if (!raw) {
              resolve({} as T);
              return;
            }
            try {
              resolve(JSON.parse(raw) as T);
            } catch {
              resolve(raw as unknown as T);
            }
          });
        }
      );
      req.on("error", reject);
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
        res.setEncoding("utf-8");
        res.on("data", (chunk) => {
          this.buffer += chunk;
          this._drain();
        });
        res.on("end", () => {
          if (!this.closed) this._scheduleReconnect();
        });
      }
    );
    this.req.on("error", (err) => {
      this.emit("error", err);
      if (!this.closed) this._scheduleReconnect();
    });
    this.req.end();
  }

  private _drain() {
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
