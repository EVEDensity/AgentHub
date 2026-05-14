from __future__ import annotations

JS = r"""
const MEMBERS=[
  ['Orchestrator','任务拆解与调度'],['Architect','架构设计与方案'],['CodeGen','代码生成与补丁'],['Review','审查与风险提示'],['Test','测试与验证'],['Deploy','部署发布']
];
let token=localStorage.agenthub_token||'',user=JSON.parse(localStorage.agenthub_user||'null');
let ws=null,active='session-1',messages=[],pending=[];
let sessions=[
  {id:'session-1',n:'群聊协作频道',a:'群',un:3,t:'刚刚',p:'@CodeGen 生成 health_router.py'},
  {id:'task-2',n:'Agent 任务2',a:'A2',un:1,t:'昨天',p:'部署流程演练'},
  {id:'task-3',n:'Agent 任务3',a:'A3',un:0,t:'4月19日',p:'代码评审'}
];

const h=(x={})=>token?{...x,Authorization:'Bearer '+token}:x;
const esc=s=>String(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const av=s=>(s||'A').replace('Orchestrator','O').replace('Architect','A').replace('CodeGen','C').replace('Review','R').replace('Test','T').replace('Deploy','D').slice(0,2);

async function doLogin(e){e.preventDefault();const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:ln.value,password:lp.value})});const d=await r.json();if(!r.ok){note.style.display='block';note.textContent=d.detail||'登录失败';return;}token=d.accessToken;user=d.user;localStorage.agenthub_token=token;localStorage.agenthub_user=JSON.stringify(user);boot();}
async function doRegister(){const r=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:ln.value,password:lp.value,role:'developer'})});const d=await r.json();if(!r.ok){note.style.display='block';note.textContent=d.detail||'注册失败';return;}token=d.accessToken;user=d.user;localStorage.agenthub_token=token;localStorage.agenthub_user=JSON.stringify(user);boot();}

async function boot(){login.style.display='none';app.style.display='grid';uava.textContent=(user?.name||'A').slice(0,1).toUpperCase();renderSessions();await loadMessages();connectWs();}

async function loadMessages(){
  try{const r=await fetch(`/api/chat/sessions/${active}/messages`,{headers:h()});const d=await r.json();messages=Array.isArray(d)?d:[];}catch{}
  if(!messages.length){
    messages=[
      {sender:'Orchestrator',content:'欢迎进入群聊协作频道。\n我会负责任务拆解。',timestamp:new Date().toISOString(),type:'text'},
      {sender:'Orchestrator',content:'你可以 @CodeGen 直接发起代码任务。',timestamp:new Date().toISOString(),type:'text'},
      {sender:'admin',content:'@CodeGen 生成 FastAPI health 路由文件',timestamp:new Date().toISOString(),type:'text'},
      {sender:'CodeGen',content:'已生成结构化文件，请确认提交。',timestamp:new Date().toISOString(),type:'text'}
    ];
  }
  renderMessages();
}

function connectWs(){
  if(ws) ws.close();
  ws=new WebSocket(`ws://${location.hostname}:8000/ws/${active}?token=${encodeURIComponent(token)}`);
  ws.onopen=()=>{};
  ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.event==='message'){messages.push(d);syncSessionPreview(d);renderMessages();renderSessions();}};
  ws.onclose=()=>setTimeout(connectWs,1500);
}

function renderSessions(){
  const qv=(q.value||'').toLowerCase();
  const list=sessions.filter(s=>s.n.toLowerCase().includes(qv)||s.p.toLowerCase().includes(qv));
  clist.innerHTML=list.map(s=>`<div class="card ${s.id===active?'active':''}" onclick="switchSession('${s.id}')"><div class="ava">${s.a}</div><div><div class="name">${s.n}</div><div class="preview">${esc(s.p)}</div></div><div><div class="time">${s.t}</div>${s.un?`<div class="unread">${s.un}</div>`:''}</div></div>`).join('');
  navRed.textContent=sessions.reduce((n,s)=>n+(s.un||0),0);
}

function switchSession(id){active=id;sessions=sessions.map(s=>s.id===id?{...s,un:0}:s);room.textContent=sessions.find(s=>s.id===id)?.n||id;messages=[];renderSessions();loadMessages();connectWs();}
function newSession(){const id='task-'+Date.now();sessions.unshift({id,n:'新群聊任务',a:'新',un:0,t:'刚刚',p:'新的群聊协作会话'});switchSession(id);}
function syncSessionPreview(m){sessions=sessions.map(s=>s.id===active?{...s,p:(m.sender?m.sender+'：':'')+(m.content||'').slice(0,28),t:'刚刚'}:s);}

function toBlocks(items){
  const blocks=[];
  for(const m of items){
    const prev=blocks[blocks.length-1];
    if(prev && prev.sender===m.sender){ prev.msgs.push(m); }
    else{ blocks.push({sender:m.sender,msgs:[m]}); }
  }
  return blocks;
}

function bubbleClass(sender){ return (sender===user?.name||sender==='admin'||sender==='user') ? 'bubble me' : 'bubble'; }

function renderMessages(){
  const blocks=toBlocks(messages);
  msgs.innerHTML=blocks.map(b=>{
    const head=`<div class="headline"><div class="mava">${av(b.sender)}</div><div class="nick">${esc(b.sender||'成员')}</div></div>`;
    const body=b.msgs.map(x=>`<div class="msg"><div class="${bubbleClass(x.sender)}">${esc(x.content||'').replace(/\n/g,'<br>')}</div></div>`).join('');
    return `<div class="group">${head}${body}</div>`;
  }).join('');
  msgs.scrollTop=msgs.scrollHeight;
}

function send(){
  const text=inp.value.trim(); if(!text) return;
  const m={sessionId:active,content:text,sender:user?.name||'user',timestamp:new Date().toISOString(),type:'text'};
  messages.push(m); syncSessionPreview(m); renderMessages(); renderSessions();
  if(ws?.readyState===1) ws.send(JSON.stringify(m)); else pending.push(m);
  inp.value='';
}

inp?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}setTimeout(checkMention,0);});
inp?.addEventListener('input',checkMention);

function checkMention(){
  const v=inp.value; const at=v.lastIndexOf('@');
  if(at<0){mention.style.display='none';return;}
  const qv=v.slice(at+1).split(/\s/)[0].toLowerCase();
  const items=MEMBERS.filter(x=>x[0].toLowerCase().includes(qv));
  mention.style.display=items.length?'block':'none';
  mention.innerHTML=items.map(x=>`<div class="mi" onclick="pickMention('${x[0]}')"><div class="mava">${av(x[0])}</div><div><b>${x[0]}</b><div class="nick">${x[1]}</div></div></div>`).join('');
}

function pickMention(name){const v=inp.value;const at=v.lastIndexOf('@');inp.value=v.slice(0,at)+'@'+name+' '+v.slice(at+1).replace(/^\S*/,'');mention.style.display='none';inp.focus();}
function put(t){const s=inp.selectionStart;inp.value=inp.value.slice(0,s)+t+inp.value.slice(inp.selectionEnd);inp.focus();}

if(token&&user) boot();
"""
