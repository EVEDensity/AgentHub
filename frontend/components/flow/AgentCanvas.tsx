import { useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { Circle, Group, Layer, Line, Rect, Stage, Text, Transformer, Arrow } from 'react-konva';
import type Konva from 'konva';

type T='start'|'agent'|'tool'|'decision'|'end';
type N={id:string;type:T;title:string;desc:string;x:number;y:number;w:number;h:number;color:string;z:number;agent?:string;layer?:string};
type E={id:string;from:string;to:string;label?:string};
type D={nodes:N[];edges:E[];viewport:{x:number;y:number;scale:number}};

interface EmbedData {
  id?: number;
  name: string;
  description: string;
  triggerKeywords: string[];
  nodes: { id:string; type:string; name:string; description:string; x:number; y:number; agent?:string; layer?:string; dependencies:string[] }[];
  edges: { from:string; to:string; label?:string }[];
  isDefault: boolean;
  active: boolean;
}

const CID='agent-flow',W=260,H=128,G=28;
const lib:Array<{type:T;title:string;desc:string;color:string}>=[
 {type:'start',title:'Start',desc:'触发工作流输入',color:'#22A06B'},{type:'agent',title:'Agent',desc:'LLM 角色节点',color:'#4F6CF7'},
 {type:'tool',title:'Tool',desc:'调用外部工具/API',color:'#8B5CF6'},{type:'decision',title:'IF / ELSE',desc:'条件分支判断',color:'#D97706'},{type:'end',title:'End',desc:'输出最终结果',color:'#64748B'}];
const init:D={viewport:{x:0,y:0,scale:1},nodes:[
 {id:'start',type:'start',title:'Start',desc:'接收用户需求',x:100,y:210,w:W,h:H,color:'#22A06B',z:0},
 {id:'orchestrator',type:'agent',title:'Orchestrator',desc:'拆解任务并调度 Agent',x:440,y:210,w:W,h:H,color:'#4F6CF7',z:1},
 {id:'architect',type:'agent',title:'Architect',desc:'设计低代码连接方案',x:780,y:100,w:W,h:H,color:'#4F6CF7',z:2},
 {id:'codegen',type:'agent',title:'CodeGen',desc:'生成接口与页面代码',x:780,y:320,w:W,h:H,color:'#4F6CF7',z:3},
 {id:'end',type:'end',title:'End',desc:'汇总输出与执行结果',x:1120,y:210,w:W,h:H,color:'#64748B',z:4}],
 edges:[{id:'e1',from:'start',to:'orchestrator',label:'input'},{id:'e2',from:'orchestrator',to:'architect',label:'plan'},{id:'e3',from:'orchestrator',to:'codegen',label:'build'},{id:'e4',from:'architect',to:'end',label:'review'},{id:'e5',from:'codegen',to:'end',label:'merge'}]};
const cp=(d:D):D=>JSON.parse(JSON.stringify(d)) as D,lim=(n:number)=>Math.max(.28,Math.min(2.2,n)),ctr=(n:N)=>({x:n.x+n.w/2,y:n.y+n.h/2});
function rectRayIntersection(node:N,target:{x:number;y:number}){const c=ctr(node),dx=target.x-c.x,dy=target.y-c.y;if(dx===0&&dy===0)return{point:{x:c.x,y:c.y},normal:{x:1,y:0}};const hw=node.w/2,hh=node.h/2,ax=Math.abs(dx),ay=Math.abs(dy);if(ax*hh>=ay*hw){const sx=Math.sign(dx)||1;const x=c.x+sx*hw;const y=c.y+dy*(hw/ax);return{point:{x,y},normal:{x:sx,y:0}}}const sy=Math.sign(dy)||1;const y=c.y+sy*hh;const x=c.x+dx*(hh/ay);return{point:{x,y},normal:{x:0,y:sy}}}
function edgePath(a:N,b:N){
 const ca=ctr(a),cb=ctr(b);
 const sa=rectRayIntersection(a,cb);
 const sb=rectRayIntersection(b,ca);
 const s=sa.point,e=sb.point;
 const dx=e.x-s.x,dy=e.y-s.y;
 const dist=Math.hypot(dx,dy);
 const along=Math.max(60,Math.min(176,dist*0.4));
 const curve=Math.max(0,Math.min(52,dist*0.09));
 const c1={x:s.x+sa.normal.x*along-sa.normal.y*curve,y:s.y+sa.normal.y*along+sa.normal.x*curve};
 const c2={x:e.x+sb.normal.x*along+sb.normal.y*curve,y:e.y+sb.normal.y*along-sb.normal.x*curve};
 return{start:s,label:{x:(s.x+e.x)/2,y:(s.y+e.y)/2},points:[s.x,s.y,c1.x,c1.y,c2.x,c2.y,e.x,e.y]}
}

function embedToDoc(ed:EmbedData):D{
  const nodes:N[]=ed.nodes.map((n,i)=>{
    const typeMap:Record<string,T>={start:'start',agent:'agent',tool:'tool',ifelse:'decision',decision:'decision',end:'end'};
    const t:T=typeMap[n.type]||'agent';
    const colorMap:Record<T,string>={start:'#22A06B',agent:'#4F6CF7',tool:'#8B5CF6',decision:'#D97706',end:'#64748B'};
    return{id:n.id,type:t,title:n.name,desc:n.description,x:n.x||300+Math.random()*400,y:n.y||200+Math.random()*300,w:W,h:H,color:n.type==='tool'?colorMap.tool:colorMap[t],z:i,agent:n.agent,layer:n.layer};
  });
  const edges:E[]=ed.edges.map((e,i)=>({id:`ee-${i}`,from:e.from,to:e.to,label:e.label}));
  return{viewport:{x:0,y:0,scale:1},nodes,edges};
}

function docToEmbed(doc:D,embedId?:number,name?:string,desc?:string,keys?:string,isDefault?:boolean,active?:boolean):any{
  const deps= new Map<string,string[]>();
  doc.edges.forEach(e=>{const a=deps.get(e.to)||[];a.push(e.from);deps.set(e.to,a)});
  return{
    id:embedId,
    name:name||'未命名工作流',
    description:desc||'',
    triggerKeywords:(keys||'').split(',').map(k=>k.trim()).filter(Boolean),
    nodes:doc.nodes.map(n=>({
      id:n.id,type:n.type==='decision'?'ifelse':n.type,name:n.title,description:n.desc,
      x:n.x,y:n.y,agent:n.agent||n.title,layer:n.layer||'domain',dependencies:deps.get(n.id)||[]
    })),
    edges:doc.edges.map(e=>({from:e.from,to:e.to,label:e.label})),
    isDefault:isDefault||false,active:active??true
  };
}

function normalize(d:D|null|undefined):D{if(!d?.nodes?.length)return init;return{viewport:{x:Number.isFinite(d.viewport?.x)?d.viewport.x:0,y:Number.isFinite(d.viewport?.y)?d.viewport.y:0,scale:lim(Number.isFinite(d.viewport?.scale)?d.viewport.scale:1)},nodes:d.nodes.map((n,i)=>({...n,w:Math.max(200,n.w||W),h:Math.max(100,n.h||H),z:Number.isFinite(n.z)?n.z:i})),edges:Array.isArray(d.edges)?d.edges:[]}}

export default function AgentCanvas(props?:{
  embedded?:boolean;initialData?:EmbedData;
  agents?:{agentId:string;domain:string}[];
  onSave?:(d:any)=>void;onDelete?:()=>void;
}):JSX.Element{
 const embedded=props?.embedded||false;
 const edRef=useRef(props?.initialData);
 const[doc,setDoc]=useState<D>(()=>embedded&&edRef.current?embedToDoc(edRef.current):init);
 const[sel,setSel]=useState(embedded&&edRef.current?.nodes[0]?edRef.current.nodes[0].id:'orchestrator');
 const[from,setFrom]=useState(''),[msg,setMsg]=useState(''),[sz,setSz]=useState({width:900,height:720});
 const[hist,setHist]=useState<D[]>([embedded&&edRef.current?embedToDoc(edRef.current):init]);const[hi,setHi]=useState(0);
 const[pressed,setPressed]=useState(''),[edgeHover,setEdgeHover]=useState('');
 const hiRef=useRef(0),stage=useRef<Konva.Stage|null>(null),tr=useRef<Konva.Transformer|null>(null);
 const refs=useRef<Record<string,Konva.Group|null>>({}),wrap=useRef<HTMLDivElement|null>(null),fromRef=useRef('');

 const[wfName,setWfName]=useState(props?.initialData?.name||'');
 const[wfDesc,setWfDesc]=useState(props?.initialData?.description||'');
 const[wfKeys,setWfKeys]=useState(props?.initialData?.triggerKeywords?.join(',')||'');

 const sn=doc.nodes.find(n=>n.id===sel)||null;
 const map=useMemo(()=>new Map(doc.nodes.map(n=>[n.id,n])),[doc.nodes]);
 const sorted=useMemo(()=>[...doc.nodes].sort((a,b)=>a.z-b.z),[doc.nodes]);
 const grid=useMemo(()=>{const{x,y,scale}=doc.viewport,l=Math.floor((-x/scale)/G)-2,r=Math.ceil(((sz.width-x)/scale)/G)+2,t=Math.floor((-y/scale)/G)-2,b=Math.ceil(((sz.height-y)/scale)/G)+2;return{v:Array.from({length:Math.max(0,r-l+1)},(_,i)=>(l+i)*G),h:Array.from({length:Math.max(0,b-t+1)},(_,i)=>(t+i)*G),minX:l*G,maxX:r*G,minY:t*G,maxY:b*G}},[doc.viewport,sz]);

 function commit(d:D,silent=false){const nd=normalize(d);setDoc(nd);if(!silent)setHist(p=>{const h=[...p.slice(0,hiRef.current+1),cp(nd)].slice(-80);hiRef.current=h.length-1;setHi(hiRef.current);return h})}
 function patch(id:string,p:Partial<N>,silent=false){commit({...doc,nodes:doc.nodes.map(n=>n.id===id?{...n,...p}:n)},silent)}
 function undo(){if(hiRef.current>0){hiRef.current-=1;setHi(hiRef.current);setDoc(cp(hist[hiRef.current]))}}
 function redo(){if(hiRef.current<hist.length-1){hiRef.current+=1;setHi(hiRef.current);setDoc(cp(hist[hiRef.current]))}}
 function del(){if(!sel)return;commit({...doc,nodes:doc.nodes.filter(n=>n.id!==sel),edges:doc.edges.filter(e=>e.from!==sel&&e.to!==sel)});setSel('');setFrom('');fromRef.current='';setMsg('已删除节点与关联连线')}
 function dup(){if(!sn)return;const n={...sn,id:`${sn.id}-copy-${Date.now()}`,title:`${sn.title} Copy`,x:sn.x+40,y:sn.y+40,z:doc.nodes.length+1};commit({...doc,nodes:[...doc.nodes,n]});setSel(n.id)}
 function add(b:(typeof lib)[number]){const i=doc.nodes.length+1,n:N={id:`${b.type}-${Date.now()}`,type:b.type,title:b.title==='Agent'?`Agent ${i}`:b.title,desc:b.desc,x:190+i*36,y:140+i*28,w:W,h:H,color:b.color,z:i,agent:embedded&&b.type==='agent'?props?.agents?.[0]?.agentId:undefined,layer:b.type==='start'?'meta':b.type==='end'?'micro':'domain'};commit({...doc,nodes:[...doc.nodes,n]});setSel(n.id)}
 function link(to:string){const f=fromRef.current||from;if(!f){setFrom(to);fromRef.current=to;setMsg('连线模式：请选择目标节点完成业务关系');return}if(f===to){setFrom('');fromRef.current='';setMsg('已取消自连接');return}if(doc.edges.some(e=>e.from===f&&e.to===to)){setFrom('');fromRef.current='';setMsg('该流程关系已存在');return}const a=map.get(f),b=map.get(to),label=a?.type==='decision'?'branch':b?.type==='tool'?'call':'flow';commit({...doc,edges:[...doc.edges,{id:`e-${Date.now()}`,from:f,to,label}]});setMsg('连线已创建');setFrom('');fromRef.current=''}

 async function save(){
  if(embedded){
   const edId=props?.initialData?.id;
   const d=docToEmbed(doc,edId,wfName,wfDesc,wfKeys,props?.initialData?.isDefault,props?.initialData?.active);
   props?.onSave?.(d);
   setMsg('工作流已保存');
   return;
  }
  const ok=await fetch('/api/canvas/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:CID,name:'Agent Workflow Canvas',data:doc})}).then(()=>true).catch(()=>false);
  setMsg(ok?'画布已保存':'保存失败')
 }
 async function exp(){const r=await fetch('/api/canvas/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:CID,image:stage.current?.toDataURL({pixelRatio:2}),data:doc})}).then(r=>r.ok?r.json():null).catch(()=>null);if(r?.url)window.open(r.url,'_blank');setMsg(r?'已导出 PNG':'导出失败')}

 useEffect(()=>{const el=wrap.current;if(!el)return;const f=()=>{const b=el.getBoundingClientRect();setSz({width:Math.max(Math.floor(b.width),720),height:Math.max(Math.floor(b.height),520)})};f();const ro=new ResizeObserver(f);ro.observe(el);return()=>ro.disconnect()},[]);

 useEffect(()=>{
  if(embedded)return;
  fetch(`/api/canvas/${CID}`).then(r=>r.json()).then(r=>{const d=normalize(r.data||init);setDoc(d);setHist([cp(d)]);hiRef.current=0;setHi(0)}).catch(()=>setMsg('加载失败，已使用默认模板'))
 },[]);

 useEffect(()=>{const n=sel?refs.current[sel]:null;tr.current?.nodes(n?[n]:[]);tr.current?.getLayer()?.batchDraw()},[sel,doc.nodes]);

 useEffect(()=>{const f=(e:KeyboardEvent)=>{const t=e.target as HTMLElement;if(['INPUT','TEXTAREA'].includes(t.tagName))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo()}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='d'){e.preventDefault();dup()}if(e.key==='Escape'){setFrom('');fromRef.current='';setMsg('已退出连线模式')}if(e.key==='Delete'||e.key==='Backspace')del()};window.addEventListener('keydown',f);return()=>window.removeEventListener('keydown',f)});

 const Btn=({children,onClick,disabled}:{children:string;onClick:()=>void;disabled?:boolean})=><button className="btn-secondary" disabled={disabled} onClick={onClick}>{children}</button>;
 const agentOpts=props?.agents||[];
 const depMap=useMemo(()=>{const m=new Map<string,string[]>();doc.edges.forEach(e=>{const a=m.get(e.to)||[];a.push(e.from);m.set(e.to,a)});return m},[doc.edges]);

 return <div className={`flex ${embedded?'h-full':'h-screen'} bg-[#F8F7F4] text-warm-800`}>
  {/* Left Sidebar — Blocks */}
  <aside className="flex w-[272px] flex-shrink-0 flex-col border-r border-warm-150 bg-white">
   <div className="border-b border-warm-150 px-6 py-6">
    <div className="text-lg font-semibold tracking-tight text-warm-800">Agent Flow</div>
    <p className="mt-1 text-xs text-warm-400">低代码 Agent 连接画布</p>
    {!embedded&&<a className="btn-ghost mt-4 inline-flex px-0 text-xs text-primary-500" href="/">← 返回会话</a>}
    {embedded&&<a className="btn-ghost mt-4 inline-flex px-0 text-xs text-primary-500" href="/canvas" target="_blank">全屏画布 →</a>}
   </div>
   <div className="flex-1 overflow-y-auto px-4 py-5">
    <div className="mb-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-warm-400">节点类型</div>
    <div className="space-y-2.5">{lib.map(b=><button key={b.type} className="group w-full rounded-xl border border-warm-150 bg-white p-4 text-left transition-all duration-200 hover:border-primary-200 hover:shadow-card hover:-translate-y-px" onClick={()=>add(b)}>
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg" style={{background:`${b.color}14`}}>
          <span className="h-2.5 w-2.5 rounded-full" style={{background:b.color}}/>
        </span>
        <div>
          <div className="text-sm font-semibold text-warm-800">{b.title}</div>
          <div className="mt-0.5 text-[11px] text-warm-400">{b.desc}</div>
        </div>
      </div>
    </button>)}</div>
   </div>
   <div className="border-t border-warm-150 px-4 py-4 text-[11px] leading-relaxed text-warm-400">
    <kbd className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-500">Ctrl+Z</kbd> 撤销 · <kbd className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-500">Shift+Z</kbd> 重做<br/>
    <kbd className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-500">Ctrl+D</kbd> 复制 · <kbd className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-500">Esc</kbd> 退出连线 · <kbd className="rounded bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-500">Del</kbd> 删除
   </div>
  </aside>

  {/* Main Area */}
  <main className="flex min-w-0 flex-1 flex-col">
   {/* Toolbar */}
   <header className="flex h-14 items-center justify-between border-b border-warm-150 bg-white px-5">
    <div>
     {embedded?<div className="flex items-center gap-3">
      <input className="input-field w-36 text-sm" placeholder="工作流名称" value={wfName} onChange={e=>setWfName(e.target.value)}/>
      <input className="input-field w-44 text-sm" placeholder="描述" value={wfDesc} onChange={e=>setWfDesc(e.target.value)}/>
      <input className="input-field w-40 text-sm" placeholder="触发关键词（逗号分隔）" value={wfKeys} onChange={e=>setWfKeys(e.target.value)}/>
     </div>:<><div className="text-sm font-semibold text-warm-800">Workflow Canvas</div><div className="text-[11px] text-warm-400">缩放 {Math.round(doc.viewport.scale*100)}% · 节点 {doc.nodes.length} · 连线 {doc.edges.length}</div></>}
    </div>
    <div className="flex items-center gap-1.5">
     {msg&&<span className="mr-2 rounded-lg bg-primary-50 px-3 py-1.5 text-[11px] text-primary-600">{msg}</span>}
     <Btn onClick={undo} disabled={hi<=0}>撤销</Btn><Btn onClick={redo} disabled={hi>=hist.length-1}>重做</Btn>
     <Btn onClick={()=>{const ns=lim(doc.viewport.scale-.1),cx=sz.width/2,cy=sz.height/2,wx=(cx-doc.viewport.x)/doc.viewport.scale,wy=(cy-doc.viewport.y)/doc.viewport.scale;commit({...doc,viewport:{x:cx-wx*ns,y:cy-wy*ns,scale:ns}},true)}}>缩小</Btn>
     <Btn onClick={()=>{const ns=lim(doc.viewport.scale+.1),cx=sz.width/2,cy=sz.height/2,wx=(cx-doc.viewport.x)/doc.viewport.scale,wy=(cy-doc.viewport.y)/doc.viewport.scale;commit({...doc,viewport:{x:cx-wx*ns,y:cy-wy*ns,scale:ns}},true)}}>放大</Btn>
     <Btn onClick={()=>{commit({...doc,viewport:{x:0,y:0,scale:1}},true);setMsg('视角已重置')}}>重置视角</Btn>
     {!embedded&&<Btn onClick={exp}>导出 PNG</Btn>}
     {embedded&&props?.initialData?.id&&<button className="btn-ghost text-sm text-red-500" onClick={()=>{if(window.confirm(`确认删除工作流 "${wfName}"？`))props?.onDelete?.()}}>删除工作流</button>}
     <button className="btn-primary" onClick={save}>保存</button>
    </div>
   </header>

   {/* Canvas + Right Sidebar */}
   <div className="flex min-h-0 flex-1">
    {/* Canvas — single grid layer from Konva only, no CSS background-image */}
    <section ref={wrap} className="relative flex-1 overflow-hidden bg-[#FAF9F6]">
     <Stage ref={stage} width={sz.width} height={sz.height} x={doc.viewport.x} y={doc.viewport.y} scaleX={doc.viewport.scale} scaleY={doc.viewport.scale} draggable dragDistance={3}
      onWheel={e=>{e.evt.preventDefault();const p=e.target.getStage()?.getPointerPosition();if(!p)return;const os=doc.viewport.scale,ns=lim(os*(e.evt.deltaY>0?.92:1.08)),mp={x:(p.x-doc.viewport.x)/os,y:(p.y-doc.viewport.y)/os};commit({...doc,viewport:{x:p.x-mp.x*ns,y:p.y-mp.y*ns,scale:ns}},true)}}
      onDragEnd={e=>{if(e.target!==e.target.getStage())return;commit({...doc,viewport:{...doc.viewport,x:e.target.x(),y:e.target.y()}},true)}}
      onMouseDown={e=>{if(e.target===e.target.getStage()){setSel('');setPressed('')}}}>
      {/* Grid layer — large + fine dots for depth */}
      <Layer listening={false}>
       {/* Fine grid */}
       {grid.v.map(x=><Line key={`v${x}`} points={[x,grid.minY,x,grid.maxY]} stroke={x%(G*4)===0?'#D8D6CF':'#ECEAE4'} strokeWidth={x%(G*4)===0?1:.6} lineCap="round"/>)}
       {grid.h.map(y=><Line key={`h${y}`} points={[grid.minX,y,grid.maxX,y]} stroke={y%(G*4)===0?'#D8D6CF':'#ECEAE4'} strokeWidth={y%(G*4)===0?1:.6} lineCap="round"/>)}
      </Layer>
      {/* Nodes & Edges */}
      <Layer>
       {/* Edges */}
       {doc.edges.map(e=>{const a=map.get(e.from),b=map.get(e.to);if(!a||!b)return null;const p=edgePath(a,b),act=edgeHover===e.id||sel===e.from||sel===e.to;return <Group key={e.id} onMouseEnter={()=>setEdgeHover(e.id)} onMouseLeave={()=>setEdgeHover('')}>
        <Arrow points={p.points} bezier stroke={act?'#4F6CF7':'#B8B5AC'} fill={act?'#4F6CF7':'#B8B5AC'} strokeWidth={act?3:2} pointerLength={11} pointerWidth={11} lineCap="round" lineJoin="round" shadowColor={act?'#4F6CF7':'#000'} shadowBlur={act?14:4} shadowOpacity={act?.2:.06}/>
        <Circle x={p.start.x} y={p.start.y} radius={4} fill={a.color} stroke="#FFF" strokeWidth={2}/>
        {e.label&&<Group x={p.label.x-28} y={p.label.y-16}>
         <Rect width={56} height={26} cornerRadius={13} fill="#FFF" stroke={act?'#C7D7FE':'#E6E5DF'} shadowBlur={6} shadowOpacity={.06}/>
         <Text x={8} y={6} width={40} align="center" text={e.label} fontSize={11} fontStyle="500" fill={act?'#4F6CF7':'#6D6B65'}/>
        </Group>}
       </Group>})}
       {/* Nodes */}
       {sorted.map(n=>{
        const isSelected=sel===n.id;
        const isConnecting=from===n.id;
        const NODE_PAD=16;
        const accentH=36;
        return <Group key={n.id} ref={r=>{refs.current[n.id]=r}} x={n.x} y={n.y} draggable dragDistance={3}
          scaleX={pressed===n.id?.98:1} scaleY={pressed===n.id?.98:1}
          onMouseDown={()=>setPressed(n.id)} onMouseUp={()=>setPressed('')}
          onClick={()=>{setSel(n.id);link(n.id)}} onTap={()=>setSel(n.id)}
          onDragStart={e=>{e.cancelBubble=true;setSel(n.id);setPressed(n.id)}}
          onDragEnd={e=>{e.cancelBubble=true;setPressed('');patch(n.id,{x:e.target.x(),y:e.target.y()})}}
          onTransformEnd={e=>{const t=e.target,sx=t.scaleX(),sy=t.scaleY();t.scaleX(1);t.scaleY(1);patch(n.id,{x:t.x(),y:t.y(),w:Math.max(200,n.w*sx),h:Math.max(100,n.h*sy)})}}>

          {/* Card background */}
          <Rect width={n.w} height={n.h} cornerRadius={16} fill="#FFF"
            stroke={isConnecting?'#1C1B19':isSelected?n.color:'#E6E5DF'}
            strokeWidth={isConnecting?2.5:isSelected?2:1}
            shadowColor={isSelected||isConnecting?n.color:'#1C1B19'}
            shadowBlur={isSelected||isConnecting?28:10}
            shadowOpacity={isSelected||isConnecting?.15:.05}
            shadowOffsetY={isSelected||isConnecting?8:4}/>

          {/* Top accent strip */}
          <Rect width={n.w} height={4} cornerRadius={[16,16,0,0]} fill={n.color} opacity={.85}/>

          {/* Type badge */}
          <Group x={NODE_PAD} y={NODE_PAD}>
            <Rect width={n.w-NODE_PAD*2} height={accentH} cornerRadius={10} fill={`${n.color}0D`}/>
            <Circle x={NODE_PAD-2} y={accentH/2} radius={5} fill={n.color}/>
            <Text x={NODE_PAD+8} y={accentH/2-7} text={n.type.toUpperCase()} fontSize={11} fill={n.color} fontStyle="bold" letterSpacing={1.2}/>
            <Text x={NODE_PAD+8} y={accentH/2+4} text={n.agent||''} fontSize={10} fill={n.color} opacity={.6}/>
          </Group>

          {/* Title */}
          <Text x={NODE_PAD+4} y={NODE_PAD+accentH+14} text={n.title} fontSize={16} fontStyle="bold" fill="#24231F" width={n.w-(NODE_PAD+4)*2}/>

          {/* Description */}
          <Text x={NODE_PAD+4} y={NODE_PAD+accentH+40} text={n.desc||' '} fontSize={12} fill="#8D8B84" width={n.w-(NODE_PAD+4)*2-28} lineHeight={1.35}/>

          {/* Connection handle (right edge) */}
          <Circle x={n.w-14} y={n.h/2} radius={10} fill={isConnecting?'#1C1B19':n.color} stroke="#FFF" strokeWidth={2.5}/>
          <Circle x={n.w-14} y={n.h/2} radius={16} stroke={isConnecting?'#1C1B19':n.color} strokeWidth={1.5} opacity={isSelected||isConnecting?.28:.10}/>
        </Group>
       })}
       <Transformer ref={tr} rotateEnabled={false} boundBoxFunc={(oldBox,newBox)=>newBox.width<200||newBox.height<100?oldBox:newBox}
        borderStroke="#4F6CF7" borderStrokeWidth={1.5} borderDash={[6,3]}
        anchorFill="#FFF" anchorStroke="#4F6CF7" anchorSize={8} anchorCornerRadius={4}/>
      </Layer>
     </Stage>
     {/* Floating status bar */}
     <div className="pointer-events-none absolute bottom-4 left-4 rounded-xl border border-warm-150 bg-white/90 backdrop-blur-sm px-4 py-2.5 text-[11px] leading-relaxed text-warm-500 shadow-card">
      拖动画布平移 · 滚轮缩放 · 点击节点选择 · 再点目标节点连线<br/>
      当前连线源：<span className="font-semibold text-primary-500">{from||'无'}</span>
     </div>
    </section>

    {/* Right Sidebar — Properties */}
    <aside className="w-[288px] flex-shrink-0 border-l border-warm-150 bg-white flex flex-col">
     <div className="border-b border-warm-150 px-5 py-4">
      <div className="text-sm font-semibold text-warm-800">属性面板</div>
      <div className="mt-0.5 text-[11px] text-warm-400">Selection Inspector</div>
     </div>
     <div className="flex-1 overflow-y-auto px-5 py-4">
      {sn?<div className="space-y-5">
       <div className="space-y-1.5">
        <label className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">名称</label>
        <input className="input-field text-sm" value={sn.title} onChange={e=>patch(sn.id,{title:e.target.value})}/>
       </div>
       <div className="space-y-1.5">
        <label className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">描述</label>
        <textarea className="input-field min-h-[76px] resize-none text-sm" value={sn.desc} onChange={e=>patch(sn.id,{desc:e.target.value})}/>
       </div>
       <div className="space-y-1.5">
        <label className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">颜色</label>
        <div className="flex items-center gap-2">
          <input className="h-9 w-full rounded-lg border border-warm-150 px-2" type="color" value={sn.color} onChange={e=>patch(sn.id,{color:e.target.value})}/>
          <span className="text-[10px] text-warm-400">{sn.color}</span>
        </div>
       </div>
       {embedded&&sn.type==='agent'&&<>
        <div className="space-y-1.5">
         <label className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">Agent 绑定</label>
         <select className="input-field text-sm" value={sn.agent||''} onChange={e=>patch(sn.id,{agent:e.target.value})}>{agentOpts.map(a=><option key={a.agentId} value={a.agentId}>{a.agentId}</option>)}</select>
        </div>
        <div className="space-y-1.5">
         <label className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">层级</label>
         <select className="input-field text-sm" value={sn.layer||'domain'} onChange={e=>patch(sn.id,{layer:e.target.value})}><option value="meta">Layer 1 · Meta</option><option value="domain">Layer 2 · Domain</option><option value="micro">Layer 3 · Micro</option></select>
        </div>
       </>}
       {embedded&&<div className="space-y-1.5">
        <label className="text-[11px] font-medium text-warm-400 uppercase tracking-wide">依赖节点</label>
        <div className="rounded-lg bg-warm-50 px-3 py-2 text-[11px] text-warm-500">{(depMap.get(sn.id)||[]).join(', ')||'无（通过连线自动推导）'}</div>
       </div>}
       <div className="grid grid-cols-2 gap-2">
        <Btn onClick={dup}>复制节点</Btn>
        <Btn onClick={()=>{setFrom(sn.id);fromRef.current=sn.id;setMsg('连线模式：请选择目标节点')}}>连线模式</Btn>
        <Btn onClick={()=>patch(sn.id,{z:Math.max(...doc.nodes.map(n=>n.z))+1})}>置顶</Btn>
        <Btn onClick={()=>patch(sn.id,{z:Math.min(...doc.nodes.map(n=>n.z))-1})}>置底</Btn>
       </div>
       <div className="rounded-xl border border-warm-150 bg-warm-50/50 px-4 py-3 text-[11px] leading-relaxed text-warm-500">
        <div className="flex justify-between"><span>ID</span><span className="font-mono text-warm-600">{sn.id}</span></div>
        <div className="mt-1 flex justify-between"><span>位置</span><span className="font-mono text-warm-600">{Math.round(sn.x)}, {Math.round(sn.y)}</span></div>
        <div className="mt-1 flex justify-between"><span>大小</span><span className="font-mono text-warm-600">{Math.round(sn.w)} × {Math.round(sn.h)}</span></div>
       </div>
       <button className="btn-ghost w-full text-sm text-danger-500" onClick={del}>删除节点</button>
      </div>:<div className="rounded-xl border border-dashed border-warm-200 bg-warm-50/50 px-4 py-8 text-center text-[12px] text-warm-400">选择画布上的节点<br/>以编辑其属性</div>}
     </div>
     {/* Layer list */}
     <div className="border-t border-warm-150 px-5 py-4">
      <div className="mb-3 text-[11px] font-medium text-warm-400 uppercase tracking-wide">图层管理</div>
      <div className="max-h-48 space-y-1 overflow-y-auto">
       {[...sorted].reverse().map(n=><button key={n.id} className={`w-full rounded-lg px-3 py-2 text-left text-xs transition-colors ${sel===n.id?'bg-primary-50 text-primary-700 font-medium':'text-warm-600 hover:bg-warm-50'}`} onClick={()=>setSel(n.id)}>
        <span className="mr-2 inline-block h-2 w-2 rounded-full" style={{background:n.color}}/>{n.title}
       </button>)}
      </div>
     </div>
    </aside>
   </div>
  </main>
 </div>;
}
