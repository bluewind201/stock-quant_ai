'use strict';
// 외부 라이브러리 없는 차트. 선(영역)·막대(손익)·도넛(비중)·가로 막대(종목별)를 그리고
// 마우스를 올리면 값을 보여 준다. 처음 그릴 때만 짧게 그려지는 효과를 주고,
// '동작 줄이기' 설정이면 효과 없이 바로 그린다. 상승·이익은 빨강, 하락·손실은 파랑(국내 관례).
const SQCharts=(()=>{
  const reduce=()=>window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  const css=name=>getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const palette=()=>({up:css('--up')||'#fb8190',down:css('--down')||'#76adff',accent:css('--accent')||'#56e2c1',
    text:css('--text')||'#e2ebf8',muted:css('--muted')||'#9aabc3',line:css('--line')||'#253248',panel:css('--panel')||'#111a29'});
  const registry=new Map();let tip=null,resizeTimer=null;
  function tooltip(){if(!tip){tip=document.createElement('div');tip.className='sq-tip';tip.setAttribute('role','status');tip.hidden=true;document.body.append(tip);}return tip;}
  function showTip(html,x,y){const t=tooltip();t.innerHTML=html;t.hidden=false;const r=t.getBoundingClientRect();
    let left=x+14,top=y-r.height-10;if(left+r.width>innerWidth-8)left=x-r.width-14;if(top<8)top=y+16;t.style.left=left+'px';t.style.top=top+'px';}
  function hideTip(){if(tip)tip.hidden=true;}
  function setup(canvas){
    const dpr=Math.min(window.devicePixelRatio||1,2),rect=canvas.getBoundingClientRect();
    const w=Math.max(120,rect.width||canvas.parentElement?.clientWidth||300),h=Math.max(80,rect.height||Number(canvas.dataset.height)||220);
    canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
    const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
    ctx.font='11px "Segoe UI","Malgun Gothic",sans-serif';ctx.textBaseline='middle';
    return {ctx,w,h};
  }
  function animate(canvas,draw){
    const first=!canvas.dataset.sqDrawn;canvas.dataset.sqDrawn='1';
    if(!first||reduce())return draw(1);
    const start=performance.now();
    const step=now=>{const p=Math.min(1,(now-start)/650),e=1-Math.pow(1-p,3);draw(e);if(p<1)requestAnimationFrame(step);};
    requestAnimationFrame(step);
  }
  function remember(canvas,fn){registry.set(canvas,fn);}
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{
    for(const [canvas,fn] of registry){if(!canvas.isConnected){registry.delete(canvas);continue;}if(canvas.offsetParent)fn();}},150);});
  function roundRect(ctx,x,y,w,h,r){
    const rr=Math.max(0,Math.min(r,Math.abs(w)/2,Math.abs(h)/2));ctx.beginPath();
    if(ctx.roundRect){ctx.roundRect(x,y,w,h,rr);return;}
    ctx.moveTo(x+rr,y);ctx.arcTo(x+w,y,x+w,y+h,rr);ctx.arcTo(x+w,y+h,x,y+h,rr);ctx.arcTo(x,y+h,x,y,rr);ctx.arcTo(x,y,x+w,y,rr);ctx.closePath();
  }
  function empty(canvas,message){
    const {ctx,w,h}=setup(canvas),c=palette();ctx.fillStyle=c.muted;ctx.textAlign='center';ctx.font='13px "Segoe UI","Malgun Gothic",sans-serif';
    ctx.fillText(message||'아직 자료가 없습니다',w/2,h/2);canvas.onmousemove=null;canvas.onmouseleave=null;
  }
  function niceScale(min,max){if(min===max){const pad=Math.abs(min)*.01||1;return [min-pad,max+pad];}const pad=(max-min)*.08;return [min-pad,max+pad];}

  // 영역 선 차트: points=[{x:label/date, y:number}]
  function line(canvas,points,opt={}){
    if(!canvas)return;
    const draw=()=>{
      if(!points||points.length<2)return empty(canvas,opt.empty);
      const c=palette(),color=opt.color||c.accent,format=opt.format||(v=>Math.round(v).toLocaleString('ko-KR'));
      const pad={l:10,r:62,t:14,b:24};
      animate(canvas,progress=>{
        const {ctx,w,h}=setup(canvas),ys=points.map(p=>p.y);
        let [min,max]=niceScale(Math.min(...ys,opt.baseline??Infinity),Math.max(...ys,opt.baseline??-Infinity));
        const X=i=>pad.l+(w-pad.l-pad.r)*i/(points.length-1),Y=v=>pad.t+(h-pad.t-pad.b)*(1-(v-min)/(max-min));
        ctx.strokeStyle=c.line;ctx.lineWidth=1;ctx.fillStyle=c.muted;ctx.textAlign='left';
        for(let i=0;i<=3;i++){const v=min+(max-min)*i/3,y=Y(v);ctx.globalAlpha=.6;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();ctx.globalAlpha=1;ctx.fillText(format(v,true),w-pad.r+6,y);}
        if(opt.baseline!=null&&opt.baseline>min&&opt.baseline<max){ctx.setLineDash([4,4]);ctx.strokeStyle=c.muted;ctx.beginPath();ctx.moveTo(pad.l,Y(opt.baseline));ctx.lineTo(w-pad.r,Y(opt.baseline));ctx.stroke();ctx.setLineDash([]);}
        ctx.textAlign='center';const labels=[0,Math.floor((points.length-1)/2),points.length-1];
        for(const i of [...new Set(labels)]){ctx.fillText(points[i].label||'',Math.min(Math.max(X(i),pad.l+24),w-pad.r-24),h-10);}
        ctx.save();ctx.beginPath();ctx.rect(0,0,pad.l+(w-pad.l-pad.r)*progress+1,h);ctx.clip();
        const grad=ctx.createLinearGradient(0,pad.t,0,h-pad.b);grad.addColorStop(0,color+'55');grad.addColorStop(1,color+'00');
        ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(X(i),Y(p.y)):ctx.moveTo(X(i),Y(p.y)));
        ctx.lineTo(X(points.length-1),h-pad.b);ctx.lineTo(X(0),h-pad.b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
        ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(X(i),Y(p.y)):ctx.moveTo(X(i),Y(p.y)));ctx.strokeStyle=color;ctx.lineWidth=2;ctx.lineJoin='round';ctx.stroke();ctx.restore();
        if(progress>=1){const i=points.length-1;ctx.beginPath();ctx.arc(X(i),Y(points[i].y),4,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();ctx.lineWidth=2;ctx.strokeStyle=c.panel;ctx.stroke();}
        canvas._sqHit={X,Y,pad,w,h};
      });
      canvas.onmousemove=e=>{const hit=canvas._sqHit;if(!hit)return;const r=canvas.getBoundingClientRect(),x=e.clientX-r.left;
        const i=Math.max(0,Math.min(points.length-1,Math.round((x-hit.pad.l)/(hit.w-hit.pad.l-hit.pad.r)*(points.length-1))));
        const p=points[i];showTip(`<b>${esc(p.tip||p.label||'')}</b><br>${esc(opt.name||'값')} ${esc(format(p.y))}${p.extra?'<br>'+esc(p.extra):''}`,e.clientX,e.clientY);
      };
      canvas.onmouseleave=hideTip;
    };
    remember(canvas,draw);draw();
  }

  // 막대(0 기준 위·아래): rows=[{label, value, tip}]
  function bars(canvas,rows,opt={}){
    if(!canvas)return;
    const draw=()=>{
      if(!rows||!rows.length)return empty(canvas,opt.empty);
      const c=palette(),format=opt.format||(v=>Math.round(v).toLocaleString('ko-KR'));
      animate(canvas,progress=>{
        const {ctx,w,h}=setup(canvas),pad={l:10,r:58,t:12,b:24};
        const vals=rows.map(r=>r.value),maxAbs=Math.max(1,...vals.map(Math.abs));
        const min=Math.min(0,...vals)<0?-maxAbs:0,max=Math.max(0,...vals)>0?maxAbs:0||1;
        const Y=v=>pad.t+(h-pad.t-pad.b)*(1-(v-min)/((max-min)||1)),zero=Y(0);
        const slot=(w-pad.l-pad.r)/rows.length,bw=Math.max(3,Math.min(26,slot-2));
        ctx.strokeStyle=c.line;ctx.fillStyle=c.muted;ctx.textAlign='left';
        for(const v of [max,0,min].filter((v,i,a)=>a.indexOf(v)===i)){ctx.beginPath();ctx.moveTo(pad.l,Y(v));ctx.lineTo(w-pad.r,Y(v));ctx.stroke();ctx.fillText(format(v,true),w-pad.r+6,Y(v));}
        rows.forEach((r,i)=>{const x=pad.l+slot*i+(slot-bw)/2,y=Y(r.value*progress),top=Math.min(y,zero),height=Math.max(1,Math.abs(zero-y));
          ctx.fillStyle=r.value>=0?c.up:c.down;roundRect(ctx,x,top,bw,height,Math.min(4,bw/2));ctx.fill();});
        ctx.fillStyle=c.muted;ctx.textAlign='center';
        const step=Math.ceil(rows.length/6);rows.forEach((r,i)=>{if(i%step===0||i===rows.length-1)ctx.fillText(r.label,pad.l+slot*i+slot/2,h-10);});
        canvas._sqHit={pad,slot};
      });
      canvas.onmousemove=e=>{const hit=canvas._sqHit;if(!hit)return;const r=canvas.getBoundingClientRect(),i=Math.floor((e.clientX-r.left-hit.pad.l)/hit.slot);
        if(i<0||i>=rows.length)return hideTip();const row=rows[i];showTip(`<b>${esc(row.tip||row.label)}</b><br>${esc(opt.name||'값')} ${esc(format(row.value))}${row.extra?'<br>'+esc(row.extra):''}`,e.clientX,e.clientY);};
      canvas.onmouseleave=hideTip;
    };
    remember(canvas,draw);draw();
  }

  // 도넛: segments=[{label,value,color}]
  function donut(canvas,segments,opt={}){
    if(!canvas)return;
    const draw=()=>{
      const total=(segments||[]).reduce((s,x)=>s+Math.max(0,x.value),0);
      if(!total)return empty(canvas,opt.empty);
      const c=palette();
      animate(canvas,progress=>{
        const {ctx,w,h}=setup(canvas),cx=w/2,cy=h/2,R=Math.min(w,h)/2-8,r=R*.64;let a=-Math.PI/2;const arcs=[];
        for(const s of segments){const v=Math.max(0,s.value);if(!v)continue;const span=Math.PI*2*v/total*progress,gap=segments.length>1?0.025:0;
          ctx.beginPath();ctx.arc(cx,cy,R,a+gap/2,a+span-gap/2);ctx.arc(cx,cy,r,a+span-gap/2,a+gap/2,true);ctx.closePath();ctx.fillStyle=s.color;ctx.fill();
          arcs.push({start:a,end:a+span,s});a+=span;}
        ctx.textAlign='center';ctx.fillStyle=c.muted;ctx.font='11px "Segoe UI","Malgun Gothic",sans-serif';ctx.fillText(opt.title||'',cx,cy-11);
        ctx.fillStyle=c.text;ctx.font='700 16px "Segoe UI","Malgun Gothic",sans-serif';ctx.fillText(opt.value||'',cx,cy+9);
        canvas._sqHit={cx,cy,R,r,arcs};
      });
      canvas.onmousemove=e=>{const hit=canvas._sqHit;if(!hit)return;const b=canvas.getBoundingClientRect(),x=e.clientX-b.left-hit.cx,y=e.clientY-b.top-hit.cy,d=Math.hypot(x,y);
        if(d<hit.r||d>hit.R)return hideTip();let ang=Math.atan2(y,x);if(ang<-Math.PI/2)ang+=Math.PI*2;
        const seg=hit.arcs.find(s=>ang>=s.start&&ang<s.end);if(!seg)return hideTip();
        showTip(`<b>${esc(seg.s.label)}</b><br>${esc((opt.format||(v=>Math.round(v).toLocaleString('ko-KR')+'원'))(seg.s.value))} · ${(seg.s.value/total*100).toFixed(1)}%`,e.clientX,e.clientY);};
      canvas.onmouseleave=hideTip;
    };
    remember(canvas,draw);draw();
  }

  // 가로 막대(종목별 손익): rows=[{label, value, sub}]
  function hbars(canvas,rows,opt={}){
    if(!canvas)return;
    const draw=()=>{
      if(!rows||!rows.length)return empty(canvas,opt.empty);
      const c=palette(),format=opt.format||(v=>(v>0?'+':'')+v.toFixed(2)+'%');
      const rowH=30;canvas.style.height=Math.max(90,rows.length*rowH+16)+'px';
      animate(canvas,progress=>{
        const {ctx,w}=setup(canvas),labelW=Math.min(150,w*.34),valueW=76,maxAbs=Math.max(0.5,...rows.map(r=>Math.abs(r.value)));
        const span=w-labelW-valueW-12,zero=labelW+span/2;
        ctx.strokeStyle=c.line;ctx.beginPath();ctx.moveTo(zero,4);ctx.lineTo(zero,rows.length*rowH+8);ctx.stroke();
        rows.forEach((r,i)=>{const y=10+i*rowH,len=span/2*Math.abs(r.value)/maxAbs*progress;
          ctx.fillStyle=c.text;ctx.textAlign='left';ctx.font='12px "Segoe UI","Malgun Gothic",sans-serif';
          let label=r.label;while(ctx.measureText(label).width>labelW-8&&label.length>2)label=label.slice(0,-2)+'…';ctx.fillText(label,0,y+9);
          ctx.fillStyle=r.value>=0?c.up:c.down;roundRect(ctx,r.value>=0?zero:zero-len,y+2,Math.max(2,len),14,4);ctx.fill();
          ctx.fillStyle=c.muted;ctx.textAlign='right';ctx.fillText(format(r.value),w,y+9);});
        canvas._sqHit={rowH};
      });
      canvas.onmousemove=e=>{const b=canvas.getBoundingClientRect(),i=Math.floor((e.clientY-b.top-6)/rowH);const r=rows[i];
        if(!r)return hideTip();showTip(`<b>${esc(r.label)}</b><br>${esc(format(r.value))}${r.sub?'<br>'+esc(r.sub):''}`,e.clientX,e.clientY);};
      canvas.onmouseleave=hideTip;
    };
    remember(canvas,draw);draw();
  }

  // 작은 추세선 (KPI 카드용, 상호작용 없음)
  function spark(canvas,values,color){
    if(!canvas||!values||values.length<2)return;
    const draw=()=>{const {ctx,w,h}=setup(canvas),min=Math.min(...values),max=Math.max(...values),c=palette(),col=color||c.accent;
      const X=i=>2+(w-4)*i/(values.length-1),Y=v=>h-3-(h-6)*((v-min)/((max-min)||1));
      const grad=ctx.createLinearGradient(0,0,0,h);grad.addColorStop(0,col+'44');grad.addColorStop(1,col+'00');
      ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(X(i),Y(v)):ctx.moveTo(X(i),Y(v)));ctx.lineTo(X(values.length-1),h);ctx.lineTo(X(0),h);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
      ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(X(i),Y(v)):ctx.moveTo(X(i),Y(v)));ctx.strokeStyle=col;ctx.lineWidth=1.6;ctx.stroke();};
    remember(canvas,draw);draw();
  }
  return {line,bars,donut,hbars,spark,palette,hideTip};
})();
