import { NextResponse } from 'next/server';

/**
 * GET /api/agent/registry/[botId]
 *
 * Proxies to the Go gateway's public bot config endpoint. The gateway returns
 * branding, theme, and welcome-message metadata for the published bot page.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ botId: string }> }
): Promise<NextResponse> {
  const { botId } = await params;

  const gatewayUrl =
    process.env.GO_GATEWAY_URL || 'http://127.0.0.1:8081';

  try {
    const res = await fetch(`${gatewayUrl}/api/public/bots/${botId}`, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(3000),
    });

    if (!res.ok) {
      // Return defaults so the page still renders without a gateway
      return NextResponse.json({
        display_name: botId,
        welcome_message: `你好！我是 ${botId} Agent。有什么可以帮助你的？`,
        theme_color: '#6366f1',
        suggestions: ['你能做什么？', '介绍一下你自己', '帮我分析一个问题'],
      });
    }

    const data = await res.json();

    // Translate gateway publicBotConfig → App Router page expected format
    return NextResponse.json({
      display_name: data.name || botId,
      welcome_message: data.welcomeMessage,
      theme_color: data.themeColor || '#6366f1',
      logo_url: data.logoUrl || '',
      suggestions: data.suggestedQuestions || [],
      title: data.name || botId,
    });
  } catch {
    // Gateway unreachable — return defaults
    return NextResponse.json({
      display_name: botId,
      welcome_message: `你好！我是 ${botId} Agent。有什么可以帮助你的？`,
      theme_color: '#6366f1',
      suggestions: ['你能做什么？', '介绍一下你自己', '帮我分析一个问题'],
    });
  }
}
