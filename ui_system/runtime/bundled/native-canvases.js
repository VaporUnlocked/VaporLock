/* Trusted canvas adapters for native-painted controls. Raw pixels supplied by
 * a future reviewed adapter are not URLs and invoke no image/media decoder.
 * Theme archives and arbitrary drawing scripts are not accepted here.
 */
const clamp=(n,low,high)=>Math.min(high,Math.max(low,n));
const typedArrayByteLength=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype),"byteLength").get;
export function createNativeCanvas(document,{kind,translate=source=>source,onAnnotations=()=>{},onSelection=()=>{},onZoom=()=>{}}) {
  const surface=document.createElement("div"),canvas=document.createElement("canvas"),status=document.createElement("span");
  surface.className=`vl-native-canvas vl-native-canvas-${kind}`;surface.tabIndex=0;surface.setAttribute("role",kind==="DatasetReviewCanvas"?"group":"img");
  surface.append(canvas,status);status.className="vl-native-canvas-status";status.setAttribute("aria-live","polite");
  const context=canvas.getContext("2d",{alpha:false});
  let frame=null,annotations=[],selected=-1,pending=null,cursor=null,activeClass=0,zoom=1,pan={x:0,y:0},dragOrigin=null,panOrigin=null;
  let stickValues=[0,0,0,0],timings=[],disposed=false;
  const label=()=>translate(kind==="DatasetReviewCanvas"?"Choose an image to review":kind==="PerformanceChart"?"No timing samples":kind==="StickView"?"Controller input":"Start capture to receive the live annotated preview");
  function dimensions() {return {width:Math.max(1,surface.clientWidth),height:Math.max(1,surface.clientHeight)};}
  function fitRect() {
    if (!frame)return null;const {width,height}=dimensions(),scale=Math.min(width/frame.width,height/frame.height),w=frame.width*scale*zoom,h=frame.height*scale*zoom;
    pan.x=clamp(pan.x,-Math.max(0,(w-width)/2),Math.max(0,(w-width)/2));pan.y=clamp(pan.y,-Math.max(0,(h-height)/2),Math.max(0,(h-height)/2));
    return {x:(width-w)/2+pan.x,y:(height-h)/2+pan.y,width:w,height:h};
  }
  function normalized(point) {
    const r=fitRect();if (!r||point.x<r.x||point.y<r.y||point.x>r.x+r.width||point.y>r.y+r.height)return null;
    return {x:clamp((point.x-r.x)/r.width,0,1),y:clamp((point.y-r.y)/r.height,0,1)};
  }
  function screen(point,r) {return {x:r.x+point.x*r.width,y:r.y+point.y*r.height};}
  function hit(point) {
    const r=fitRect();if(!r)return -1;
    for(let i=annotations.length-1;i>=0;i--) {
      const a=annotations[i];
      if(a.kind==="box") {
        const start=screen(a,r);if(point.x>=start.x-6&&point.y>=start.y-6&&point.x<=start.x+a.w*r.width+6&&point.y<=start.y+a.h*r.height+6)return i;
      } else if(a.kind==="polygon") {
        const points=a.points.map(p=>screen(p,r));let inside=false;
        for(let j=0,k=points.length-1;j<points.length;k=j++) {
          const p=points[j],q=points[k];if((p.y>point.y)!==(q.y>point.y)&&point.x<(q.x-p.x)*(point.y-p.y)/(q.y-p.y)+p.x)inside=!inside;
        }
        if(inside)return i;
      }
    }
    return -1;
  }
  function select(index) {selected=index;onSelection(index);draw();}
  function emitAnnotations() {onAnnotations(structuredClone(annotations));status.textContent=`${annotations.length} ${translate("Annotations")} · ${Math.round(zoom*100)}%`;}
  function finalize(point) {
    if(!pending)return;
    const x=Math.min(pending.x,point.x),y=Math.min(pending.y,point.y),w=Math.abs(pending.x-point.x),h=Math.abs(pending.y-point.y);pending=null;
    if(w>=.003&&h>=.003&&annotations.length<2048) {annotations.push({kind:"box",class_id:activeClass,x,y,w,h});select(annotations.length-1);emitAnnotations();}
    draw();
  }
  function deleteSelected() {if(selected<0||selected>=annotations.length)return;annotations.splice(selected,1);select(-1);emitAnnotations();}
  function setZoom(percent,anchor=null) {
    const previous=anchor?normalized(anchor):null;zoom=clamp(Math.round(percent)/100,1,3);
    if(previous&&anchor) {const r=fitRect();pan.x+=anchor.x-(r.x+previous.x*r.width);pan.y+=anchor.y-(r.y+previous.y*r.height);}
    fitRect();onZoom(Math.round(zoom*100));draw();
  }
  function drawSticks(width,height) {
    const radius=Math.min(43,height/3);
    ["Left stick","Right stick"].forEach((name,i)=>{
      const x=width*(.25+i*.5),y=height/2-8;
      context.strokeStyle="#5f7fa9";context.fillStyle="#101827";context.lineWidth=1;
      context.beginPath();context.arc(x,y,radius,0,Math.PI*2);context.fill();context.stroke();
      context.beginPath();context.moveTo(x-radius,y);context.lineTo(x+radius,y);context.moveTo(x,y-radius);context.lineTo(x,y+radius);context.stroke();
      context.fillStyle=i===0?"#7dbcf5":"#e887c5";context.beginPath();context.arc(x+stickValues[i*2]/32768*radius,y-stickValues[i*2+1]/32768*radius,5,0,Math.PI*2);context.fill();
      context.fillStyle="#f2f6fa";context.textAlign="center";context.fillText(`${translate(name)}: ${stickValues[i*2]}, ${stickValues[i*2+1]}`,x,y+radius+20);
    });
  }
  function drawTimings(width,height) {
    if(!timings.length)return false;
    const peak=Math.max(1,...timings.flatMap(row=>row.values)),left=52,top=24,w=Math.max(1,width-68),h=height-52;
    context.fillStyle="#aab7c3";context.textAlign="left";context.fillText(`${peak.toFixed(0)} ms`,4,top+4);
    const span=Math.max(1,timings.at(-1).time-timings[0].time);
    context.fillText(`${timings.length} ${translate("samples")} | ${span.toFixed(0)} ${translate("seconds")}`,left,height-8);
    ["#7dbcf5","#a481f1","#b9e7ff","#e887c5"].forEach((color,i)=>{
      context.strokeStyle=color;context.lineWidth=1.5;context.beginPath();
      timings.forEach((row,index)=>{const x=left+(row.time-timings[0].time)/span*w,y=top+h-clamp(row.values[i]/peak,0,1)*h;if(index===0)context.moveTo(x,y);else context.lineTo(x,y);});context.stroke();
    });return true;
  }
  function draw() {
    if(disposed)return;const {width,height}=dimensions(),dpr=clamp(document.defaultView.devicePixelRatio||1,1,3);
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);context.setTransform(dpr,0,0,dpr,0,0);
    context.fillStyle=kind==="DatasetReviewCanvas"?"#060b13":kind==="PerformanceChart"?"#101827":"#080d16";context.fillRect(0,0,width,height);
    context.font=getComputedStyle(surface).font;context.textBaseline="alphabetic";
    if(kind==="StickView") {drawSticks(width,height);return;}
    if(kind==="PerformanceChart"&&drawTimings(width,height))return;
    const r=fitRect();
    if(frame&&r) {
      context.drawImage(frame,r.x,r.y,r.width,r.height);
      annotations.forEach((a,i)=>{
        const color=i===selected?"#f08bc8":"#78c3ff";context.strokeStyle=color;context.lineWidth=i===selected?3:2;
        if(a.kind==="box") {const p=screen(a,r);context.strokeRect(p.x,p.y,a.w*r.width,a.h*r.height);context.fillStyle=color;context.fillRect(p.x,Math.max(r.y,p.y-19),Math.max(36,12+String(a.class_id).length*8),18);context.fillStyle="#07111f";context.textAlign="left";context.fillText(String(a.class_id),p.x+5,Math.max(r.y,p.y-19)+13);}
        else {context.beginPath();a.points.forEach((p,j)=>{const s=screen(p,r);if(!j)context.moveTo(s.x,s.y);else context.lineTo(s.x,s.y);});context.closePath();context.stroke();}
      });
      if(cursor) {
        const p=screen(cursor,r);context.strokeStyle="#f7d34a";context.lineWidth=1;context.setLineDash([4,4]);context.beginPath();context.moveTo(r.x,p.y);context.lineTo(r.x+r.width,p.y);context.moveTo(p.x,r.y);context.lineTo(p.x,r.y+r.height);context.stroke();context.setLineDash([]);
        context.strokeStyle="#fff2a8";context.lineWidth=2;context.beginPath();context.moveTo(p.x-10,p.y);context.lineTo(p.x+10,p.y);context.moveTo(p.x,p.y-10);context.lineTo(p.x,p.y+10);context.arc(p.x,p.y,4,0,Math.PI*2);context.stroke();
      }
      if(pending) {const p=screen(pending,r),q=screen(cursor||pending,r);context.strokeStyle="#f7d34a";context.lineWidth=2;context.setLineDash([4,4]);context.strokeRect(Math.min(p.x,q.x),Math.min(p.y,q.y),Math.abs(p.x-q.x),Math.abs(p.y-q.y));context.setLineDash([]);}
    } else {context.fillStyle=kind==="PerformanceChart"?"#aab7c3":"#9db4cf";context.textAlign="center";context.fillText(label(),width/2,height/2+5);}
    surface.setAttribute("aria-label",label());
  }
  const point=event=>{const r=surface.getBoundingClientRect();return{x:event.clientX-r.left,y:event.clientY-r.top};};
  surface.addEventListener("pointerdown",event=>{
    if(kind!=="DatasetReviewCanvas"||!frame)return;surface.focus();const p=point(event);
    if(event.button===1){event.preventDefault();panOrigin={point:p,pan:{...pan}};surface.setPointerCapture(event.pointerId);return;}
    const normalizedPoint=normalized(p);if(!normalizedPoint)return;const found=hit(p);
    if(event.button===2){if(found>=0){select(found);deleteSelected();}return;}
    if(event.button!==0)return;
    if(found>=0&&!pending)select(found);else if(pending)finalize(normalizedPoint);else{pending=normalizedPoint;dragOrigin=normalizedPoint;select(-1);surface.setPointerCapture(event.pointerId);}
  });
  surface.addEventListener("pointermove",event=>{
    if(kind!=="DatasetReviewCanvas"||!frame)return;const p=point(event);
    if(panOrigin){pan={x:panOrigin.pan.x+p.x-panOrigin.point.x,y:panOrigin.pan.y+p.y-panOrigin.point.y};fitRect();}
    cursor=normalized(p);draw();
  });
  surface.addEventListener("pointerup",event=>{
    if(event.button===1){panOrigin=null;return;}
    const p=normalized(point(event));if(event.button===0&&dragOrigin&&pending&&p&&(Math.abs(p.x-dragOrigin.x)>=.008||Math.abs(p.y-dragOrigin.y)>=.008))finalize(p);
    dragOrigin=null;
  });
  surface.addEventListener("pointercancel",()=>{panOrigin=null;dragOrigin=null;pending=null;draw();});
  surface.addEventListener("pointerleave",()=>{cursor=null;draw();});
  surface.addEventListener("contextmenu",event=>{if(kind==="DatasetReviewCanvas"&&frame)event.preventDefault();});
  surface.addEventListener("wheel",event=>{if(kind!=="DatasetReviewCanvas"||!frame)return;event.preventDefault();setZoom(Math.round(zoom*100)+(event.deltaY<0?20:-20),point(event));},{passive:false});
  surface.addEventListener("keydown",event=>{
    if(kind!=="DatasetReviewCanvas"||!frame)return;
    if(["Delete","Backspace"].includes(event.key)&&selected>=0){event.preventDefault();deleteSelected();}
    else if(event.key==="Escape"){pending=null;select(-1);}
    else if(event.key==="+"||event.key==="="){event.preventDefault();setZoom(zoom*100+20);}
    else if(event.key==="-"){event.preventDefault();setZoom(zoom*100-20);}
    else if(event.key==="0"){event.preventDefault();zoom=1;pan={x:0,y:0};onZoom(100);draw();}
  });
  const observer=new ResizeObserver(draw);observer.observe(surface);
  function setFrame(rgba,width,height) {
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>4096||height>4096||width*height>16777216||!ArrayBuffer.isView(rgba)||!(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray)||typedArrayByteLength.call(rgba)!==width*height*4)throw new Error("Invalid raw preview frame");
    const pixels=document.createElement("canvas");pixels.width=width;pixels.height=height;pixels.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(rgba),width,height),0,0);frame=pixels;zoom=1;pan={x:0,y:0};pending=null;select(-1);onZoom(100);draw();
  }
  return Object.freeze({element:surface,draw,setFrame,setZoom,deleteSelected,zoomPercent(){return Math.round(zoom*100);},
    resetZoom(){zoom=1;pan={x:0,y:0};onZoom(100);draw();},
    setActiveClass(value){if(!Number.isInteger(value)||value<0||value>1000000)throw new Error("Invalid class");activeClass=value;},
    setAnnotations(value){
      if(!Array.isArray(value)||value.length>2048)throw new Error("Invalid annotations");
      annotations=value.map(a=>{
        if(!a||!Number.isInteger(a.class_id)||a.class_id<0||a.class_id>1000000)throw new Error("Invalid annotation class");
        if(a.kind==="box"&&[a.x,a.y,a.w,a.h].every(n=>typeof n==="number"&&Number.isFinite(n)&&n>=0&&n<=1)&&a.x+a.w<=1.000001&&a.y+a.h<=1.000001)return{kind:"box",class_id:a.class_id,x:a.x,y:a.y,w:a.w,h:a.h};
        if(a.kind==="polygon"&&Array.isArray(a.points)&&a.points.length>=3&&a.points.length<=512&&a.points.every(p=>p&&[p.x,p.y].every(n=>typeof n==="number"&&Number.isFinite(n)&&n>=0&&n<=1)))return{kind:"polygon",class_id:a.class_id,points:a.points.map(p=>({x:p.x,y:p.y}))};
        throw new Error("Invalid annotation geometry");
      });selected=-1;pending=null;draw();
    },
    setSticks(value){if(!Array.isArray(value)||value.length!==4||!value.every(n=>Number.isInteger(n)&&n>=-32768&&n<=32767))throw new Error("Invalid stick values");stickValues=[...value];draw();},
    setTimings(value){if(!Array.isArray(value)||value.length>1200||!value.every(r=>r&&Number.isFinite(r.time)&&Array.isArray(r.values)&&r.values.length===4&&r.values.every(n=>Number.isFinite(n)&&n>=0&&n<=1000000)))throw new Error("Invalid timing samples");timings=value.map(r=>({time:r.time,values:[...r.values]}));draw();},
    dispose(){disposed=true;observer.disconnect();frame=null;annotations=[];timings=[];},
  });
}
