"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.SSESubscription = exports.AgentHubClient = void 0;
const https = __importStar(require("https"));
const http = __importStar(require("http"));
const url_1 = require("url");
const events_1 = require("events");
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
class AgentHubClient {
    constructor(baseUrl, apiKey = "") {
        this.baseUrl = baseUrl.replace(/\/$/, "");
        this.apiKey = apiKey;
    }
    // ── Health ─────────────────────────────────────────────────────────
    async health() {
        return this._get("/api/health");
    }
    // ── Agents ────────────────────────────────────────────────────────
    async listAgents() {
        const data = await this._get("/api/v1/agent/registry");
        return data.registry ?? [];
    }
    // ── Chat Mission ──────────────────────────────────────────────────
    async createMission(objective) {
        // The backend accepts either { message: "... }" or { objective: "...", message: "..." }.
        // We send both to be forgiving.
        const body = JSON.stringify({ message: objective, objective });
        const data = await this._post("/api/v1/chat/mission", body, "application/json");
        // Note: the initial response does NOT include sessionId — the extension
        // learns it from the first SSE event.  This is intentional (smaller
        // create-mission payload, full context on the stream).
        return {
            missionId: data.missionId,
            assignedAgentId: "", // not in response; will come via SSE
            sessionId: "", // not in response; will come via SSE
        };
    }
    async confirmMission(missionId, confirmId) {
        return this._post("/api/v1/chat/confirm", JSON.stringify({ missionId, confirmId }), "application/json");
    }
    async cancelMission(missionId) {
        return this._post("/api/v1/chat/cancel", JSON.stringify({ missionId }), "application/json");
    }
    // ── Git ────────────────────────────────────────────────────────────
    async gitStatus() {
        return this._get("/api/v1/git/status");
    }
    async gitDiff() {
        return this._get("/api/v1/git/diff");
    }
    // ── SSE ─────────────────────────────────────────────────────────────
    /** Subscribe to Mission event stream.  Returns an EventEmitter that
     *  emits 'event' (parsed SessionEvent) and 'error' (stream errors).
     *  Call .close() to tear down the connection. */
    subscribeMissionEvents(missionId) {
        const url = new url_1.URL(`${this.baseUrl}/api/v1/missions/${missionId}/events/stream`);
        return this._openSSE(url.toString());
    }
    subscribeSessionEvents(sessionId) {
        const url = new url_1.URL(`${this.baseUrl}/api/v1/sessions/${sessionId}/events/stream`);
        return this._openSSE(url.toString());
    }
    // ── Internals ──────────────────────────────────────────────────────
    async _get(path) {
        return this._fetch("GET", path);
    }
    async _post(path, body, contentType) {
        return this._fetch("POST", path, body, contentType);
    }
    async _fetch(method, path, body, contentType) {
        const url = new url_1.URL(`${this.baseUrl}${path}`);
        return new Promise((resolve, reject) => {
            const lib = url.protocol === "https:" ? https : http;
            const headers = {
                Accept: "application/json",
            };
            if (this.apiKey)
                headers["Authorization"] = `Bearer ${this.apiKey}`;
            if (body)
                headers["Content-Type"] = contentType ?? "application/json";
            const req = lib.request({
                hostname: url.hostname,
                port: url.port || (url.protocol === "https:" ? 443 : 80),
                path: url.pathname + url.search,
                method,
                headers,
            }, (res) => {
                const chunks = [];
                res.on("data", (c) => chunks.push(c));
                res.on("end", () => {
                    const raw = Buffer.concat(chunks).toString("utf-8");
                    if (res.statusCode && res.statusCode >= 400) {
                        reject(new Error(`${res.statusCode} ${res.statusMessage}: ${raw.slice(0, 200)}`));
                        return;
                    }
                    if (!raw) {
                        resolve({});
                        return;
                    }
                    try {
                        resolve(JSON.parse(raw));
                    }
                    catch {
                        resolve(raw);
                    }
                });
            });
            req.on("error", reject);
            if (body)
                req.write(body);
            req.end();
        });
    }
    _openSSE(url) {
        const sub = new SSESubscription(url, this.apiKey);
        sub.connect();
        return sub;
    }
}
exports.AgentHubClient = AgentHubClient;
/** Server-Sent Events client over raw http/https.
 *  We parse the ``data: {...}`` lines and emit parsed JSON events. */
class SSESubscription extends events_1.EventEmitter {
    constructor(url, apiKey) {
        super();
        this.buffer = "";
        this.closed = false;
        this.url = url;
        this.apiKey = apiKey;
        this.setMaxListeners(20);
    }
    connect() {
        const urlObj = new url_1.URL(this.url);
        const lib = urlObj.protocol === "https:" ? https : http;
        const headers = {
            Accept: "text/event-stream",
            "Cache-Control": "no-cache",
        };
        if (this.apiKey)
            headers["Authorization"] = `Bearer ${this.apiKey}`;
        this.req = lib.request({
            hostname: urlObj.hostname,
            port: urlObj.port || (urlObj.protocol === "https:" ? 443 : 80),
            path: urlObj.pathname + urlObj.search,
            method: "GET",
            headers,
        }, (res) => {
            res.setEncoding("utf-8");
            res.on("data", (chunk) => {
                this.buffer += chunk;
                this._drain();
            });
            res.on("end", () => {
                if (!this.closed)
                    this._scheduleReconnect();
            });
        });
        this.req.on("error", (err) => {
            this.emit("error", err);
            if (!this.closed)
                this._scheduleReconnect();
        });
        this.req.end();
    }
    _drain() {
        // SSE frames are separated by blank lines (double \n)
        let idx;
        while ((idx = this.buffer.indexOf("\n\n")) !== -1) {
            const frame = this.buffer.slice(0, idx);
            this.buffer = this.buffer.slice(idx + 2);
            this._parseFrame(frame);
        }
    }
    _parseFrame(frame) {
        const dataLines = [];
        for (const line of frame.split("\n")) {
            if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).trimStart());
            }
        }
        if (dataLines.length === 0)
            return;
        const payload = dataLines.join("\n");
        try {
            const evt = JSON.parse(payload);
            this.emit("event", evt);
        }
        catch {
            this.emit("raw", payload);
        }
    }
    _scheduleReconnect() {
        if (this.reconnectTimer)
            return;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = undefined;
            if (!this.closed)
                this.connect();
        }, 2000);
    }
    close() {
        this.closed = true;
        if (this.reconnectTimer)
            clearTimeout(this.reconnectTimer);
        if (this.req) {
            try {
                this.req.destroy();
            }
            catch {
                // ignore
            }
            this.req = undefined;
        }
        this.removeAllListeners();
    }
}
exports.SSESubscription = SSESubscription;
//# sourceMappingURL=agenthubClient.js.map