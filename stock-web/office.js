'use strict';
// AI 회의실. 역할별 AI 호출 기록(office_runs)을 2D 사무실 장면으로 보여 준다.
// - 실제 진행 중인 단계만 '작업 중'으로 그린다. 완료한 담당자는 회의 테이블로 이동해
//   자기 보고서의 실제 문장을 말풍선으로 말한다. 기록에 없는 내용을 지어내지 않는다.
// - 기록이 없을 때는 대기 동작(숨쉬기·눈 깜빡임)만 한다. 작업처럼 보이게 하지 않는다.
// - '지난 회의 다시 보기'는 저장된 마지막 기록을 순서대로 재생하며 화면에 '다시 보기'로 표시한다.
// - 동작 줄이기 설정이면 이동·입자 효과 없이 최종 위치만 그린다.
const officeRoles=[
  {id:'collect',label:'자료 수집원',short:'자료',color:'#69c9ff',hair:'#2b2b3a',skin:'#f1c7a1',item:'papers',work:'기사를 모으는 중'},
  {id:'technical',label:'차트 분석가',short:'차트',color:'#55e4bf',hair:'#e6c07b',skin:'#f6d3b3',item:'glasses',work:'일봉과 추세를 보는 중'},
  {id:'flow',label:'수급·심리 분석가',short:'수급',color:'#f3be72',hair:'#3a2a20',skin:'#d9a47c',item:'headset',work:'외국인·기관 수급 확인 중'},
  {id:'news',label:'뉴스·거시 분석가',short:'뉴스',color:'#b5a1ff',hair:'#1f2433',skin:'#f1c7a1',item:'paper',work:'뉴스와 경제 일정 읽는 중'},
  {id:'company',label:'기업·공시 분석가',short:'기업',color:'#f29bbb',hair:'#7a4b2a',skin:'#e8b894',item:'tie',work:'기업 소식 검토 중'},
  {id:'research',label:'찬반 리서치',short:'찬반',color:'#8ed6b1',hair:'#c9cfd8',skin:'#f6d3b3',item:'marker',work:'찬성·반대 근거 정리 중'},
  {id:'trader',label:'매매 전략가',short:'전략',color:'#8baeff',hair:'#2b2b3a',skin:'#d9a47c',item:'tablet',work:'기간별 매매 계획 작성 중'},
  {id:'risk',label:'위험 관리자',short:'위험',color:'#e6b47f',hair:'#5a3b2a',skin:'#f1c7a1',item:'shield',work:'계획의 위험을 검토 중'}];
const officeOrder=officeRoles.map(r=>r.id);
const officeStateNames={running:'작업 중',done:'완료',failed:'실패',cancelled:'중단',interrupted:'중단 추정',waiting:'대기'};
const officeSymbolNames={MARKET:'시장 전체'};
const W=1000,H=600;
const DESKS={collect:{x:120,y:236},technical:{x:316,y:214},news:{x:684,y:214},company:{x:880,y:236},
  flow:{x:120,y:452},research:{x:880,y:452},trader:{x:330,y:560},risk:{x:670,y:560}};
const TABLE={x:500,y:372,rx:150,ry:60};
const SEATS=officeRoles.map((r,i)=>{const a=(-90+i*45)*Math.PI/180;const s=Math.sin(a);return {x:TABLE.x+Math.cos(a)*(TABLE.rx+34),y:TABLE.y+s*(TABLE.ry+(s>0.1?60:26))};});
const STAGE_SPOT={x:500,y:198};
let officeSelected='collect';

function officeText(value,limit){const text=String(value||'').replace(/\s+/g,' ').trim();return text.length>limit?text.slice(0,limit-1)+'…':text;}
function officeSymbol(s){return officeSymbolNames[s]||s;}
function officeLines(stage){
  // 말풍선에 쓸 실제 보고 문장들. 없으면 빈 배열.
  const out=stage?.output||{},lines=[];
  if(out.summary)lines.push(out.summary);
  for(const f of (out.findings||out.reasons||[]).slice(0,3)){if(f.positive)lines.push(officeSymbol(f.symbol)+' 찬성: '+f.positive);if(f.negative)lines.push(officeSymbol(f.symbol)+' 반대: '+f.negative);}
  for(const p of (out.picks||[]).slice(0,2))if(p.thesis)lines.push((p.name||p.symbol)+': '+p.thesis);
  if(out.approved_buys)lines.push('매수 승인 '+(out.approved_buys.length?out.approved_buys.join(', '):'없음'));
  return lines.filter(Boolean);
}

class OfficeScene{
  constructor(canvas,opt={}){
    this.canvas=canvas;this.opt=opt;this.ctx=canvas.getContext('2d');this.run=null;this.account=null;
    this.replay=null;this.t=0;this.last=performance.now();this.particles=[];this.stamp=null;this.hover=null;
    this.talk={role:null,line:'',until:0,index:0};
    this.people=Object.fromEntries(officeRoles.map((r,i)=>{const d=DESKS[r.id];return [r.id,{role:r,x:d.x,y:d.y-14,tx:d.x,ty:d.y-14,seat:SEATS[i],
      pose:'desk',status:'waiting',blink:Math.random()*4,phase:Math.random()*6,walking:false,face:1,prevStatus:'waiting'}];}));
    canvas.addEventListener('mousemove',e=>{const p=this.pick(e);this.hover=p;canvas.style.cursor=p?'pointer':'default';});
    canvas.addEventListener('mouseleave',()=>{this.hover=null;});
    canvas.addEventListener('click',e=>{const p=this.pick(e);if(p&&this.opt.onSelect)this.opt.onSelect(p);});
    this.loop=this.loop.bind(this);requestAnimationFrame(this.loop);
  }
  reduce(){return window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;}
  pick(e){const r=this.canvas.getBoundingClientRect(),sx=W/r.width,x=(e.clientX-r.left)*sx,y=(e.clientY-r.top)*sx;
    let best=null,bd=40;for(const p of Object.values(this.people)){const d=Math.hypot(p.x-x,(p.y-26)-y);if(d<bd){bd=d;best=p.role.id;}}return best;}
  update(account){this.account=account;this.run=account?.office||null;}
  startReplay(){
    const run=this.run;if(!run||!run.stages||!Object.keys(run.stages).length)return false;
    this.replay={start:performance.now(),run,order:[...officeOrder,'office'].filter(id=>run.stages[id])};return true;
  }
  view(){
    // 지금 그릴 상태. 다시 보기면 기록을 시간에 따라 펼친다.
    if(this.replay){
      const elapsed=(performance.now()-this.replay.start)/1000,each=3.4,order=this.replay.order,stages={};
      order.forEach((id,i)=>{const st=this.replay.run.stages[id];const s=i*each,e=s+each;
        if(elapsed>=e)stages[id]=st;else if(elapsed>=s)stages[id]=st.status==='failed'?st:{...st,status:'running',output:undefined};});
      const doneAt=order.length*each;
      if(elapsed>doneAt+9){this.replay=null;if(this.opt.onReplayEnd)this.opt.onReplayEnd();return this.view();}
      return {stages,status:elapsed>=doneAt?this.replay.run.status:'running',replay:true,run:this.replay.run};
    }
    const run=this.run;if(!run)return {stages:{},status:'idle',run:null};
    return {stages:run.stages||{},status:run.status,run};
  }
  loop(now){
    requestAnimationFrame(this.loop);
    const visible=this.canvas.offsetParent!==null&&document.visibilityState==='visible';
    const dt=Math.min(.05,(now-this.last)/1000);this.last=now;if(!visible)return;this.t+=dt;
    this.resize();this.step(dt);this.draw();
  }
  resize(){
    const dpr=Math.min(window.devicePixelRatio||1,2),w=this.canvas.clientWidth||W,h=w*H/W;
    if(this.canvas.width!==Math.round(w*dpr)||this.canvas.height!==Math.round(h*dpr)){this.canvas.width=Math.round(w*dpr);this.canvas.height=Math.round(h*dpr);this.canvas.style.height=h+'px';}
    this.scale=this.canvas.width/W;
  }
  step(dt){
    const v=this.view(),reduce=this.reduce(),active=v.status==='running',finished=v.status==='done';
    for(const [i,role] of officeRoles.entries()){
      const p=this.people[role.id],st=v.stages[role.id],status=!st?'waiting':st.status==='running'&&!active?'interrupted':st.status;
      if(status!==p.prevStatus){if(status==='done'&&!reduce)this.burst(p.x,p.y-40,role.color);if(role.id==='risk'&&status==='done'&&!reduce)this.stamp={t:0,ok:(st.output?.approved_buys||[]).length>0};p.prevStatus=status;}
      p.status=status;const d=DESKS[role.id];let tx=d.x,ty=d.y-14,pose='desk';
      if(status==='running'&&active){
        if(role.id==='research'){tx=STAGE_SPOT.x;ty=STAGE_SPOT.y;pose='present';}
        else if(role.id==='trader'||role.id==='risk'){tx=SEATS[i].x;ty=SEATS[i].y;pose='seat';}
        else if(role.id==='collect'){const swing=(Math.sin(this.t*.9)+1)/2;tx=40+swing*(d.x-40);ty=176+swing*(d.y-14-176);pose='carry';}
      }else if(status==='done'&&(active||finished)){tx=SEATS[i].x;ty=SEATS[i].y;pose='seat';}
      p.tx=tx;p.ty=ty;p.pose=pose;
      const dx=tx-p.x,dy=ty-p.y,dist=Math.hypot(dx,dy),speed=reduce?1e9:150*dt;
      if(dist>1){const k=Math.min(1,speed/dist);p.x+=dx*k;p.y+=dy*k;p.walking=!reduce&&dist>2;if(Math.abs(dx)>1)p.face=dx>0?1:-1;}else{p.walking=false;}
      p.blink-=dt;if(p.blink<-.12)p.blink=2.5+Math.random()*3.5;
      if(status==='running'&&active&&role.id==='collect'&&!reduce&&Math.random()<dt*2.2)this.paper(p);
    }
    // 회의 대화: 테이블에 앉은 담당자가 자기 보고 문장을 번갈아 말한다
    const seated=officeRoles.filter(r=>{const p=this.people[r.id];return p.pose==='seat'&&!p.walking&&p.status==='done'&&officeLines(v.stages[r.id]).length;});
    if(performance.now()>this.talk.until||!seated.some(r=>r.id===this.talk.role)){
      if(seated.length){const r=seated[this.talk.index++%seated.length],lines=officeLines(v.stages[r.id]);
        this.talk={...this.talk,role:r.id,line:lines[Math.floor(this.talk.index/seated.length)%lines.length],until:performance.now()+(reduce?6000:4200)};}
      else this.talk={...this.talk,role:null,line:''};
    }
    for(const q of this.particles){q.t+=dt;}
    this.particles=this.particles.filter(q=>q.t<q.life);
    if(this.stamp){this.stamp.t+=dt;if(this.stamp.t>3.2)this.stamp=null;}
  }
  paper(p){const targets=['technical','flow','news','company'].map(id=>DESKS[id]);const to=targets[Math.floor(Math.random()*targets.length)];
    this.particles.push({kind:'paper',x0:p.x,y0:p.y-40,x1:to.x,y1:to.y-40,t:0,life:1.4,spin:Math.random()*6});}
  burst(x,y,color){for(let i=0;i<14;i++){const a=Math.random()*Math.PI*2,s=40+Math.random()*60;this.particles.push({kind:'spark',x0:x,y0:y,vx:Math.cos(a)*s,vy:Math.sin(a)*s-30,t:0,life:.9+Math.random()*.4,color});}}

  // ── 그리기 ────────────────────────────────────────────────────────────
  draw(){
    const c=this.ctx,v=this.view();c.setTransform(this.scale,0,0,this.scale,0,0);c.clearRect(0,0,W,H);
    this.drawRoom(v);
    const items=[];
    for(const [id,d] of Object.entries(DESKS))items.push({y:d.y+8,draw:()=>this.drawDesk(id,d,this.people[id])});
    items.push({y:TABLE.y,draw:()=>this.drawTable(v)});
    for(const p of Object.values(this.people))items.push({y:p.y+(p.pose==='desk'&&!p.walking?0:1),draw:()=>this.drawPerson(p)});
    items.sort((a,b)=>a.y-b.y).forEach(i=>i.draw());
    this.drawParticles();this.drawLabels(v);this.drawStamp();this.drawBanner(v);
  }
  sky(){const h=Number(new Date().toLocaleString('en-US',{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}))%24;
    if(h>=6&&h<9)return ['#ffb38a','#7fb2e5',false];if(h>=9&&h<17)return ['#8fd0ff','#cfeaff',false];if(h>=17&&h<20)return ['#ff9a6b','#5a4c8f',false];return ['#0b1530','#1d2b55',true];}
  drawRoom(v){
    const c=this.ctx,[s1,s2,night]=this.sky();
    // 벽
    let g=c.createLinearGradient(0,0,0,126);g.addColorStop(0,'#16213a');g.addColorStop(1,'#1d2a45');c.fillStyle=g;c.fillRect(0,0,W,126);
    c.fillStyle='#0f1729';c.fillRect(0,122,W,6);
    // 창문 두 개 + 도시
    for(const wx of [36,770]){g=c.createLinearGradient(0,18,0,104);g.addColorStop(0,s1);g.addColorStop(1,s2);c.fillStyle=g;c.fillRect(wx,18,194,86);
      c.fillStyle=night?'#0a1022':'#4a6386';for(let i=0;i<9;i++){const bw=14+(i*7)%13,bh=18+(i*23)%44;c.fillRect(wx+i*22,104-bh,bw,bh);}
      if(night){for(let i=0;i<14;i++){const tw=.5+.5*Math.sin(this.t*2+i*1.7);c.fillStyle=`rgba(255,226,140,${.35+tw*.5})`;c.fillRect(wx+6+(i*29)%180,70+(i*13)%30,2,2);}}
      c.strokeStyle='#2b3b5c';c.lineWidth=4;c.strokeRect(wx,18,194,86);c.beginPath();c.moveTo(wx+97,18);c.lineTo(wx+97,104);c.stroke();}
    // 전광판
    c.fillStyle='#09101d';c.beginPath();c.roundRect?c.roundRect(300,14,400,96,10):c.rect(300,14,400,96);c.fill();
    c.strokeStyle=v.status==='running'?'#2dd4bf':'#2c3e5e';c.lineWidth=2.5;c.stroke();
    // 전광판 하단 뉴럴 파형
    c.save();c.beginPath();for(let i=0;i<=48;i++){const px=314+i*7.75,amp=v.status==='running'?5:1.8,spd=this.reduce()?0:this.t*3.2;const py=68+Math.sin(i*.42+spd)*amp*Math.sin(i/48*Math.PI);i?c.lineTo(px,py):c.moveTo(px,py);}c.strokeStyle=v.status==='running'?'rgba(45,212,191,.36)':'rgba(167,139,250,.18)';c.lineWidth=1.5;c.stroke();c.restore();
    c.fillStyle='#2dd4bf';c.font='700 15px "Segoe UI","Malgun Gothic",sans-serif';c.textAlign='center';c.textBaseline='middle';
    c.fillText(v.replay?'지난 회의 다시 보기':'AI 투자팀 회의실',500,32);
    c.font='12px "Segoe UI","Malgun Gothic",sans-serif';c.fillStyle='#d5e2f2';c.fillText(officeText(this.headline(v),44),500,52);
    officeOrder.forEach((id,i)=>{const st=v.stages[id]?.status,x=355+i*41.5,y=87;
      if(st==='running'){c.fillStyle='rgba(251,191,36,.22)';c.beginPath();c.arc(x,y,11.5,0,Math.PI*2);c.fill();}
      c.fillStyle=st==='done'?'#2dd4bf':st==='running'?'#fbbf24':st==='failed'?'#ff5c77':'#27344d';
      c.beginPath();c.arc(x,y,st==='running'&&!this.reduce()?7+Math.sin(this.t*6)*1.2:7,0,Math.PI*2);c.fill();c.fillStyle='#09101d';c.font='700 9px "Segoe UI","Malgun Gothic",sans-serif';c.fillText(officeRoles[i].short[0],x,y+.5);
      if(i<7){c.fillStyle=st==='done'?'rgba(45,212,191,.6)':'#27344d';c.fillRect(x+8,y-1,25,2);}});
    // 벽시계
    const now=new Date(),kst=new Date(now.toLocaleString('en-US',{timeZone:'Asia/Seoul'}));const cx=740,cy=60;
    c.fillStyle='#e9eef6';c.beginPath();c.arc(cx-4,cy,15,0,Math.PI*2);c.fill();c.strokeStyle='#2b3b5c';c.lineWidth=2;c.stroke();
    const hand=(a,l,w)=>{c.strokeStyle='#1a2236';c.lineWidth=w;c.beginPath();c.moveTo(cx-4,cy);c.lineTo(cx-4+Math.sin(a)*l,cy-Math.cos(a)*l);c.stroke();};
    hand((kst.getHours()%12+kst.getMinutes()/60)/12*Math.PI*2,8,2.4);hand(kst.getMinutes()/60*Math.PI*2,12,1.6);
    // 바닥
    g=c.createLinearGradient(0,128,0,H);g.addColorStop(0,'#172236');g.addColorStop(1,'#0e1524');c.fillStyle=g;c.fillRect(0,128,W,H-128);
    c.strokeStyle='rgba(148,163,184,.04)';c.lineWidth=1;for(let x=0;x<W;x+=50){c.beginPath();c.moveTo(x,128);c.lineTo(x,H);c.stroke();}for(let y=128;y<H;y+=50){c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();}
    // 창문 빛
    if(!night){for(const wx of [36,770]){g=c.createLinearGradient(wx,128,wx+120,420);g.addColorStop(0,'rgba(255,240,200,.10)');g.addColorStop(1,'rgba(255,240,200,0)');c.fillStyle=g;
      c.beginPath();c.moveTo(wx+20,128);c.lineTo(wx+180,128);c.lineTo(wx+260,420);c.lineTo(wx+80,420);c.closePath();c.fill();}}
    // 러그 + 뉴럴 데이터 배선 + 자료 선반
    c.fillStyle='rgba(45,212,191,.06)';c.beginPath();c.ellipse(TABLE.x,TABLE.y+6,TABLE.rx+76,TABLE.ry+54,0,0,Math.PI*2);c.fill();
    c.strokeStyle='rgba(45,212,191,.22)';c.setLineDash([6,6]);c.stroke();c.setLineDash([]);
    this.drawNeuralLinks(v);
    c.fillStyle='#2a3550';c.fillRect(16,140,52,74);c.fillStyle='#3a4a6b';for(let i=0;i<3;i++)c.fillRect(20,146+i*23,44,18);
    c.fillStyle='#8aa2c6';for(let i=0;i<3;i++)for(let j=0;j<4;j++)c.fillRect(24+j*10,150+i*23,6,10);
    // 화분
    for(const [px,py] of [[960,168],[960,590],[40,590]]){c.fillStyle='#6b4a34';c.fillRect(px-12,py-16,24,18);c.fillStyle='#3ea371';
      for(let k=0;k<5;k++){c.beginPath();c.ellipse(px+Math.cos(k*1.3+Math.sin(this.t*.8)*.05)*9,py-28-Math.sin(k*1.9)*8,7,12,k,0,Math.PI*2);c.fill();}}
  }
  drawNeuralLinks(v){
    const c=this.ctx,red=this.reduce();
    c.save();
    for(const role of officeRoles){
      const d=DESKS[role.id],st=v.stages[role.id]?.status;
      const sx=d.x,sy=d.y+6,ex=TABLE.x,ey=TABLE.y;
      const cx=(sx+ex)/2+(d.x<TABLE.x?36:-36),cy=(sy+ey)/2+(d.y<TABLE.y?-18:18);
      c.beginPath();c.moveTo(sx,sy);c.quadraticCurveTo(cx,cy,ex,ey);
      c.strokeStyle=st==='done'?'rgba(45,212,191,.34)':st==='running'?'rgba(251,191,36,.52)':st==='failed'?'rgba(255,92,119,.32)':'rgba(100,130,170,.14)';
      c.lineWidth=st==='running'?2.4:st==='done'?1.8:1.1;c.stroke();
      if(!red&&(st==='running'||(st==='done'&&v.status==='running'))){
        const u=((this.t*(st==='running'?0.85:0.42)+(officeOrder.indexOf(role.id)*0.17))%1);
        const mu=1-u;
        const px=mu*mu*sx+2*mu*u*cx+u*u*ex,py=mu*mu*sy+2*mu*u*cy+u*u*ey;
        c.fillStyle=st==='running'?'#fde68a':role.color;
        c.beginPath();c.arc(px,py,3.3,0,Math.PI*2);c.fill();
      }
    }
    c.restore();
  }
  headline(v){
    if(v.replay)return '기록된 실제 단계를 순서대로 재생합니다';
    if(!this.account)return '계좌를 만들면 판단 과정을 보여 드립니다';
    const s=this.account;if(s.config?.strategy!=='portfolio')return '기간별 포트폴리오 전략에서 동작합니다';
    if(s.config?.ai_office===false)return 'AI 회의실 꺼짐 · 단일 판단 사용 중';
    if(!v.run)return window.officeMeetingReason||'다음 예약 판단(장전·정오·오후)을 기다리는 중';
    if(v.status==='running'){const cur=officeRoles.find(r=>v.stages[r.id]?.status==='running');return cur?`${cur.label}: ${cur.work}`:'회의 진행 중';}
    if(v.status==='done')return '회의 완료 · '+officeText(v.stages.office?.output?.summary||v.run.application||'',36);
    return (officeStateNames[v.status]||v.status)+' · '+(v.run.application||'');
  }
  drawDesk(id,d,p){
    const c=this.ctx,working=p.status==='running'&&p.pose==='desk'&&this.view().status==='running',role=p.role;
    c.fillStyle='rgba(0,0,0,.25)';c.beginPath();c.ellipse(d.x,d.y+30,70,10,0,0,Math.PI*2);c.fill();
    // 의자
    c.fillStyle='#2b3550';c.beginPath();c.roundRect?c.roundRect(d.x-16,d.y-38,32,26,6):c.rect(d.x-16,d.y-38,32,26);c.fill();
    // 책상
    c.fillStyle='#8a6a4c';c.fillRect(d.x-62,d.y-6,124,12);c.fillStyle='#6f5239';c.fillRect(d.x-62,d.y+6,124,6);
    c.fillStyle='#5a4230';c.fillRect(d.x-58,d.y+12,6,20);c.fillRect(d.x+52,d.y+12,6,20);
    // 모니터
    const mx=d.x+(id==='company'||id==='research'||id==='news'||id==='risk'?-34:34),my=d.y-30;
    if(working){const g=c.createRadialGradient(mx,my,4,mx,my,60);g.addColorStop(0,role.color+'55');g.addColorStop(1,role.color+'00');c.fillStyle=g;c.beginPath();c.arc(mx,my,60,0,Math.PI*2);c.fill();}
    c.fillStyle='#202a3d';c.fillRect(mx-3,my+14,6,10);c.fillRect(mx-12,my+22,24,3);
    c.fillStyle='#0b1220';c.fillRect(mx-24,my-14,48,30);c.strokeStyle='#33415c';c.lineWidth=2;c.strokeRect(mx-24,my-14,48,30);
    c.save();c.beginPath();c.rect(mx-22,my-12,44,26);c.clip();
    if(working){this.screen(id,mx,my,role.color);}else{c.fillStyle=p.status==='done'?'#12392f':'#111a2a';c.fillRect(mx-22,my-12,44,26);
      c.fillStyle=p.status==='done'?'#56e2c1':'#2c3a55';c.font='700 9px "Segoe UI","Malgun Gothic",sans-serif';c.textAlign='center';c.fillText(p.status==='done'?'완료':role.short,mx,my+1);}
    c.restore();
    // 소품
    c.fillStyle='#e9eef6';c.fillRect(d.x-46,d.y-12,16,6);c.fillStyle='#c94f4f';c.beginPath();c.arc(d.x+(mx>d.x?-16:16),d.y-10,4,0,Math.PI*2);c.fill();
  }
  screen(id,x,y,color){
    const c=this.ctx,t=this.t;c.fillStyle='#07101d';c.fillRect(x-22,y-12,44,26);c.strokeStyle=color;c.lineWidth=1.3;
    if(id==='technical'){for(let i=0;i<7;i++){const h=4+Math.abs(Math.sin(i*1.7+t*2))*10,cx=x-19+i*6;c.fillStyle=i%2?'#fb8190':'#76adff';c.fillRect(cx,y+2-h/2,3,h);}}
    else if(id==='flow'){for(let i=0;i<6;i++){const h=3+((Math.sin(t*3+i)+1)/2)*16;c.fillStyle=color;c.fillRect(x-19+i*7,y+12-h,4,h);}}
    else{c.beginPath();for(let i=0;i<=22;i++){const px=x-22+i*2,py=y+Math.sin(i*.6+t*4)*5*(id==='risk'?.5:1);i?c.lineTo(px,py):c.moveTo(px,py);}c.stroke();
      c.fillStyle=color;for(let i=0;i<3;i++)c.fillRect(x-19,y-9+i*4+(t*12%4),16+((i*7+Math.floor(t*3))%14),1.5);}
  }
  drawTable(v){
    const c=this.ctx;c.fillStyle='rgba(0,0,0,.3)';c.beginPath();c.ellipse(TABLE.x,TABLE.y+18,TABLE.rx+6,TABLE.ry+8,0,0,Math.PI*2);c.fill();
    c.fillStyle='#5b4632';c.beginPath();c.ellipse(TABLE.x,TABLE.y+8,TABLE.rx,TABLE.ry,0,0,Math.PI*2);c.fill();
    const g=c.createLinearGradient(0,TABLE.y-TABLE.ry,0,TABLE.y+TABLE.ry);g.addColorStop(0,'#a07c58');g.addColorStop(1,'#7d5e41');
    c.fillStyle=g;c.beginPath();c.ellipse(TABLE.x,TABLE.y,TABLE.rx,TABLE.ry,0,0,Math.PI*2);c.fill();
    c.strokeStyle='rgba(255,255,255,.12)';c.lineWidth=2;c.beginPath();c.ellipse(TABLE.x,TABLE.y,TABLE.rx-12,TABLE.ry-8,0,0,Math.PI*2);c.stroke();
    // 테이블 중앙 홀로그램 AI 코어 링
    c.save();const spin=this.reduce()?0:this.t*(v.status==='running'?1.8:0.35);
    c.strokeStyle=v.status==='running'?'rgba(45,212,191,.55)':'rgba(167,139,250,.32)';c.lineWidth=1.8;
    c.beginPath();c.ellipse(TABLE.x,TABLE.y,44,18,0,spin,spin+Math.PI*1.35);c.stroke();
    c.strokeStyle='rgba(45,212,191,.28)';c.lineWidth=1.2;
    c.beginPath();c.ellipse(TABLE.x,TABLE.y,26,10.5,0,-spin*1.4,-spin*1.4+Math.PI*1.2);c.stroke();
    c.fillStyle=v.status==='running'?'rgba(45,212,191,.35)':'rgba(167,139,250,.22)';c.beginPath();c.arc(TABLE.x,TABLE.y,5,0,Math.PI*2);c.fill();c.restore();
    // 테이블 위 서류: 완료한 보고서 수만큼
    const done=officeOrder.filter(id=>v.stages[id]?.status==='done').length;
    for(let i=0;i<done;i++){const a=(-90+i*45)*Math.PI/180;const px=TABLE.x+Math.cos(a)*(TABLE.rx-46),py=TABLE.y+Math.sin(a)*(TABLE.ry-22);
      c.save();c.translate(px,py);c.rotate(a+Math.PI/2);c.fillStyle='#eef3fa';c.fillRect(-9,-7,18,14);c.fillStyle=officeRoles[i].color;c.fillRect(-9,-7,18,3);c.restore();}
    if(v.status==='done'||(v.replay&&v.status!=='running')){c.fillStyle='#56e2c1';c.font='700 13px "Segoe UI","Malgun Gothic",sans-serif';c.textAlign='center';c.fillText('회의 결론',TABLE.x,TABLE.y-26);
      const out=v.stages.office?.output||{};c.fillStyle='#f4f7fb';c.font='12px "Segoe UI","Malgun Gothic",sans-serif';
      c.fillText(officeText('매수 승인 '+((out.approved_buys||[]).length)+'건 · 매도 승인 '+((out.approved_exits||[]).length)+'건',30),TABLE.x,TABLE.y-7);}
  }
  drawPerson(p){
    const c=this.ctx,r=p.role,t=this.t+p.phase,reduce=this.reduce();
    const sitting=p.pose==='seat'&&!p.walking,atDesk=p.pose==='desk'&&!p.walking;
    const typing=p.status==='running'&&atDesk&&this.view().status==='running';
    const bob=reduce?0:p.walking?Math.abs(Math.sin(t*10))*2.5:Math.sin(t*1.6)*.8;
    const x=p.x,y=p.y-bob,legSwing=p.walking&&!reduce?Math.sin(t*10)*5:0;
    if(this.hover===r.id||officeSelected===r.id&&this.opt.highlight){c.strokeStyle=r.color;c.lineWidth=2;c.globalAlpha=.6+.3*Math.sin(this.t*4);c.beginPath();c.ellipse(x,p.y+2,24,8,0,0,Math.PI*2);c.stroke();c.globalAlpha=1;}
    c.fillStyle='rgba(0,0,0,.28)';c.beginPath();c.ellipse(x,p.y+2,15,5,0,0,Math.PI*2);c.fill();
    if(sitting){c.fillStyle='#2b3550';c.beginPath();c.roundRect?c.roundRect(x-15,y-30,30,30,6):c.rect(x-15,y-30,30,30);c.fill();}
    // 다리
    c.fillStyle='#2a3248';
    if(!sitting){c.fillRect(x-7+legSwing*.4,y-14,6,14);c.fillRect(x+1-legSwing*.4,y-14,6,14);c.fillStyle='#151b2a';c.fillRect(x-8+legSwing*.4,y-2,8,3);c.fillRect(x+1-legSwing*.4,y-2,8,3);}
    else{c.fillRect(x-8,y-12,16,6);}
    // 몸
    const by=y-(sitting?36:38);
    c.fillStyle=r.color;c.beginPath();c.roundRect?c.roundRect(x-12,by,24,24,8):c.rect(x-12,by,24,24);c.fill();
    c.fillStyle='rgba(0,0,0,.15)';c.fillRect(x-12,by+16,24,8);
    // 팔
    const armA=typing&&!reduce?Math.sin(t*18)*2:0;c.fillStyle=r.color;
    if(p.pose==='present'&&!p.walking){c.save();c.translate(x+11,by+6);c.rotate(-1.9+Math.sin(t*3)*.25);c.fillRect(0,-3,18,6);c.restore();c.fillRect(x-16,by+4,5,15);}
    else{c.fillRect(x-16,by+4+armA,5,15);c.fillRect(x+11,by+4-armA,5,15);}
    c.fillStyle=r.skin;c.beginPath();c.arc(x-13.5,by+20+armA,3,0,Math.PI*2);c.arc(x+13.5,by+20-armA,3,0,Math.PI*2);c.fill();
    // 소품
    this.item(r,x,by,t,p);
    // 머리
    const hy=by-12;c.fillStyle=r.skin;c.beginPath();c.arc(x,hy,12,0,Math.PI*2);c.fill();
    c.fillStyle=r.hair;c.beginPath();c.arc(x,hy-2,12.5,Math.PI*1.05,Math.PI*1.95);c.lineTo(x+12,hy-1);c.lineTo(x-12,hy-1);c.closePath();c.fill();
    if(r.id==='research'){c.beginPath();c.arc(x-11,hy+1,4,0,Math.PI*2);c.arc(x+11,hy+1,4,0,Math.PI*2);c.fill();}
    if(r.id==='company'||r.id==='news'){c.beginPath();c.ellipse(x+p.face*6,hy-10,7,4,0,0,Math.PI*2);c.fill();}
    // 눈·입
    const blink=p.blink<0;c.fillStyle='#1b2030';
    if(blink){c.fillRect(x-6,hy+1,4,1.5);c.fillRect(x+2,hy+1,4,1.5);}else{c.beginPath();c.arc(x-4+p.face,hy+1,1.8,0,Math.PI*2);c.arc(x+4+p.face,hy+1,1.8,0,Math.PI*2);c.fill();}
    const talking=this.talk.role===r.id&&!reduce;c.strokeStyle='#7a3b3b';c.lineWidth=1.4;c.beginPath();
    if(talking&&Math.sin(this.t*14)>0){c.fillStyle='#7a3b3b';c.ellipse(x+p.face,hy+6,2.6,2,0,0,Math.PI*2);c.fill();}else{c.arc(x+p.face,hy+4,3,.2*Math.PI,.8*Math.PI);c.stroke();}
    if(r.item==='glasses'){c.strokeStyle='#1b2030';c.lineWidth=1.2;c.strokeRect(x-8+p.face,hy-2,6,5);c.strokeRect(x+2+p.face,hy-2,6,5);}
    if(r.item==='headset'){c.strokeStyle='#1b2030';c.lineWidth=2;c.beginPath();c.arc(x,hy-2,13,Math.PI*1.1,Math.PI*1.9);c.stroke();c.fillStyle='#1b2030';c.fillRect(x-14,hy-2,4,7);c.fillRect(x+10,hy-2,4,7);}
    // 상태 표시
    const iy=hy-24;
    if(p.status==='running'&&this.view().status==='running'){c.strokeStyle='#f3c57c';c.lineWidth=2.4;c.beginPath();const a=this.t*5;c.arc(x,iy,6,a,a+Math.PI*1.4);c.stroke();}
    else if(p.status==='done'){c.fillStyle='#56e2c1';c.beginPath();c.arc(x,iy,6,0,Math.PI*2);c.fill();c.strokeStyle='#0a2a22';c.lineWidth=2;c.beginPath();c.moveTo(x-3,iy);c.lineTo(x-1,iy+2.5);c.lineTo(x+3.5,iy-2.5);c.stroke();}
    else if(p.status==='failed'||p.status==='interrupted'){c.fillStyle='#fb8190';c.beginPath();c.arc(x,iy,6.5,0,Math.PI*2);c.fill();c.fillStyle='#2a0d12';c.font='700 10px sans-serif';c.textAlign='center';c.textBaseline='middle';c.fillText('!',x,iy+.5);}
    p.headY=iy;
  }
  item(r,x,by,t,p){
    const c=this.ctx;
    if(r.item==='papers'&&p.pose==='carry'){c.fillStyle='#eef3fa';for(let i=0;i<3;i++)c.fillRect(x-10+i,by+6-i*3,20,4);}
    if(r.item==='paper'){c.fillStyle='#eef3fa';c.fillRect(x-9,by+8,18,12);c.fillStyle='#9aabc3';for(let i=0;i<3;i++)c.fillRect(x-7,by+10+i*3,14,1);}
    if(r.item==='tie'){c.fillStyle='#3b2350';c.beginPath();c.moveTo(x,by+2);c.lineTo(x-3,by+8);c.lineTo(x,by+18);c.lineTo(x+3,by+8);c.closePath();c.fill();}
    if(r.item==='marker'&&p.pose==='present'){c.fillStyle='#fb8190';c.fillRect(x+24,by-22,3,9);}
    if(r.item==='tablet'){c.fillStyle='#1b2236';c.fillRect(x-8,by+7,16,12);c.fillStyle='#8baeff';c.fillRect(x-6,by+9,12,8);}
    if(r.item==='shield'){c.fillStyle='#f4f7fb';c.beginPath();c.moveTo(x,by+4);c.lineTo(x+7,by+7);c.lineTo(x+6,by+15);c.lineTo(x,by+19);c.lineTo(x-6,by+15);c.lineTo(x-7,by+7);c.closePath();c.fill();c.fillStyle='#e6b47f';c.fillRect(x-1,by+8,2,8);}
  }
  drawParticles(){
    const c=this.ctx;
    for(const q of this.particles){const k=q.t/q.life;
      if(q.kind==='paper'){const x=q.x0+(q.x1-q.x0)*k,y=q.y0+(q.y1-q.y0)*k-Math.sin(k*Math.PI)*70;c.save();c.translate(x,y);c.rotate(q.spin+k*6);c.globalAlpha=1-Math.max(0,k-.8)*5;c.fillStyle='#eef3fa';c.fillRect(-6,-4,12,8);c.fillStyle='#69c9ff';c.fillRect(-6,-4,12,2);c.restore();}
      else{const x=q.x0+q.vx*q.t,y=q.y0+q.vy*q.t+60*q.t*q.t;c.globalAlpha=1-k;c.fillStyle=q.color;c.beginPath();c.moveTo(x,y-4);c.lineTo(x+1.5,y-1.5);c.lineTo(x+4,y);c.lineTo(x+1.5,y+1.5);c.lineTo(x,y+4);c.lineTo(x-1.5,y+1.5);c.lineTo(x-4,y);c.lineTo(x-1.5,y-1.5);c.closePath();c.fill();}
      c.globalAlpha=1;}
  }
  bubble(x,y,text,color,work){
    const c=this.ctx;c.font='12px "Segoe UI","Malgun Gothic",sans-serif';
    const words=officeText(text,64),lines=[];let cur='';
    for(const ch of words){if(c.measureText(cur+ch).width>190){lines.push(cur);cur=ch;if(lines.length===2)break;}else cur+=ch;}
    if(lines.length<2&&cur)lines.push(cur);else if(cur&&lines.length===2)lines[1]=lines[1].slice(0,-1)+'…';
    const w=Math.max(...lines.map(l=>c.measureText(l).width))+20,h=lines.length*16+12;
    let bx=Math.max(8,Math.min(W-w-8,x-w/2)),by=y-h-12;
    c.fillStyle=work?'rgba(18,28,46,.94)':'rgba(248,251,255,.97)';c.strokeStyle=color;c.lineWidth=1.5;
    c.beginPath();c.roundRect?c.roundRect(bx,by,w,h,9):c.rect(bx,by,w,h);c.fill();c.stroke();
    c.beginPath();c.moveTo(x-6,by+h);c.lineTo(x,by+h+8);c.lineTo(x+6,by+h);c.closePath();c.fill();
    c.fillStyle=work?'#e2ebf8':'#152033';c.textAlign='left';c.textBaseline='middle';lines.forEach((l,i)=>c.fillText(l,bx+10,by+14+i*16));
  }
  drawLabels(v){
    const c=this.ctx;
    for(const p of Object.values(this.people)){
      const y=(p.headY||p.y-70)-14;c.font='700 11px "Segoe UI","Malgun Gothic",sans-serif';const w=c.measureText(p.role.label).width+14;
      if(!this.opt.compact||this.hover===p.role.id){c.fillStyle='rgba(10,16,28,.82)';c.beginPath();c.roundRect?c.roundRect(p.x-w/2,y-9,w,18,9):c.rect(p.x-w/2,y-9,w,18);c.fill();
        c.fillStyle=p.role.color;c.textAlign='center';c.textBaseline='middle';c.fillText(p.role.label,p.x,y);}
    }
    // 작업 중 말풍선은 한 명만(가장 최근 단계), 회의 대화는 말하는 사람 한 명
    const running=v.status==='running'?officeRoles.filter(r=>this.people[r.id].status==='running'):[];
    for(const r of running.slice(-1)){const p=this.people[r.id];if(p.pose==='present')continue;const dots='.'.repeat(1+Math.floor(this.t*2.5)%3);this.bubble(p.x,(p.headY||p.y-70)-24,r.work+dots,r.color,true);}
    if(this.talk.role&&this.talk.line){const p=this.people[this.talk.role];this.bubble(p.x,(p.headY||p.y-70)-24,this.talk.line,p.role.color,false);}
  }
  drawStamp(){
    if(!this.stamp)return;const c=this.ctx,k=Math.min(1,this.stamp.t/.35),s=2.2-1.2*k,alpha=this.stamp.t>2.6?1-(this.stamp.t-2.6)/.6:1;
    c.save();c.globalAlpha=Math.max(0,alpha);c.translate(TABLE.x+(k<1?0:Math.sin(this.stamp.t*60)*Math.max(0,.5-this.stamp.t)*4),TABLE.y-6);c.rotate(-.18);c.scale(s,s);
    c.strokeStyle=this.stamp.ok?'#ff6b6b':'#8fa1b9';c.lineWidth=3;c.beginPath();c.roundRect?c.roundRect(-44,-18,88,36,6):c.rect(-44,-18,88,36);c.stroke();
    c.fillStyle=c.strokeStyle;c.font='800 20px "Segoe UI","Malgun Gothic",sans-serif';c.textAlign='center';c.textBaseline='middle';c.fillText(this.stamp.ok?'승인':'검토 완료',0,1);c.restore();
  }
  drawBanner(v){
    if(!v.replay)return;const c=this.ctx;c.fillStyle='rgba(243,197,124,.92)';c.beginPath();c.roundRect?c.roundRect(14,136,150,26,13):c.rect(14,136,150,26);c.fill();
    c.fillStyle='#2b1d05';c.font='700 12px "Segoe UI","Malgun Gothic",sans-serif';c.textAlign='center';c.textBaseline='middle';c.fillText('다시 보기 · 기록 재생',89,149);
  }
}

// ── 화면 연결 ──────────────────────────────────────────────────────────────
let officeScene=null,homeOfficeScene=null;
function officeSelect(id){officeSelected=id;renderOfficeReport(dbData?.account?.office);renderOfficeRoles(dbData?.account);}
function ensureOfficeScenes(){
  if(!officeScene&&$('office-canvas'))officeScene=new OfficeScene($('office-canvas'),{highlight:true,onSelect:officeSelect,onReplayEnd:()=>{$('office-replay').textContent='지난 회의 다시 보기';}});
  if(!homeOfficeScene&&$('home-office-canvas'))homeOfficeScene=new OfficeScene($('home-office-canvas'),{compact:true,onSelect:id=>{officeSelected=id;tab('office');}});
}
function renderOfficeRoles(s){
  const box=$('office-roles');if(!box)return;const stages=s?.office?.stages||{},running=s?.office?.status==='running';
  box.innerHTML=officeRoles.map(r=>{const st=stages[r.id]?.status,status=st==='running'&&!running?'interrupted':st||'waiting';
    return `<button role="listitem" class="office-role-chip ${status} ${officeSelected===r.id?'selected':''}" data-office-role="${r.id}" style="--agent-color:${r.color}"><i></i><b>${r.label}</b><small>${officeStateNames[status]||status}</small></button>`;}).join('');
}
function renderOfficeReport(run){
  if(!$('office-report'))return;
  const stage=run?.stages?.[officeSelected],out=stage?.output||{},role=officeRoles.find(r=>r.id===officeSelected);
  $('office-report-title').textContent=(role?.label||officeSelected)+(stage?' · '+(officeStateNames[stage.status]||stage.status):'');
  const usage=out.usage?`<p class="small muted">${esc(out.usage.provider==='codex'?'ChatGPT':out.usage.provider==='claude'?'Claude':out.usage.provider||'')} ${esc(out.usage.model||'')} · ${mdhm(stage.updated_at)}</p>`:stage?.updated_at?`<p class="small muted">${mdhm(stage.updated_at)}</p>`:'';
  $('office-report').innerHTML=`<p class="office-summary">${esc(out.summary||'이 담당자의 보고서가 아직 없습니다. 다음 예약 판단에서 실제로 실행되면 여기에 남습니다.')}</p>`+usage+
    (out.findings||out.reasons||[]).map(f=>`<div class="office-finding"><b>${esc(officeSymbol(f.symbol))}</b><p class="pro">찬성 · ${esc(f.positive)}</p><p class="con">반대 · ${esc(f.negative)}</p><small>근거 ${esc((f.evidence_ids||[]).length)}건 인용${(f.missing||[]).length?' · 부족 자료: '+esc(f.missing.join(' · ')):''}</small></div>`).join('')+
    (out.picks||[]).map(p=>`<div class="office-finding"><b>${esc(p.name||p.symbol)} · ${esc(horizonNames?.[p.horizon]||p.horizon)}</b><p>${esc(p.thesis)}</p>${p.counter_case?`<p class="con">반대 · ${esc(p.counter_case)}</p>`:''}</div>`).join('')+
    (out.approved_buys?`<p class="office-approval">매수 승인: <b>${esc(out.approved_buys.join(', ')||'없음')}</b> · 매도 승인: <b>${esc((out.approved_exits||[]).join(', ')||'없음')}</b></p>`:'')+
    ((out.missing||[]).length?`<p class="small muted">부족 자료 · ${esc(out.missing.join(' · '))}</p>`:'');
}
function renderOfficeCoverage(run){
  if(!$('office-coverage'))return;const collected=run?.stages?.collect?.output,c=collected?.coverage;
  $('office-coverage').innerHTML=c?`<div class="office-count">${c.articles}<small>중복 제거 기사</small></div><p>수집 요청 ${c.requests}개 · 확보 ${c.unique_received}건</p><p class="small">발표 시각 없음 ${c.excluded.undated} · 미래 시각 ${c.excluded.future} · 오래된 기사 ${c.excluded.old} · 중복 ${c.excluded.duplicate}</p><p class="small muted">${esc((c.missing||[]).join(' · '))}</p><details><summary>공급원별 상태</summary>${(c.sources||[]).map(r=>`<p class="small">${esc(r.source)} · ${r.received}건 · ${r.status==='available'?'확보':r.status==='failed'?'실패':'빈 응답 또는 확인 필요'}</p>`).join('')}</details>`:'<p class="muted">자료 수집이 끝나면 출처·누락·제외 건수를 표시합니다.</p>';
  $('office-evidence').innerHTML=(collected?.evidence||[]).map(e=>`<div class="feed-item"><strong>${external(e.url,e.title)}</strong><small>${esc(e.source)} · ${mdhm(e.published_at)} · ${e.kind==='official'?'공식 출처':'보도·검색'}</small></div>`).join('')||'<p class="muted">아직 수집된 기록이 없습니다.</p>';
}
function renderOffice(s){
  ensureOfficeScenes();
  officeScene?.update(s);homeOfficeScene?.update(s);
  const run=s?.office;const state=!s?'계좌가 없습니다':run?`${officeStateNames[run.status]||run.status} · ${run.application||''} · ${mdhm(run.updated_at)}`:s.config.strategy!=='portfolio'?'기간별 포트폴리오 전략에서 동작합니다.':s.config.ai_office===false?'AI 회의실 꺼짐 · 단일 판단 사용':(window.officeMeetingReason||'다음 예약 판단을 기다립니다.');
  if($('office-run-state'))$('office-run-state').textContent=state;
  if($('office-launch-state'))$('office-launch-state').textContent=state;
  if($('office-replay'))$('office-replay').disabled=!run||!Object.keys(run.stages||{}).length;
  renderOfficeRoles(s);renderOfficeReport(run);renderOfficeCoverage(run);
}
$('office-roles')?.addEventListener('click',e=>{const b=e.target.closest('[data-office-role]');if(b)officeSelect(b.dataset.officeRole);});
$('office-replay')?.addEventListener('click',()=>{ensureOfficeScenes();if(officeScene?.replay){officeScene.replay=null;$('office-replay').textContent='지난 회의 다시 보기';return;}
  if(officeScene?.startReplay())$('office-replay').textContent='다시 보기 멈추기';});
const officeBaseRender=renderDashboard;
renderDashboard=function(){officeBaseRender();renderOffice(dbData?.account);};
// 대시보드 안내 띠: 회의실 탭으로 이동
const officeBanner=document.createElement('div');officeBanner.className='office-launch';officeBanner.innerHTML='<div><b>AI 투자팀의 판단 과정을 확인하세요</b><small id="office-launch-state">다음 판단부터 실제 실행 상태를 기록합니다.</small></div><button id="office-open">AI 회의실 가기 →</button>';
terminalDb.insertBefore(officeBanner,terminalSwitch);
$('office-open').addEventListener('click',()=>tab('office'));
// 계좌 설정: AI 회의실 사용 여부 (저장되는 설정. 화면 조작만으로 판단을 시작하지 않는다)
$('portfolio-fields').insertAdjacentHTML('beforeend','<label class="check"><input id="portfolio-ai-office" type="checkbox" checked> AI 회의실: 네 분야 분석·찬반 리서치·매매·위험 검토</label><p class="small muted">예약 판단 1회당 최대 7개 역할 호출을 순차 실행합니다. 구독 사용량과 판단 시간이 늘어납니다. 끄면 기존 단일 판단을 사용합니다.</p>');
const officeForm=portfolioForm;portfolioForm=function(c){officeForm(c);if(c)$('portfolio-ai-office').checked=c.ai_office!==false;};
const officeConfig=dtConfig;dtConfig=function(){return {...officeConfig(),ai_office:$('portfolio-ai-office').checked};};
