from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app.api.im_assets import JS

router = APIRouter(tags=["im-ui"])


@router.get("/im", response_class=HTMLResponse)
async def im_page() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentHub IM</title>
  <link rel="stylesheet" href="/im.css" />
</head>
<body>
<div class="app" id="app" style="display:none">
  <aside class="rail">
    <div class="me" id="uava">A</div>
    <div class="rail-item active">消息<i class="red" id="navRed">0</i></div>
    <div class="rail-item">问答</div><div class="rail-item">文档</div><div class="rail-item">日历</div><div class="rail-item">更多</div>
  </aside>

  <aside class="conv">
    <div class="conv-head">
      <div class="title">消息<button class="icon" onclick="newSession()">＋</button></div>
      <div class="search">🔍<input id="q" placeholder="搜索会话" oninput="renderSessions()" /></div>
    </div>
    <div class="conv-list" id="clist"></div>
  </aside>

  <main class="chat">
    <header class="chat-top">
      <button class="icon">←</button>
      <div class="group-name" id="room">群聊协作频道</div>
      <div class="top-actions"><button class="icon">📌</button><button class="icon">⋯</button></div>
    </header>

    <section class="msg-zone" id="msgs"></section>

    <footer class="composer">
      <button class="icon">☰</button>
      <button class="icon" onclick="put('/help ')">/</button>
      <textarea id="inp" class="input" placeholder="输入消息，@ 提及成员，Shift+Enter 换行"></textarea>
      <button class="icon" onclick="put('😀')">😊</button>
      <button class="icon">📎</button>
      <button class="send" onclick="send()">发送</button>
    </footer>
  </main>
</div>

<div class="mention" id="mention"></div>

<div class="login" id="login">
  <form class="login-card" onsubmit="doLogin(event)">
    <h1>AgentHub IM</h1>
    <p>浅灰白飞书风群聊界面</p>
    <div class="note" id="note"></div>
    <label>用户名<input id="ln" value="admin" /></label>
    <label>密码<input id="lp" type="password" value="admin123" /></label>
    <button class="primary">登录</button>
    <button type="button" class="link" onclick="doRegister()">没有账号？注册</button>
  </form>
</div>

<script src="/im.js"></script>
</body>
</html>
"""


@router.get("/im.css")
async def im_css() -> Response:
    return Response(CSS, media_type="text/css")


@router.get("/im.js")
async def im_js() -> Response:
    return Response(JS, media_type="application/javascript")


CSS = """
:root{
  --bg:#f4f6fa;
  --panel:#ffffff;
  --line:#e5eaf3;
  --line2:#dfe5ef;
  --text:#1f2733;
  --muted:#6f7b8f;
  --muted2:#8b97aa;
  --blue:#2f80ff;
  --blue-soft:#eef4ff;
  --bubble:#edf0f5;
  --danger:#ff4d4f;
}
*{box-sizing:border-box}
body{
  margin:0;
  background:var(--bg);
  color:var(--text);
  font:14px/1.57 -apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
  height:100vh;
  overflow:hidden;
}
.app{display:grid;grid-template-columns:68px 340px 1fr;height:100vh}

/* 左导航 */
.rail{
  background:#eef2f8;
  border-right:1px solid var(--line);
  display:flex;
  flex-direction:column;
  align-items:center;
  padding:12px 8px;
  gap:10px;
}
.me{
  width:36px;height:36px;border-radius:50%;
  display:grid;place-items:center;
  background:var(--blue);color:#fff;
  font-size:14px;font-weight:700;letter-spacing:.2px;
}
.rail-item{
  position:relative;
  width:52px;height:50px;border-radius:12px;
  display:flex;align-items:center;justify-content:center;
  color:#5f6b7a;
  font-size:12px;font-weight:500;line-height:1;
  cursor:pointer;
}
.rail-item.active{
  background:#fff;
  color:var(--blue);
  font-weight:600;
  box-shadow:0 1px 2px rgba(31,39,51,.06);
}
.red{
  position:absolute;
  top:5px;right:5px;
  min-width:16px;height:16px;
  padding:0 4px;
  border-radius:8px;
  background:var(--danger);
  color:#fff;
  font-size:10px;
  line-height:16px;
  text-align:center;
  font-weight:600;
}

/* 会话列表 */
.conv{background:#f8fafc;border-right:1px solid var(--line);display:flex;flex-direction:column}
.conv-head{padding:14px;border-bottom:1px solid var(--line)}
.title{
  font-size:18px;
  font-weight:700;
  line-height:26px;
  display:flex;
  justify-content:space-between;
  align-items:center;
}
.search{
  margin-top:10px;
  height:34px;
  border-radius:10px;
  border:1px solid var(--line);
  background:#f0f3f7;
  display:flex;
  align-items:center;
  padding:0 10px;
  color:var(--muted2);
  font-size:13px;
}
.search input{
  border:0;background:transparent;outline:0;width:100%;padding-left:8px;
  font-size:13px;color:#445064;
}
.conv-list{overflow:auto;padding:8px}
.card{
  height:74px;
  border-radius:12px;
  padding:10px;
  display:grid;
  grid-template-columns:42px 1fr auto;
  gap:10px;
  cursor:pointer;
  transition:background .12s ease,border-color .12s ease;
  border:1px solid transparent;
}
.card:hover{background:#f2f6ff;border-color:#e6edff}
.card.active{background:var(--blue-soft);border-color:#d7e4ff}
.ava{
  width:42px;height:42px;border-radius:12px;
  background:#dbe7ff;color:var(--blue);
  font-weight:700;font-size:13px;
  display:grid;place-items:center;
}
.name{font-size:14px;font-weight:600;line-height:20px;letter-spacing:.1px}
.preview{
  font-size:12px;
  font-weight:400;
  color:#7b8798;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  margin-top:4px;line-height:18px;
}
.time{font-size:12px;color:#9aa4b5;line-height:18px}
.unread{
  min-width:16px;height:16px;padding:0 4px;
  border-radius:8px;
  background:var(--danger);color:#fff;
  font-size:10px;font-weight:600;
  display:grid;place-items:center;
  margin-top:10px;margin-left:auto;
}

/* 右侧聊天窗口 */
.chat{display:grid;grid-template-rows:60px 1fr 78px;background:var(--bg)}
.chat-top{
  height:60px;
  background:#fff;
  border-bottom:1px solid var(--line);
  display:grid;
  grid-template-columns:40px 1fr auto;
  align-items:center;
  padding:0 12px;
}
.group-name{
  text-align:center;
  font-size:16px;
  font-weight:600;
  letter-spacing:.1px;
  color:#1f2733;
}
.top-actions{display:flex;gap:8px}

.icon{
  min-width:32px;height:32px;
  border:0;background:transparent;
  border-radius:8px;
  color:#5f6b7a;
  cursor:pointer;
  display:grid;place-items:center;
  font-size:15px;
}
.icon:hover{background:#eef2f8}

/* 消息区 */
.msg-zone{overflow:auto;padding:18px 22px 14px}
.group{margin-bottom:14px}
.headline{
  display:flex;
  align-items:center;
  gap:10px;
  margin-bottom:6px;
  min-height:34px;
}
.mava{
  width:34px;height:34px;border-radius:50%;
  background:#e3e8f3;
  color:#556074;
  display:grid;place-items:center;
  font-size:12px;font-weight:700;
  flex:none;
}
.nick{
  font-size:12px;
  line-height:18px;
  font-weight:600;
  color:var(--muted);
  letter-spacing:.1px;
}
.msg{
  margin-left:44px;
  margin-top:4px;
  max-width:780px;
}
.msg:first-of-type{margin-top:0}
.bubble{
  display:inline-block;
  border-radius:14px;
  padding:9px 12px;
  background:var(--bubble);
  color:#1f2733;
  font-size:14px;
  line-height:22px;
  font-weight:400;
  white-space:pre-wrap;
  word-break:break-word;
  vertical-align:top;
}
/* 你的要求：自己气泡蓝底、文字黑色 */
.bubble.me{background:var(--blue);color:#111827}

/* 输入区 */
.composer{
  border-top:1px solid var(--line);
  background:#fff;
  display:grid;
  grid-template-columns:32px 32px 1fr 32px 32px 68px;
  gap:8px;
  align-items:center;
  padding:10px 12px;
}
.input{
  height:44px;
  border:1px solid var(--line);
  border-radius:12px;
  background:#f7f9fc;
  padding:10px 12px;
  resize:none;
  outline:0;
  font:14px/22px inherit;
  color:#1f2733;
}
.input::placeholder{color:#98a2b3}
.send{
  height:36px;
  border:0;
  border-radius:10px;
  background:var(--blue);
  color:#fff;
  font-size:14px;
  font-weight:600;
  cursor:pointer;
}
.send:hover{filter:brightness(.97)}

/* 登录层 */
.login{position:fixed;inset:0;display:grid;place-items:center;background:#f4f7fc}
.login-card{
  width:360px;background:#fff;border-radius:18px;padding:24px;
  box-shadow:0 10px 30px rgba(31,39,51,.08);
}
.login-card h1{margin:0 0 4px;font-size:26px;font-weight:700}
.login-card p{margin:0 0 8px;color:#6f7b8f;font-size:13px;line-height:20px}
.login-card label{display:block;margin:12px 0;font-size:13px;font-weight:600;color:#4b5565}
.login-card input{width:100%;height:40px;border:1px solid var(--line2);border-radius:10px;padding:0 12px;margin-top:6px}
.primary{width:100%;height:40px;border:0;border-radius:10px;background:var(--blue);color:#fff;font-weight:700}
.link{margin-top:10px;border:0;background:transparent;color:var(--blue)}
.note{display:none;margin:10px 0;padding:8px;border-radius:10px;background:#fff7e8;color:#9a5b00;font-size:13px}

/* @提及面板 */
.mention{
  position:fixed;bottom:84px;left:456px;width:280px;
  background:#fff;border:1px solid var(--line);border-radius:12px;
  box-shadow:0 10px 26px rgba(31,39,51,.08);
  display:none;overflow:hidden;
}
.mi{display:flex;gap:10px;padding:9px 12px;cursor:pointer}
.mi:hover{background:#f4f7fc}

@media(max-width:1100px){
  .app{grid-template-columns:60px 280px 1fr}
  .mention{left:370px}
}
"""
