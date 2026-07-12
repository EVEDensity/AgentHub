import { NextResponse } from 'next/server';

/**
 * POST /api/chat/send
 *
 * Proxies chat messages to the Go gateway's public chat endpoint. For
 * streaming requests (stream: true), the gateway returns an SSE stream
 * which we relay back to the client. For non-streaming requests we
 * collect the full response and return it as JSON.
 *
 * The server-side API key is injected from GATEWAY_API_KEY (or
 * NEXT_PUBLIC_BOT_API_KEY as fallback) so the token never reaches the
 * browser.
 */
export async function POST(request: Request): Promise<NextResponse> {
  const gatewayUrl =
    process.env.GO_GATEWAY_URL || 'http://127.0.0.1:8081';
  const apiKey =
    process.env.GATEWAY_API_KEY ||
    process.env.NEXT_PUBLIC_BOT_API_KEY ||
    '';

  let body: {
    message?: string;
    agent_id?: string;
    session_id?: string;
    stream?: boolean;
    metadata?: Record<string, unknown>;
  };

  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: 'invalid json body' },
      { status: 400 }
    );
  }

  if (!body.message?.trim()) {
    return NextResponse.json(
      { error: 'message is required' },
      { status: 400 }
    );
  }

  const wantsStream = body.stream === true;

  try {
    const upstream = await fetch(`${gatewayUrl}/v1/public/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      body: JSON.stringify({
        message: body.message,
        agent_id: body.agent_id,
        session_id: body.session_id,
        stream: wantsStream,
        metadata: body.metadata,
      }),
      signal: AbortSignal.timeout(wantsStream ? 60000 : 15000),
    });

    if (!upstream.ok) {
      const errText = await upstream.text().catch(() => '');
      return NextResponse.json(
        { error: `Gateway error: ${upstream.status}${errText ? ` — ${errText}` : ''}` },
        { status: upstream.status }
      );
    }

    // If the client asked for streaming, relay SSE bytes back.
    if (wantsStream && upstream.body) {
      const encoder = new TextEncoder();
      const stream = new ReadableStream({
        async start(controller) {
          const reader = upstream.body!.getReader();
          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              controller.enqueue(value);
            }
          } finally {
            reader.releaseLock();
            controller.close();
          }
        },
      });

      return new NextResponse(stream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
          'X-Accel-Buffering': 'no',
        },
      });
    }

    // Non-streaming: parse JSON and return
    const data = await upstream.json();

    return NextResponse.json({
      status: data.status || 'accepted',
      session_id: data.session_id,
      trace_id: data.trace_id,
      content: data.reply || data.content || '',
      stream: data.stream || false,
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Gateway unreachable: ${(err as Error).message}` },
      { status: 502 }
    );
  }
}
