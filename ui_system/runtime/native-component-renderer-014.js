/* Trusted DOM port of the native Control Center presentation.
 * Input is a pinned build-time layout descriptor, never a theme or bridge
 * command list. Control edits are local preview state until a separately
 * reviewed Python adapter is attached. No HTML, URLs or executable theme data.
 */
import {createNativeCanvas} from "./native-canvases.js";
import {createNativePolicyTable} from "./native-policy.js";
import {useNativeComponentPlan} from "./native-components.js";
import {createNativeComponentViews} from "./native-component-views.js";
const DISCRETE_CONTROL_IDS=new Set(["root/2/1/0/8/0/0/0/7/2","root/2/1/0/8/0/0/0/8/2","root/2/1/0/8/0/0/0/9/2","root/2/1/0/7/0/0/0/3/2","root/2/1/0/7/0/0/0/5/2","root/2/1/0/6/0/0/0/0/1/3/2/0/0/2/6","root/2/1/0/6/0/0/0/0/1/3/1/0/0/3/1","root/2/1/0/6/0/0/0/0/1/3/1/0/0/4/1","root/2/1/0/6/0/0/0/0/1/3/0/0/0/9/1","root/2/1/0/6/0/0/0/0/1/3/0/0/0/15/1","root/2/1/0/6/0/0/0/0/1/3/0/0/0/16/1","root/2/1/0/6/0/0/0/0/1/3/0/0/0/17/1","root/2/1/0/6/0/0/0/2/1/7/0/0/1/2","root/2/1/0/6/0/0/0/2/1/7/0/0/10/2","root/2/1/0/6/0/0/0/2/1/0/0/0/1/2","root/2/1/0/6/0/0/0/2/1/0/0/0/2/2","root/2/1/0/6/0/0/0/2/1/0/0/0/3/2","root/2/1/0/6/0/0/0/2/1/0/0/0/4/2","root/2/1/0/6/0/0/0/2/1/6/0/0/7/2","root/2/1/0/6/0/0/0/2/1/6/0/0/11/2","root/2/1/0/6/0/0/0/2/1/5/0/0/1/2","root/2/1/0/6/0/0/0/2/1/5/0/0/2/2","root/2/1/0/6/0/0/0/2/1/5/0/0/3/2","root/2/1/0/6/0/0/0/2/1/5/0/0/4/2","root/2/1/0/6/0/0/0/2/1/4/0/0/1/2","root/2/1/0/6/0/0/0/2/1/3/0/0/1/2","root/2/1/0/6/0/0/0/2/1/3/0/0/2/2","root/2/1/0/6/0/0/0/2/1/3/0/0/3/2","root/2/1/0/6/0/0/0/2/1/3/0/0/4/2","root/2/1/0/6/0/0/0/2/1/3/0/0/5/2","root/2/1/0/6/0/0/0/2/1/2/0/0/1/2","root/2/1/0/6/0/0/0/2/1/2/0/0/2/2","root/2/1/0/6/0/0/0/2/1/2/0/0/3/2","root/2/1/0/6/0/0/0/2/1/2/0/0/6/2","root/2/1/0/6/0/0/0/2/1/2/0/0/11/2","root/2/1/0/6/0/0/0/2/1/2/0/0/12/2","root/2/1/0/6/0/0/0/2/1/2/0/0/13/2","root/2/1/0/6/0/0/0/2/1/2/0/0/14/2","root/2/1/0/5/0/0/0/1/1/3/0/0/3/2","root/2/1/0/5/0/0/0/1/1/3/0/0/4/2","root/2/1/0/5/0/0/0/1/1/3/0/0/5/2","root/2/1/0/5/0/0/0/1/1/3/0/0/7/2","root/2/1/0/5/0/0/0/1/1/3/0/0/8/2","root/2/1/0/5/0/0/0/1/1/3/0/0/9/2","root/2/1/0/5/0/0/0/1/1/1/0/0/1/2","root/2/1/0/5/0/0/0/1/1/1/0/0/2/2","root/2/1/0/5/0/0/0/1/1/1/0/0/3/2","root/2/1/0/5/0/0/0/1/1/1/0/0/4/2","root/2/1/0/5/0/0/0/1/1/2/0/0/9/2","root/2/1/0/5/0/0/0/1/1/2/0/0/11/2","root/2/1/0/4/0/0/0/1/1/3/0/0/1/2","root/2/1/0/4/0/0/0/1/1/3/0/0/2/2/0","root/2/1/0/4/0/0/0/1/1/3/0/0/8/2","root/2/1/0/4/0/0/0/1/1/2/0/0/8/2","root/2/1/0/4/0/0/0/1/1/2/0/0/9/2","root/2/1/0/4/0/0/0/1/1/2/0/0/12/2","root/2/1/0/4/0/0/0/1/1/2/0/0/13/2","root/2/1/0/4/0/0/0/1/1/2/0/0/15/2","root/2/1/0/4/0/0/0/1/1/2/0/0/16/2/0","root/2/1/0/4/0/0/0/1/1/2/0/0/16/2/1","root/2/1/0/4/0/0/0/1/1/2/0/0/16/2/2","root/2/1/0/4/0/0/0/1/1/2/0/0/16/2/3","root/2/1/0/4/0/0/0/1/1/2/0/0/16/2/4","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/7/2","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/15/2","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/16/2","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/18/2","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/19/2/0","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/19/2/1","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/19/2/2","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/19/2/3","root/2/1/0/4/0/0/0/1/1/1/0/1/1/0/0/19/2/4","root/2/1/0/4/0/0/0/1/1/0/0/0/3/2/3","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/14/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/17/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/18/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/26/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/27/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/28/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/29/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/30/2","root/2/1/0/4/0/0/0/1/1/1/0/1/0/0/0/31/2"]);
const LANGUAGES=new Set(["en","zh-CN","de","ru","fr"]);
const CAPTURE_PERCENT_CONTROL_ID='root/2/1/0/4/0/0/0/1/1/3/0/0/3/2';
export function capturePercentToNative(raw) {
  if(typeof raw!=='string'||!/^(?:[1-9][0-9]?|100)(?:\.[0-9]{1,2})?$/.test(raw))return NaN;
  const percent=Number(raw);
  return percent>=1&&percent<=100?Number((percent/100).toFixed(4)):NaN;
}
const ROUTES=[
  ["OBSERVE",[["overview","Overview","dashboard"],["model","Model Library","model-library"],["detection","Detection","detection-settings"]]],
  ["TRACK",[["selection","Targeting","targeting-settings"],["output","Movement","movement-settings"]]],
  ["MANAGE",[["dataset","Training Data","training-data"],["profiles","Profiles","profiles"]]],
  ["SETTINGS",[["general","General","general-settings"],["appearance","Appearance","appearance"],["shortcuts","Shortcuts","shortcuts"],["automation","API Suite","api-suite"],["diagnostics","Status & Diagnostics","diagnostics"]]],
];
const ROUTE_MAP=new Map(ROUTES.flatMap(([,items])=>items.map(([route,label,component])=>[route,{label,component}])));
const COMPONENT_ROUTES=new Map([...ROUTE_MAP].map(([route,{component}])=>[component,route]));
const COLOURS=new Set(["--vl-accent","--vl-accent-secondary","--vl-background","--vl-panel","--vl-text","--vl-text-muted","--vl-border","--vl-shadow-color","--vl-glow-color","--vl-gradient-start","--vl-gradient-end"]);
const LENGTHS=new Map([["--vl-radius",[0,40]],["--vl-blur",[0,40]],["--vl-spacing",[4,24]],["--vl-border-width",[0,6]],["--vl-shadow-blur",[0,64]],["--vl-shadow-spread",[0,16]],["--vl-glow-blur",[0,40]]]);
const NUMBERS=new Map([["--vl-ui-scale",[.75,1.5]],["--vl-glass-strength",[.1,1]],["--vl-animation-intensity",[0,1]]]);
const FONTS=new Set(["Segoe UI","Segoe UI Variable","Arial","Tahoma","Verdana"]);
// Explicit native presentation relationships, never a Python command allowlist.
const SHARED_NUMERIC_LABELS=new Set(["Capture rate","Inference rate","Frame skip","Snap radius","Near radius","Acquisition radius","Retention radius","Switch tolerance"]);
const NUMERIC_METRICS=new Map([["Snap radius","policy_snap_metric"],["Near radius","policy_near_metric"],["Acquisition radius","policy_acquisition_metric"],["Retention radius","policy_retention_metric"],["Switch tolerance","policy_switching_value"]]);
const clamp=(value,low,high)=>Math.min(high,Math.max(low,value));
const text=(value,fallback="")=>typeof value==="string"&&value.length<=4096?value:fallback;
// Display-only drafts. A completed gesture still passes through the closed
// native command adapter; neither this draft nor its enabled state grants access.
function createNativeNumericDraft() {
  let state=null,draft=null,blockedGesture=false,notice=null,preparedCandidate=null;
  return Object.freeze({
    accept(next){state=next;
      if(preparedCandidate&&(!next.record.enabled||next.record.value!==preparedCandidate.baseValue
        ||next.record.id!==preparedCandidate.fieldId||next.target!==preparedCandidate.target
        ||(next.disabled&&!next.holdDraft)))preparedCandidate=null;
      if(draft&&(!next.record.enabled||next.record.value!==draft.baseValue
        ||next.record.id!==draft.fieldId||next.target!==draft.target
        ||(next.disabled&&!next.holdDraft))){
        notice=!next.record.enabled||(next.disabled&&!next.holdDraft)?'unavailable':'changed';
        draft=null;blockedGesture=true;}
    },
    begin(){blockedGesture=false;preparedCandidate=null;},
    edit(value,input,raw){
      if(blockedGesture||!state?.record.enabled||state.record.value===null
        ||(state.disabled&&(!state.holdDraft||!draft)))return false;
      if(!draft)draft={baseValue:state.record.value,fieldId:state.record.id,target:state.target};
      draft={...draft,value,input,raw,released:false};notice=null;preparedCandidate=null;return true;
    },
    snapshot(){return draft?Object.freeze({...draft}):null;},
    finish(value,input){draft=null;preparedCandidate=null;
      if(blockedGesture){blockedGesture=false;return null;}
      if(!state?.record.enabled||state.record.value===null)return null;
      notice=null;
      if(Number.isFinite(value)&&value===Number(state.record.value))return null;
      preparedCandidate=Object.freeze({value,input,baseValue:state.record.value,fieldId:state.record.id,target:state.target});
      return preparedCandidate;
    },
    retain(candidate){
      if(!candidate||candidate!==preparedCandidate||!state?.record.enabled||state.record.value!==candidate.baseValue||state.record.id!==candidate.fieldId
        ||state.target!==candidate.target||!Number.isFinite(candidate.value)
        ||String(candidate.value).length>32)return false;
      draft={...candidate,raw:String(candidate.value),released:true};notice=null;preparedCandidate=null;return true;
    },
    notice(){return notice;},
    cancel(){if(draft){blockedGesture=true;notice='discarded';}draft=null;preparedCandidate=null;},
  });
}
function appearanceToken(name,value) {
  if (COLOURS.has(name)) {
    if (typeof value!=="string"||value.length>80) return null;
    if (/^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$/.test(value)) return value;
    const m=/^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(0(?:\.\d{1,6})?|1(?:\.0{1,6})?)\s*\)$/.exec(value);
    return m&&!m.slice(1,4).some(n=>Number(n)>255)?`rgba(${Number(m[1])},${Number(m[2])},${Number(m[3])},${Number(m[4])})`:null;
  }
  if (LENGTHS.has(name)) {
    if (typeof value!=="string"||!/^\d{1,3}(?:\.\d{1,3})?px$/.test(value)) return null;
    const n=Number(value.slice(0,-2)),[low,high]=LENGTHS.get(name); return n>=low&&n<=high?`${n}px`:null;
  }
  if (NUMBERS.has(name)) { const [low,high]=NUMBERS.get(name); return typeof value==="number"&&Number.isFinite(value)&&value>=low&&value<=high?String(value):null; }
  if (name==="--vl-font") return FONTS.has(value)?`"${value==="Segoe UI Variable"?"Segoe UI Variable Text":value}"`:null;
  if (name==="--vl-gradient-angle"&&typeof value==="string"&&/^\d{1,3}(?:\.\d{1,3})?deg$/.test(value)) return Number(value.slice(0,-3))<=360?value:null;
  return null;
}

export function createNativeVaporLockRenderer(mount,{componentPlan,translations,language="en",chrome=true,inspect=false,onNavigate=()=>{},onSelect=()=>{},onValue=()=>{},onAction=()=>{},onAppearance=()=>{}}={}) {
  const composition=useNativeComponentPlan(componentPlan),{presentation,registry}=composition;
  if (!mount||presentation?.descriptorVersion!==1||!presentation.nodes||!presentation.pages||Object.keys(presentation.nodes).length>6000) throw new Error("Unsupported native presentation descriptor");
  if (registry?.componentRegistryVersion!==1||!Array.isArray(registry.components)||registry.components.length>128) throw new Error("Unsupported component registry");
  const document=mount.ownerDocument,definitions=new Map(registry.components.map(item=>[item.id,item]));
  const descriptor=presentation.nodes,cache=new Map(),values=new Map(),tabs=new Map(),tabActivators=new Map(),localized=[],canvases=new Map(),policyTables=new Map();
  const nativeNumericState=new Map(),nativeNumericViews=new Map(),nativeNumericDrafts=new Map();
  const nativeDiscreteState=new Map(),nativeDiscreteViews=new Map();
  const numericState=new Map(),numericGroups=new Map(),numericViews=new Map();
  const componentElements=new Map(),headingSlots=new Map(),appearanceValues=new Map();
  let generatedViews=null;
  let currentLanguage=LANGUAGES.has(language)?language:"en",currentPage="overview",selected=null,textScale=1,disposed=false;
  const node=(tag,className="",content)=>{ const n=document.createElement(tag); n.className=className; if (content!==undefined)n.textContent=content; return n; };
  const sourceCase=new Map(Object.keys(translations?.en||{}).map(key=>[key.toLocaleLowerCase("en"),key]));
  function t(source) {
    source=text(source);
    if (translations?.[currentLanguage]?.[source])return text(translations[currentLanguage][source],source);
    if (source.startsWith("VAPOR LOCK / "))return `VAPOR LOCK / ${t(source.slice(13)).toLocaleUpperCase(currentLanguage)}`;
    const key=sourceCase.get(source.toLocaleLowerCase("en"));
    if (key&&source===source.toLocaleUpperCase("en"))return text(translations?.[currentLanguage]?.[key],key).toLocaleUpperCase(currentLanguage);
    return text(translations?.en?.[source],source);
  }
  function copy(n,source,kind="text") {
    source=text(source); localized.push([n,source,kind]);
    if (kind==="title")n.title=t(source); else if (kind==="placeholder")n.placeholder=t(source);else if(kind==="aria-label")n.setAttribute("aria-label",t(source));else n.textContent=t(source);
    return n;
  }
  function component(n,id) {
    if(!definitions.has(id)||componentElements.has(id))throw new Error("Duplicate/unknown native component");
    n.dataset.vlComponent=id;componentElements.set(id,n);return n;
  }
  const app=node("section","vl-native-app");
  const backdrop=node("div","vl-native-backdrop"); backdrop.setAttribute("aria-hidden","true"); app.append(backdrop);
  const root=component(node("section","vl-renderer vl-native-workspace"),"workspace");
  const sidebar=component(node("aside","vl-native-sidebar"),"sidebar");
  const navigation=component(node("nav","vl-native-navigation"),"navigation");
  const footer=node("div","vl-native-guide");footer.tabIndex=0;navigation.tabIndex=0;
  const guide=component(copy(node("button","vl-guideLink"),"Settings guide"),"settings-guide"); guide.type="button";
  guide.addEventListener("click",()=>onNavigate("settings-guide")); copy(guide,"Open the bundled guide in your browser.","title");
  footer.append(guide); sidebar.append(navigation,footer);
  const content=node("main","vl-native-content"); root.append(sidebar,content);
  const nativePageHeading=node("span","vl-headerPage");
  const workspaceHeading=component(node("div","vl-native-workspace-heading"),"workspace-header");
  const headingKicker=node("span"),headingTitle=node("h1");headingTitle.tabIndex=-1;workspaceHeading.append(headingKicker,headingTitle);
  const navButtons=new Map();
  for (const [group,items] of ROUTES) {
    const groupNode=node("div","vl-native-nav-group"); groupNode.append(copy(node("div","vl-navGroup"),group));
    if(group==="SETTINGS")component(groupNode,"settings");
    for (const [route,label] of items) {
      const button=copy(node("button","vl-navButton"),label); button.type="button";
      button.dataset.vlRoute=route; button.addEventListener("click",()=>showPage(route,true));
      navButtons.set(route,button); groupNode.append(button);
    }
    navigation.append(groupNode);
  }
  function makeChrome() {
    // This is a synthetic representation for the builder. In the client these
    // controls are native Qt siblings, outside all theme CSS/layout surfaces.
    const bar=node("header","vl-fixed-commandBar"); bar.dataset.vlProtected="command-bar";
    const mark=node("span","vl-brandMark","VL");
    const identity=node("div","vl-native-identity"); identity.append(node("strong","vl-brandWordmark","VAPOR LOCK"),copy(node("small","vl-brandPath"),"CONTROL CENTER / PREMIUM"));
    const divider=node("span","vl-headerDivider");
    const context=node("div","vl-native-context"); context.append(copy(node("small","vl-headerKicker"),"WORKSPACE"),nativePageHeading);
    function choice(label,items,cls) {
      const group=node("label","vl-native-command-choice"); group.append(copy(node("small","vl-headerKicker"),label));
      const select=node("select",cls);
      copy(select,label,"aria-label");
      for (const item of items) { const option=copy(node("option"),item); option.value=item; select.append(option); }
      if(cls==="vl-sessionModeChoice")select.addEventListener("change",measureFit);
      group.append(select); return group;
    }
    const profile=choice("QUICK PROFILE",["No saved profiles"],"vl-profileQuickSwitch");
    const search=copy(node("button","vl-native-icon-button vl-native-icon-search"),"Search settings (Ctrl+K)","title"); search.type="button";copy(search,"Search settings","aria-label");search.addEventListener("click",openSearch);
    const session=choice("SESSION MODE",["Control","Observe only"],"vl-sessionModeChoice");
    const status=copy(node("span","vl-statusPill"),"READY");
    const undo=copy(node("button","vl-native-icon-button vl-native-icon-back"),"Undo last tuning change","title"); undo.type="button"; undo.disabled=true;copy(undo,"Undo last tuning change","aria-label");
    const save=copy(node("button","vl-native-icon-button vl-native-icon-save"),"Save profile","title"); save.type="button";copy(save,"Save profile","aria-label");save.setAttribute("aria-disabled","true");
    const start=copy(node("button","vl-primaryButton vl-native-icon-play"),"Start"),stop=copy(node("button","vl-dangerButton vl-native-icon-stop"),"Stop");
    for (const button of [start,stop]) {button.type="button";button.setAttribute("aria-disabled","true");copy(button,"Sample state only","title");}
    stop.disabled=true;
    bar.append(mark,identity,divider,context,profile,search,session,status,undo,save,start,stop);
    return bar;
  }
  if (chrome)app.append(makeChrome()); app.append(root);
  if (chrome) {
    const hud=node("footer","vl-fixed-telemetryStrip"); hud.dataset.vlProtected="telemetry";
    for (const [caption,value] of [["RATE","--"],["INFERENCE","--"],["TRACKS","0"],["OUTPUT","IDLE"]]) {
      const group=node("span","vl-native-hud-group"); group.append(copy(node("small","vl-hudLabel"),caption),copy(node("strong","vl-hudValue"),value)); hud.append(group);
    }
    hud.append(node("span","vl-native-hud-spacer"),copy(node("span","vl-hudLabel vl-native-community"),"COMMUNITY"),copy(node("span","vl-hudLabel"),"VAPOR LOCK 0.1.3 / PREMIUM EDITION")); app.append(hud);
  } else app.classList.add("vl-native-without-chrome");
  mount.replaceChildren(app);
  generatedViews=createNativeComponentViews({document,node,copy,translate:t,setLanguage,setAccessibility,setAppearance,resetAppearance,showPage,
    initialLanguage:currentLanguage,initialTextScale:textScale});
  for(const element of generatedViews.overviewRow.children)componentElements.set(element.dataset.vlComponent,element);

  function decorate(n,d) {
    n.classList.add("vl-q");
    if (/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(d.objectName||""))n.classList.add(`vl-${d.objectName}`);
    n.dataset.vlNode=d.id;
    const sourceOwner=composition.sourceOwner(d.id);if(sourceOwner)n.dataset.vlSourceOwner=sourceOwner;
    if (d.attribute)n.dataset.vlAttribute=text(d.attribute);
    if (d.tooltip)copy(n,d.tooltip,"title");
    if (d.font?.pointSize>0&&d.font.pointSize<=40) n.style.fontSize=`calc(${d.font.pointSize*4/3}px * var(--vl-ui-scale) * var(--vl-accessibility-text-scale,1))`;
    if (d.font?.weight>=100&&d.font.weight<=900)n.style.fontWeight=String(d.font.weight);
    if(d.wordWrap)n.classList.add("vl-word-wrap");
    for(const [axis,property]of [[0,"width"],[1,"height"]]) {
      const low=d.minimum?.[axis],high=d.maximum?.[axis];
      if(Number.isInteger(low)&&low>0&&low===high&&low<=2000)n.style[property]=`${low}px`;
    }
    if(Number.isInteger(d.minimum?.[1])&&d.minimum[1]>0&&d.minimum[1]<=2000)n.style.minHeight=`${d.minimum[1]}px`;
    const id=composition.rootComponent(d.id),rec=id?composition.component(id):null;
    if(rec?.sourceRoots.length===1)component(n,id);
    return n;
  }
  function refreshNumericMetrics() {
    for(const [source,attribute]of NUMERIC_METRICS)if(numericState.has(source)) {
      const metric=[...cache.values()].find(n=>n.dataset.vlAttribute===attribute);
      if(metric)metric.textContent=source==="Switch tolerance"?numericState.get(source).toFixed(2):`${Math.trunc(numericState.get(source))} px`;
    }
  }
  function localValue(id,value,input) {
    if(nativeDiscreteState.has(id)){
      const state=nativeDiscreteState.get(id);
      const semantic=state.spec.kind==='boolean'?value:state.spec.choices.find(option=>String(option)===value);
      const result=onValue(Object.freeze({controlId:id,value:semantic,label:t(ownLabel(descriptor[id]))}));
      nativeDiscreteViews.get(id)?.(nativeDiscreteState.get(id));return result;
    }
    if(nativeNumericState.has(id))return onValue(Object.freeze({controlId:id,value,input,label:t(ownLabel(descriptor[id]))}));
    values.set(id,value);
    const group=numericGroups.get(id);
    if(group&&typeof value==="number"&&Number.isFinite(value)) {
      numericState.set(group,value);
      for(const [otherId,view]of numericViews.get(group)||[]) {values.set(otherId,value);view(value);}
      refreshNumericMetrics();
    }
    onValue(Object.freeze({controlId:id,value,input}));
  }
  function inputState(n,d,kind) {
    n.disabled=!d.enabled;
    const label=ownLabel(d)||d.tooltip||d.attribute||d.type;
    n.setAttribute("aria-label",t(label)); localized.push([n,label,"aria-label"]);
    n.addEventListener("change",()=>{
      if(kind==="number") {
        if(!n.value||!Number.isFinite(Number(n.value)))return;
        n.value=String(clamp(Number(n.value),d.low,d.high));
      }
      localValue(d.id,kind==="checked"?n.checked:kind==="number"?Number(n.value):n.value);
    });
  }
  function ownLabel(d) {
    if(d.type==="QCheckBox"||d.type==="QRadioButton")return d.text;
    let parent=descriptor[d.parent];
    for (let depth=0;parent&&depth<7;depth++,parent=descriptor[parent.parent]) {
      const children=Object.values(descriptor).filter(item=>item.parent===parent.id);
      if(parent.objectName!=="policyInspectorCard") {
        const label=children.find(item=>item.objectName==="fieldTitle"||item.objectName==="policyControlLabel");
        if(label&&!(label.text==="Opacity"&&d.type==="QLineEdit"))return label.text;
      }
      if (parent.objectName==="settingsCard"||parent.objectName==="policyInspectorCard") {
        const order=parent.layout?.items||[],position=order.findIndex(item=>item.widget===d.id);
        if(position>=0)for(let i=position-1;i>=0;i--){const label=descriptor[order[i].widget];if(label?.type==="QLabel"&&label.objectName==="policyControlLabel")return label.text;}
        return children.find(item=>item.objectName==="fieldTitle"||item.objectName==="policyControlLabel")?.text||parent.tooltip;
      }
    }
    return d.text||d.tooltip;
  }
  function layout(l,depth,generated=false) {
    if (!l||depth>64||!Array.isArray(l.items)||l.items.length>512)throw new Error("Invalid trusted layout");
    const box=node("div",l.type==="QGridLayout"?"vl-layout vl-grid":l.type==="QHBoxLayout"?"vl-layout vl-hbox":"vl-layout vl-vbox");
    const margins=l.margins;
    if (Array.isArray(margins)&&margins.length===4&&margins.every(n=>Number.isInteger(n)&&n>=0&&n<=128))box.style.padding=`${margins[1]}px ${margins[2]}px ${margins[3]}px ${margins[0]}px`;
    const spacing=l.styleDefaultSpacing?6:clamp(Number(l.spacing)||0,0,64);
    box.style.setProperty("--vl-layout-gap",`${spacing}px`);
    box.style.gap=l.type==="QGridLayout"?`${spacing}px`:"0px";
    if(l.direction===1)box.style.flexDirection="row-reverse";
    if(l.direction===3)box.style.flexDirection="column-reverse";
    if (l.type==="QGridLayout")box.style.gridTemplateColumns=`repeat(${clamp(l.columns||1,1,16)},minmax(0,1fr))`;
    let realItems=0;
    const reviewToolbar=l.items.some(item=>descriptor[item.widget]?.attribute==="dataset_review_accept");
    if(reviewToolbar)box.classList.add("vl-native-review-toolbar");
    if(l.type==="QGridLayout"&&l.items.length===4&&l.items.every(item=>descriptor[item.widget]?.objectName==="metricCard"))box.classList.add("vl-native-cockpit");
    const reviewBody=l.items.some(item=>descriptor[item.widget]?.attribute==="dataset_review_preview")&&l.items.some(item=>descriptor[item.widget]?.attribute==="dataset_review_list");
    if(reviewBody)box.classList.add("vl-native-review-body");
    let zoomGroup=null;
    const items=generated?l.items:composition.layoutItems(l);
    for (const item of items) {
      let child;
      if(item.componentGroup) {
        child=component(layout(item.layout,depth+1,true),item.componentGroup);child.classList.add("vl-native-component-group");
        if(item.componentGroup==="system-monitor") {
          const heading=child.querySelector(".vl-sectionLabel");if(heading)copy(heading,"Session telemetry");
        }
      }else if(item.headingSlot) {
        const anchor=item.headingSlot;child=node("div","vl-native-heading-slot");child.dataset.vlHeaderRoute=anchor.route;
        const originals=node("div","vl-native-original-heading");originals.hidden=true;
        originals.append(renderNode(anchor.kicker,depth+1),renderNode(anchor.title,depth+1));child.append(originals);
        headingSlots.set(anchor.route,child);
      }else child=item.widget?renderNode(item.widget,depth+1):item.layout?layout(item.layout,depth+1):node("div","vl-spacer");
      if(l.type!=="QGridLayout"&&!item.spacer) {
        child.style.setProperty("--vl-parent-gap",`${spacing}px`);
        if(realItems++)child.style[l.type==="QHBoxLayout"?"marginInlineStart":"marginBlockStart"]="var(--vl-parent-gap)";
      }
      if (item.stretch>0)child.style.flex=`${clamp(item.stretch,1,100)} 1 0%`;
      if (item.spacer&&item.stretch===0) {
        if (l.type==="QHBoxLayout")child.style.width=`${clamp(item.spacer[0],0,128)}px`;
        else child.style.height=`${clamp(item.spacer[1],0,128)}px`;
      }
      if (l.type==="QGridLayout"&&item.position) {
        const [row,column,rowSpan,columnSpan]=item.position;
        child.style.gridRow=`${row+1} / span ${rowSpan}`;child.style.gridColumn=`${column+1} / span ${columnSpan}`;
      }
      if(reviewToolbar&&descriptor[item.widget]?.text==="Image zoom") {
        zoomGroup=node("div","vl-native-review-zoom");zoomGroup.setAttribute("role","group");copy(zoomGroup,"Image zoom","aria-label");box.append(zoomGroup);
      }
      if(reviewToolbar&&zoomGroup&&!item.spacer)zoomGroup.append(child);
      else if(!reviewToolbar||!item.spacer)box.append(child);
      if(item.componentGroup==="system-monitor") {
        const row=generatedViews.overviewRow;
        row.style.marginBlockStart="var(--vl-parent-gap)";row.style.setProperty("--vl-parent-gap",`${spacing}px`);box.append(row);
      }
    }
    return box;
  }
  function renderTabs(d,depth) {
    const tabbox=node("section","vl-native-tabs"),rail=node("div","vl-native-tab-rail"),panels=node("div","vl-native-tab-panels");
    rail.setAttribute("role","tablist");
    const names=d.tabs.titles,buttons=[],pages=[];
    let index=tabs.get(d.id)||0;
    function activate(value,focus=false) {
      if(value!==index)cancelNativeNumericDrafts();
      index=value;tabs.set(d.id,value);
      buttons.forEach((b,i)=>{b.setAttribute("aria-selected",String(i===index));b.tabIndex=i===index?0:-1;});
      pages.forEach((p,i)=>{p.hidden=i!==index;});
      if (focus)buttons[index].focus();
      if(tabbox.isConnected)measureFit();
    }
    names.forEach((name,i)=>{
      const button=copy(node("button","vl-secondaryTabButton"),name==="Policy"?"Targeting policy tab":name);button.type="button";
      button.id=`vl-tab-${d.id.replace(/[^a-zA-Z0-9]/g,"-")}-${i}`;button.setAttribute("role","tab");
      const page=renderNode(d.tabs.pages[i],depth+1); page.id=`${button.id}-panel`;page.setAttribute("role","tabpanel");page.setAttribute("aria-labelledby",button.id);button.setAttribute("aria-controls",page.id);
      button.addEventListener("click",()=>activate(i));
      button.addEventListener("keydown",event=>{
        const key=event.key;
        if (!["ArrowLeft","ArrowRight","Home","End"].includes(key))return;
        event.preventDefault();activate(key==="Home"?0:key==="End"?names.length-1:(index+(key==="ArrowRight"?1:names.length-1))%names.length,true);
      });
      buttons.push(button);pages.push(page);rail.append(button);panels.append(page);
    });
    tabbox.append(rail,panels);tabActivators.set(d.id,activate);activate(index);return tabbox;
  }
  function renderRange(d) {
    const control=node("div","vl-native-range"),range=node("input","vl-native-slider"),spin=node("input","vl-native-number");
    range.type="range";spin.type="number";
    const r=d.range,label=ownLabel(d)||d.tooltip||"Value",group=SHARED_NUMERIC_LABELS.has(label)?label:null,value=(group?numericState.get(group):undefined)??values.get(d.id)??r.value;
    const percent=d.id===CAPTURE_PERCENT_CONTROL_ID,factor=percent?100:1,displayDecimals=percent?2:r.decimals;
    const accent={pink:"#ffb3c6",highlight:"#ffd173",baby:"#7dbcf5",cobalt:"#7dbcf5",ultraviolet:"#7dbcf5"}[r.barAccent];
    if(accent)control.style.setProperty("--vl-slider-accent",accent);
    range.min=r.low;range.max=r.high;range.step=r.step;range.value=clamp(value,r.low,r.high);
    spin.min=r.spinLow*factor;spin.max=r.spinHigh*factor;spin.step=percent?1:r.step;spin.value=(Number(value)*factor).toFixed(displayDecimals);
    range.hidden=percent;if(percent)control.classList.add('vl-native-percent');
    range.disabled=spin.disabled=!d.enabled;
    range.setAttribute("aria-label",t(label));spin.setAttribute("aria-label",t(label));localized.push([range,label,"aria-label"],[spin,label,"aria-label"]);
    function fill() {range.style.setProperty("--vl-slider-fill",`${100*clamp((Number(spin.value)/factor-Number(range.min))/(Number(range.max)-Number(range.min)||1),0,1)}%`);}
    if(group) {
      numericGroups.set(d.id,group);if(!numericViews.has(group))numericViews.set(group,new Map());
      numericViews.get(group).set(d.id,value=>{spin.value=clamp(value,r.spinLow,r.spinHigh).toFixed(r.decimals);range.value=clamp(value,r.low,r.high);fill();});
    }
    const draft=createNativeNumericDraft();nativeNumericDrafts.set(d.id,draft);
    function redraw(){const state=nativeNumericState.get(d.id);if(state)nativeNumericViews.get(d.id)?.(state);}
    function submit(candidate){
      if(candidate&&localValue(d.id,candidate.value,candidate.input)==='deferred')draft.retain(candidate);
      redraw();
    }
    function commit(n) {const value=percent&&n===spin?capturePercentToNative(n.value):n.value?Number(n.value):NaN;
      if(nativeNumericState.has(d.id)){submit(draft.finish(value,n===range?'slider':'expert'));return;}
      if(!Number.isFinite(value))return;spin.value=(clamp(value,r.spinLow,r.spinHigh)*factor).toFixed(displayDecimals);range.value=clamp(value,r.low,r.high);localValue(d.id,percent?capturePercentToNative(spin.value):Number(spin.value));fill();}
    range.addEventListener("input",()=>{
      if(!nativeNumericState.has(d.id)){commit(range);return;}
      draft.edit(Number(range.value),'slider',range.value);redraw();
    });
    range.addEventListener("change",()=>{if(nativeNumericState.has(d.id))commit(range);});
    spin.addEventListener("input",()=>{if(nativeNumericState.has(d.id)){
      draft.edit(percent?capturePercentToNative(spin.value):spin.value?Number(spin.value):NaN,'expert',spin.value);redraw();}});
    spin.addEventListener("change",()=>commit(spin));
    for(const input of [range,spin]){
      input.addEventListener('pointerdown',()=>draft.begin());
      input.addEventListener('pointercancel',()=>{draft.cancel();redraw();});
      input.addEventListener('keydown',event=>{if(event.key==='Escape'&&draft.snapshot()){
        event.preventDefault();draft.cancel();redraw();}
        else if(!event.repeat&&!draft.snapshot())draft.begin();});
    }
    const numberBox=node("div","vl-native-number-box"),stepper=node("div","vl-native-number-stepper");
    numberBox.append(spin);if(r.suffix)numberBox.append(copy(node("span","vl-native-number-suffix"),r.suffix));
    for(const [direction,source]of [[1,"Increase value"],[-1,"Decrease value"]]) {
      const button=copy(node("button",direction===1?"vl-native-number-up":"vl-native-number-down"),source,"aria-label");
      button.type="button";button.disabled=!d.enabled;
      button.tabIndex=-1;
      button.addEventListener("click",()=>{draft.begin();if(direction===1)spin.stepUp();else spin.stepDown();commit(spin);});stepper.append(button);
    }
    numberBox.append(stepper);
    const feedback=node('span','vl-live-numeric-feedback');feedback.hidden=true;
    feedback.id='vl-numeric-feedback-'+d.id.replace(/[^a-zA-Z0-9]/g,'-');
    feedback.dataset.vlNumericFeedback='true';feedback.setAttribute('role','status');
    const applyValue=node('button','vl-live-apply-value');applyValue.type='button';applyValue.hidden=true;
    applyValue.textContent=t('Apply value');let restoreInputFocus=null,lastDraftInput='expert';
    applyValue.addEventListener('click',()=>{const state=nativeNumericState.get(d.id),candidate=draft.snapshot();
      if(!candidate?.released||state?.disabled||!state?.record.enabled)return;
      submit(draft.finish(candidate.value,candidate.input));});
    applyValue.addEventListener('keydown',event=>{if(event.key==='Escape'&&draft.snapshot()){
      event.preventDefault();draft.cancel();redraw();}});
    control.append(range,numberBox,feedback,applyValue);fill();
    nativeNumericViews.set(d.id,state=>{
      draft.accept(state);const editing=draft.snapshot();
      if(editing)lastDraftInput=editing.input;
      const applyFocused=document.activeElement===applyValue;
      control.dataset.vlLiveNumeric=state.record.id;
      range.dataset.vlLiveField=state.record.id+':slider';spin.dataset.vlLiveField=state.record.id+':expert';
      range.min=state.spec.sliderMinimum;range.max=state.spec.sliderMaximum;
      spin.min=state.spec.minimum*factor;spin.max=(state.record.id==='numeric.capture_fps'?120:state.spec.maximum)*factor;
      const observed=state.record.value!==null;
      if(observed){const actual=Number(state.record.value);values.set(d.id,actual);
        const visible=editing?.value??actual;
        // Do not assign to a focused expert input while it holds a draft: number
        // inputs expose an empty value for incomplete text such as "1e". Writing
        // that empty value would erase the text/caret even on unrelated echoes.
        if(!editing||editing.input!=='expert')spin.value=(Number(visible)*factor).toFixed(percent?2:state.spec.decimals);
        range.value=clamp(Number.isFinite(visible)?visible:actual,Number(range.min),Number(range.max));fill();
        if(group)numericState.set(group,actual);}
      else{spin.value='';range.value='';if(group)numericState.delete(group);}
      for(const input of control.querySelectorAll('input,button')){
        input.disabled=(state.disabled&&!(state.holdDraft&&editing))||!state.record.enabled;
        input.title=state.record.enabled?'':state.reason;
        input.hidden=!observed||(percent&&input===range);input.setAttribute('aria-describedby',feedback.id+' live-field-message');}
      applyValue.textContent=t('Apply value');applyValue.hidden=!editing?.released;
      applyValue.disabled=state.disabled||!state.record.enabled||!editing?.released;
      if(applyFocused&&(applyValue.hidden||applyValue.disabled))restoreInputFocus=lastDraftInput==='slider'?range:spin;
      for(const old of control.querySelectorAll('.vl-live-unavailable:not([data-vl-numeric-unknown])'))old.remove();
      const actual=observed?Number(state.record.value):null;
      const guidance=state.record.id==='numeric.capture_fps'&&actual>120
        ?t('Use native workspace to edit capture rates above 120 fps')
        :state.record.id==='numeric.training_seed'&&actual>state.spec.maximum
          ?t('Use the slider to edit training seeds above 1,000,000'):'';
      const notice=draft.notice(),cancelled=notice==='changed'?t('Draft canceled because the setting or selection changed.')
        :notice==='unavailable'?t('Draft canceled because this control is unavailable.')
          :notice==='discarded'?t('Draft discarded.'):'';
      feedback.textContent=editing?.released?t('Not applied. Select Apply value to use this setting.')
        :editing?t('Draft — release the slider or finish editing to apply')
        :state.pending?t('Applying setting…'):cancelled
          ||(!state.record.enabled?state.reason:state.disabled?t('Finish the current operation before continuing'):guidance);
      feedback.hidden=!feedback.textContent;
      if(restoreInputFocus&&!restoreInputFocus.disabled&&restoreInputFocus.isConnected&&document.hasFocus()
        &&(document.activeElement===applyValue||document.activeElement===document.body)){
        restoreInputFocus.focus({preventScroll:true});restoreInputFocus=null;}
    });
    if(nativeNumericState.has(d.id))nativeNumericViews.get(d.id)(nativeNumericState.get(d.id));
    return control;
  }
  function registerDiscreteView(control,d,container) {
    const feedback=node('span','vl-live-discrete-feedback');feedback.hidden=true;
    feedback.id='vl-discrete-feedback-'+d.id.replace(/[^a-zA-Z0-9]/g,'-');
    feedback.setAttribute('role','status');container.append(feedback);
    control.setAttribute('aria-describedby',feedback.id+' live-field-message');
    nativeDiscreteViews.set(d.id,state=>{
      if(!state)return;const {record,spec}=state,observed=record.value!==null;
      container.dataset.vlLiveDiscrete=record.id;control.dataset.vlLiveField=record.id;
      control.disabled=state.disabled||state.terminal||!record.enabled;
      if(spec.kind==='boolean'){
        control.indeterminate=!observed;control.checked=observed?record.value:false;
        control.setAttribute('aria-checked',observed?String(record.value):'mixed');
      }else{
        if(control.options.length!==spec.choices.length)throw new Error('Invalid native choice display');
        for(let index=0;index<spec.choices.length;index++){
          const option=control.options[index];option.value=String(spec.choices[index]);
          option.disabled=!record.optionEnabled[index];
        }
        const value=observed?String(record.value):'';
        if(control.value!==value)control.value=value;
        if(!observed)control.selectedIndex=-1;
      }
      control.hidden=false;control.title=state.terminal||!record.enabled?state.reason:'';
      for(const old of container.querySelectorAll('.vl-live-unavailable'))old.remove();
      feedback.textContent=state.terminal?state.reason:!observed?t('No native observation'):state.pending?t('Applying setting…')
        :!record.enabled?state.reason:state.disabled?t('Finish the current operation before continuing'):'';
      feedback.hidden=!feedback.textContent;
    });
    if(nativeDiscreteState.has(d.id))nativeDiscreteViews.get(d.id)(nativeDiscreteState.get(d.id));
  }
  function annotationTabs(){return Object.values(descriptor).find(d=>d.attribute==='ultralytics_tabs'&&d.tabs?.titles.join('|')==='Annotation|Training');}
  function annotationDetails(d){const tabs=annotationTabs();return d.text==='View run details'&&!!tabs&&ancestry(d.id).includes(tabs.parent);}
  function revealAnnotationSection(index){
    if(!Number.isInteger(index)||index<0||index>3||disposed)return;
    const tabs=annotationTabs();if(!tabs)return;
    const attributes=['ultralytics_connection_section','ultralytics_annotation_section','ultralytics_effects_section','ultralytics_results_section'];
    const matches=Object.values(descriptor).filter(d=>ancestry(d.id).includes(tabs.tabs.pages[0])&&
      d.attribute===attributes[index]);
    if(matches.length!==1)return;
    const navigation=document.querySelector('select[data-vl-annotation-navigation]');
    if(navigation)navigation.value=String(index);
    revealSetting(matches[0].id,'dataset',ancestry(matches[0].id));
  }
  function renderNode(id,depth=0) {
    if (cache.has(id))return cache.get(id);
    const d=descriptor[id];if(!d||d.id!==id||depth>64)throw new Error("Invalid trusted presentation node");
    let n;
    if (d.tabs)n=renderTabs(d,depth);
    else if (d.range)n=renderRange(d);
    else if (d.scrollWidget) {n=node("div","vl-native-scroll");n.append(renderNode(d.scrollWidget,depth+1));}
    else if (d.stackItems) {
      n=node("div","vl-native-stack");const pages=d.stackItems.map(child=>renderNode(child,depth+1));
      pages.forEach((p,i)=>{p.hidden=i!==(d.currentIndex||0);n.append(p);});
    } else if (d.type==="QComboBox") {
      n=node("select");(d.items||[]).forEach((item,i)=>{const o=d.attribute==="language_choice"?node("option","",item):copy(node("option"),item);o.value=String(i);n.append(o);});
      n.value=String(d.attribute==="language_choice"?["en","zh-CN","de","ru","fr"].indexOf(currentLanguage):values.get(id)??d.index);
      if(d.attribute==='ultralytics_section_navigation'){
        n.setAttribute('data-vl-annotation-navigation','');copy(n,'Jump to section','aria-label');
        n.addEventListener('change',()=>revealAnnotationSection(Number(n.value)));
      }else inputState(n,d,"text");
      if (d.attribute==="language_choice")n.addEventListener("change",()=>setLanguage(["en","zh-CN","de","ru","fr"][Number(n.value)]||"en"));
      if(DISCRETE_CONTROL_IDS.has(id)){const select=n;n=node("div","vl-native-choice-field");n.append(select);registerDiscreteView(select,d,n);}
    } else if (d.type==="QCheckBox"||d.type==="QRadioButton") {
      n=node("label","vl-native-check");const check=node("input");check.type=d.type==="QCheckBox"?"checkbox":"radio";check.checked=values.get(id)??d.checked;
      inputState(check,d,"checked");n.append(check,copy(node("span"),d.text));
      if(DISCRETE_CONTROL_IDS.has(id))registerDiscreteView(check,d,n);
    } else if (d.type==="QPushButton"||d.type==="QToolButton") {
      n=copy(node("button"),d.objectName==="secondaryTabButton"&&d.text==="Policy"?"Targeting policy tab":d.text||d.accessibleName||d.tooltip);n.type="button";n.disabled=!d.enabled;
      if(annotationDetails(d)){n.setAttribute('data-vl-annotation-navigation','');n.disabled=false;}
      if(d.text==="…")n.classList.add("vl-native-compact-button");
      if(d.checkable)n.setAttribute("aria-pressed",String(d.checked));
      n.addEventListener("click",()=>{
        if(annotationDetails(d))revealAnnotationSection(3);
        else if (d.text==="Model library")showPage("model",true);
        else if (d.text==="Capture settings") {showPage("selection",true);showInspector(0);}
        else if (d.text==="Movement settings")showPage("output",true);
        else if(d.objectName==="secondaryTabButton"&&descriptor[d.parent]?.objectName==="policyInspector")showInspector(["Capture","Policy","Tuning"].indexOf(d.text));
        else if(policyTables.has(d.parent)&&["Add class","Remove selected","Apply rules"].includes(d.text))policyTables.get(d.parent)[{"Add class":"add","Remove selected":"remove","Apply rules":"apply"}[d.text]]();
        else if(["dataset_review_zoom_in","dataset_review_zoom_out","dataset_review_zoom_fit","dataset_review_delete"].includes(d.attribute)) {
          const preview=[...canvases].find(([id])=>descriptor[id].attribute==="dataset_review_preview")?.[1];
          if(preview) {
            if(d.attribute==="dataset_review_zoom_fit")preview.resetZoom();
            else if(d.attribute==="dataset_review_delete")preview.deleteSelected();
            else preview.setZoom(preview.zoomPercent()+(d.attribute==="dataset_review_zoom_in"?20:-20));
          }
        }
        else onAction(Object.freeze({controlId:id}));
      });
    } else if (d.type==="QLineEdit"||d.type==="QKeySequenceEdit") {
      n=node("input");n.type=d.echoMode===2?"password":"text";n.value=values.get(id)??d.text??"";n.readOnly=!!d.readOnly;copy(n,d.placeholder||"","placeholder");inputState(n,d,"text");
    } else if (d.type==="QDoubleSpinBox"||d.type==="QSpinBox") {
      n=node("div","vl-native-number-box vl-native-standalone-number");
      const spin=node("input"),stepper=node("div","vl-native-number-stepper");spin.type="number";spin.min=d.low;spin.max=d.high;spin.step=d.step;spin.value=values.get(id)??d.value;inputState(spin,d,"number");
      for(const [direction,source]of [[1,"Increase value"],[-1,"Decrease value"]]) {
        const button=copy(node("button",direction===1?"vl-native-number-up":"vl-native-number-down"),source,"aria-label");button.type="button";button.tabIndex=-1;button.disabled=!d.enabled;
        button.addEventListener("click",()=>{if(direction===1)spin.stepUp();else spin.stepDown();localValue(d.id,Number(spin.value));});stepper.append(button);
      }
      n.append(spin,stepper);
    } else if (d.type==="QPlainTextEdit"||d.type==="QTextEdit"||d.type==="QTextBrowser") {
      n=node("textarea");n.value=values.get(id)??d.text??"";n.readOnly=!!d.readOnly;inputState(n,d,"text");
    } else if (d.type==="QListWidget") {
      n=node("select","vl-native-list");n.size=6;(d.items||[]).forEach((item,i)=>{const o=copy(node("option"),item);o.value=String(i);n.append(o);});inputState(n,d,"text");
    } else if (d.type==="QTableWidget"&&descriptor[d.parent]?.type==="ClassPolicyEditor") {
      const parentId=d.parent;
      const table=createNativePolicyTable(document,{translate:t,onApply:rows=>localValue(parentId,rows),onStatus:source=>{
        const label=Object.values(descriptor).find(child=>child.parent===parentId&&child.type==="QLabel");
        if(label&&cache.has(label.id)){const status=cache.get(label.id);status.textContent=t(source);status.setAttribute("role","status");status.dataset.vlStatusSource=source;}
      }});policyTables.set(parentId,table);n=table.element;
    } else if (d.type==="QProgressBar") {
      n=node("div","vl-native-progress");
      const low=Number.isFinite(d.low)?d.low:0,high=Number.isFinite(d.high)&&d.high>low?d.high:100,value=clamp(Number(d.value)||0,low,high);
      n.setAttribute("role","progressbar");n.setAttribute("aria-valuemin",String(low));n.setAttribute("aria-valuemax",String(high));n.setAttribute("aria-valuenow",String(value));
      const fill=node("span","vl-native-progress-fill"),caption=node("span","vl-native-progress-caption");fill.style.width=`${100*(value-low)/(high-low)}%`;fill.setAttribute("aria-hidden","true");
      const parent=descriptor[d.parent];
      if(parent?.type==="ControllerTester") {
        const bars=parent.layout.items.filter(item=>descriptor[item.widget]?.type==="QProgressBar"),title=["Left trigger","Right trigger"][bars.findIndex(item=>item.widget===id)]||"Value";
        copy(n,title,"aria-label");caption.append(copy(node("span"),title),node("span","",`: ${value} / ${high}`));
      } else {copy(n,ownLabel(d)||"Progress","aria-label");caption.textContent=`${Math.round(100*(value-low)/(high-low))}%`;}
      n.append(fill,caption);
    }
    else if (d.type==="PreviewCanvas"||d.type==="DatasetReviewCanvas"||d.canvas) {
      const adapter=createNativeCanvas(document,{kind:d.type,translate:t,onAnnotations:annotations=>onValue(Object.freeze({controlId:id,value:annotations})),onZoom:percent=>{
        const label=[...cache.values()].find(n=>n.dataset.vlAttribute==="dataset_review_zoom_label");if(label)label.textContent=`${percent}%`;
        onValue(Object.freeze({controlId:id,presentationValue:"zoom",value:percent}));
      },onSelection:index=>onValue(Object.freeze({controlId:id,presentationValue:"selection",value:index}))});
      n=adapter.element;canvases.set(id,adapter);
    } else if (d.type==="QLabel") {
      n=copy(node(d.objectName==="pageTitle"?"h1":"span"),d.text);
      if (d.objectName==="pageTitle")n.tabIndex=-1;
    } else {n=node("div");if(d.layout)n.append(layout(d.layout,depth+1));}
    decorate(n,d);
    if(d.objectName==="metricCard") {
      const caption=n.querySelector(".vl-metricCaption");
      const title=descriptor[caption?.dataset.vlNode]?.text;
      const value=n.querySelector(".vl-metricValue"),colours={Pipeline:"#ffd173","Capture frame":"#7dbcf5","Output authority":"#b4bfcd",Initialization:"#ffb3c6"};
      if(value&&colours[title])value.style.color=colours[title];
    }
    if(d.objectName==="policyTopMetric"&&n.querySelector(".vl-policyConfidenceMetric"))n.classList.add("vl-native-metric-pink");
    if(d.objectName==="settingsCard"&&n.querySelector("[data-vl-attribute='dataset_review_accept']"))n.classList.add("vl-native-review-card");
    cache.set(id,n);return n;
  }
  function showInspector(index) {
    cancelNativeNumericDrafts();
    if(disposed)return;
    const stack=[...cache.values()].find(n=>n.dataset.vlAttribute==="targeting_inspector_stack");
    if (stack)[...stack.children].forEach((child,i)=>child.hidden=i!==index);
    const buttons=root.querySelectorAll(".vl-policyInspector>.vl-layout>.vl-layout>.vl-secondaryTabButton");
    buttons.forEach((button,i)=>button.setAttribute("aria-pressed",String(i===index)));
  }
  function showPage(route,focus=false) {
    if(disposed)return;
    cancelNativeNumericDrafts();
    if(route==="my-themes"){generatedViews.showThemes();return;}
    route=COMPONENT_ROUTES.get(route)||route;
    if (route==="capture")route="selection";
    if (!ROUTE_MAP.has(route)||(!presentation.pages[route]&&!["general","appearance"].includes(route))||disposed)return;
    currentPage=route;
    // Cached real DOM subtrees preserve controls, subtab choices, canvas state
    // and nested scroll positions. Responsive CSS never remounts these nodes.
    const native=!!presentation.pages[route],page=native?renderNode(presentation.pages[route]):generatedViews.page(route);
    if(!native&&!componentElements.has(ROUTE_MAP.get(route).component)) {
      componentElements.set(page.dataset.vlComponent,page);
      for(const element of page.querySelectorAll("[data-vl-component]")) {
        if(componentElements.has(element.dataset.vlComponent))throw new Error("Duplicate generated component");componentElements.set(element.dataset.vlComponent,element);
      }
    }
    if(native) {
      const anchor=composition.headers.find(header=>header.route===route),slot=headingSlots.get(route);
      if(!anchor||!slot)throw new Error("Missing fixed heading anchor");
      const kicker=descriptor[anchor.kicker],title=descriptor[anchor.title];
      headingKicker.className=`vl-q vl-${kicker.objectName}`;headingTitle.className=`vl-q vl-${title.objectName}`;
      headingKicker.style.fontSize=`calc(${kicker.font.pointSize*4/3}px * var(--vl-ui-scale) * var(--vl-accessibility-text-scale,1))`;
      headingTitle.style.fontSize=`calc(${title.font.pointSize*4/3}px * var(--vl-ui-scale) * var(--vl-accessibility-text-scale,1))`;
      headingKicker.textContent=t(kicker.text);headingTitle.textContent=t(title.text);slot.append(workspaceHeading);
      if(route==='selection'){page.insertBefore(slot,page.firstChild);slot.classList.add('vl-native-targeting-page-heading');}
    }else {
      const title=page.querySelector("h1"),kicker=title.previousElementSibling;
      // The generated page's original text remains its fallback. There is one
      // global heading view, reused at the active page's original title slot.
      const slot=title.parentElement;slot.dataset.vlHeaderRoute=route;headingSlots.set(route,slot);
      kicker.hidden=true;title.hidden=true;
      headingKicker.className="vl-sectionKicker";headingTitle.className="vl-pageTitle";
      headingKicker.style.removeProperty("font-size");headingTitle.style.removeProperty("font-size");
      headingKicker.textContent=t("SETTINGS");headingTitle.textContent=t(route==="general"?"General Settings":"Appearance");slot.append(workspaceHeading);
    }
    content.replaceChildren(page);nativePageHeading.textContent=t(ROUTE_MAP.get(route).label);
    for (const [key,button] of navButtons) {if(key===route)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");}
    if (focus)headingTitle.focus({preventScroll:true});
    refreshNumericMetrics();onNavigate(ROUTE_MAP.get(route).component);select(selected);measureFit();
  }
  function revealComponent(id,focus=true) {
    const rec=composition.component(id);if(!rec||disposed)return false;
    if(id==="my-themes")generatedViews.showThemes();
    else if(rec.route) {
      showPage(rec.route);
      if(rec.sourceRoots[0]) {
        const path=ancestry(rec.sourceRoots[0]);
        for(const [tabId,activate]of tabActivators) {
          const index=descriptor[tabId].tabs.pages.findIndex(page=>path.includes(page));if(index>=0)activate(index);
        }
      }
      if(rec.inspector)showInspector(["Capture","Policy","Tuning"].indexOf(rec.inspector));
    }else if(["general-settings","appearance"].includes(id))showPage(id);
    const element=componentElements.get(id);if(!element)return false;select(id);
    element.scrollIntoView({block:"nearest",inline:"nearest"});
    if(focus){element.tabIndex=-1;element.focus({preventScroll:true});}
    return true;
  }
  function select(id) {
    if (id!==null&&!definitions.has(id))return;selected=id;
    for (const n of [root,...root.querySelectorAll("[data-vl-component]")])n.classList.toggle("vl-selected",n.dataset.vlComponent===id);
  }
  root.addEventListener("click",event=>{
    if(!inspect)return;
    const component=event.target.closest("[data-vl-component]");if(!component||component===root)return;
    select(component.dataset.vlComponent);onSelect(component.dataset.vlComponent);
  });
  function setLanguage(code) {
    if(disposed)throw new Error("Renderer disposed");
    currentLanguage=LANGUAGES.has(code)?code:"en";app.lang=currentLanguage;
    for(const [id,n]of cache)if(descriptor[id].attribute==="language_choice")n.value=String(["en","zh-CN","de","ru","fr"].indexOf(currentLanguage));
    for (const [n,source,kind] of localized) {
      if (kind==="title")n.title=t(source);else if(kind==="placeholder")n.placeholder=t(source);else if(kind==="aria-label")n.setAttribute("aria-label",t(source));else n.textContent=t(source);
    }
    for(const table of policyTables.values())table.refreshLanguage();
    for(const [id,adapter]of canvases)if(descriptor[id].attribute==="dataset_review_preview") {
      const label=[...cache.values()].find(n=>n.dataset.vlAttribute==="dataset_review_zoom_label");if(label)label.textContent=`${adapter.zoomPercent()}%`;
    }
    for(const status of cache.values())if(status.dataset.vlStatusSource)status.textContent=t(status.dataset.vlStatusSource);
    generatedViews.refreshLanguage(currentLanguage);
    const anchor=composition.headers.find(header=>header.route===currentPage);
    headingKicker.textContent=t(anchor?descriptor[anchor.kicker].text:"SETTINGS");
    headingTitle.textContent=t(anchor?descriptor[anchor.title].text:currentPage==="general"?"General Settings":"Appearance");
    refreshNumericMetrics();nativePageHeading.textContent=t(ROUTE_MAP.get(currentPage).label);for(const adapter of canvases.values())adapter.draw();measureFit();
  }
  function measureFit() {
    const scale=Number.parseFloat(getComputedStyle(root).fontSize)/13.333333;
    root.classList.toggle("vl-compact",root.clientWidth/scale<980);
    app.classList.toggle("vl-command-compact",app.clientWidth/scale<1160);
    const command=app.querySelector(".vl-fixed-commandBar");
    if(command) {
      const measuring=document.createElement("canvas").getContext("2d"),context=command.querySelector(".vl-native-context"),style=getComputedStyle(command);
      const session=command.querySelector(".vl-sessionModeChoice"),sessionStyle=getComputedStyle(session);measuring.font=sessionStyle.font;
      const sessionText=measuring.measureText(session.selectedOptions[0]?.textContent||"").width;
      session.style.maxWidth="none";session.style.width=`${Math.ceil(Math.max(app.classList.contains("vl-command-compact")?105:124,sessionText+Number.parseFloat(sessionStyle.paddingLeft)+Number.parseFloat(sessionStyle.paddingRight)+2))}px`;
      const contextTitle=context.querySelector(".vl-headerPage");measuring.font=getComputedStyle(contextTitle).font;
      const contextWidth=measuring.measureText(contextTitle.textContent).width;
      const required=[...command.children].filter(child=>child!==context).reduce((sum,child)=>sum+child.getBoundingClientRect().width,0)+contextWidth+Number.parseFloat(style.columnGap)*(command.children.length-1)+Number.parseFloat(style.paddingLeft)+Number.parseFloat(style.paddingRight);
      app.classList.toggle("vl-command-wrapped",required>app.clientWidth+1||app.clientWidth/scale<900);
    }
    const cockpit=content.querySelector(".vl-native-cockpit");
    if(cockpit?.getClientRects().length) {
      const measuring=document.createElement("canvas").getContext("2d");
      let required=220*scale;
      for(const label of cockpit.querySelectorAll("button,.vl-metricCaption,.vl-metricValue")) {
        measuring.font=getComputedStyle(label).font;
        const words=label.classList.contains("vl-metricValue")?label.textContent.split(/\s+/):[label.textContent];
        required=Math.max(required,...words.map(word=>measuring.measureText(word).width+32));
      }
      cockpit.classList.toggle("vl-native-cockpit-two",cockpit.clientWidth<4*required+27);
    }
    const deck=content.querySelector(".vl-policyCommandDeck");
    root.style.setProperty("--vl-inspector-width",`${320*scale}px`);
    root.classList.toggle("vl-targeting-stacked",!!deck&&deck.clientWidth<880*scale);
    const review=content.querySelector(".vl-native-review-body");
    if(review?.getClientRects().length)review.classList.toggle("vl-native-review-stacked",review.clientWidth<792*scale);
  }
  function openSearch() {
    const dialog=node("dialog","vl-native-search");const title=node("h2","",t("Search settings")),input=node("input"),list=node("div","vl-native-search-results"),close=node("button","",t("Close"));
    input.type="search";input.setAttribute("aria-label",t("Search settings"));close.type="button";
    function results() {
      list.replaceChildren();const query=input.value.trim().toLocaleLowerCase();if(!query)return;
      let count=0;
      for (const d of Object.values(descriptor)) {
        if(d.objectName!=="fieldTitle"||!t(d.text||"").toLocaleLowerCase().includes(query)||count++>=50)continue;
        const path=ancestry(d.id),route=[...ROUTE_MAP.keys()].find(key=>path.includes(presentation.pages[key]));
        if(!route)continue;
        const button=node("button","",`${t(ROUTE_MAP.get(route).label)} / ${t(d.text)}`);button.type="button";
        button.addEventListener("click",()=>{dialog.close();revealSetting(d.id,route,path);});list.append(button);
      }
    }
    input.addEventListener("input",results);close.addEventListener("click",()=>dialog.close());dialog.addEventListener("close",()=>{dialog.remove();});
    dialog.append(title,input,list,close);app.append(dialog);dialog.showModal();input.focus();
  }
  function ancestry(id) {
    const path=[];for(let depth=0;id&&depth<64;depth++){path.push(id);id=descriptor[id]?.parent;}return path;
  }
  function revealSetting(id,route,path) {
    showPage(route);
    for(const [tabId,activate]of tabActivators) {
      const pages=descriptor[tabId].tabs.pages,index=pages.findIndex(page=>path.includes(page));if(index>=0)activate(index);
    }
    const stack=[...cache.keys()].find(key=>descriptor[key].attribute==="targeting_inspector_stack");
    if(stack) {const index=descriptor[stack].stackItems.findIndex(page=>path.includes(page));if(index>=0)showInspector(index);}
    const label=cache.get(id);if(!label)return;
    label.tabIndex=-1;label.scrollIntoView({block:"center",inline:"nearest"});label.focus({preventScroll:true});
  }
  app.addEventListener("keydown",event=>{if(chrome&&event.ctrlKey&&event.key.toLowerCase()==="k"){event.preventDefault();if(!app.querySelector("dialog[open]"))openSearch();}});
  const resizeObserver=new ResizeObserver(measureFit);resizeObserver.observe(root);resizeObserver.observe(content);
  function setAppearance(values) {
    if(disposed)throw new Error("Renderer disposed");
    if (!values||typeof values!=="object"||Array.isArray(values)||Object.keys(values).length>23)throw new Error("Invalid appearance");
    const prepared=Object.entries(values).map(([name,value])=>{const normalized=appearanceToken(name,value);if(normalized===null)throw new Error("Unsupported appearance token");return[name,normalized];});
    for(const [name,value]of prepared)root.style.setProperty(name,value);
    for(const [name,value]of Object.entries(values))appearanceValues.set(name,value);
    root.classList.toggle("vl-has-gradient",appearanceValues.has("--vl-gradient-start")||appearanceValues.has("--vl-gradient-end"));
    generatedViews.refreshAppearance(Object.fromEntries(appearanceValues),Object.keys(values));measureFit();onAppearance(Object.freeze({...values}));
  }
  function resetAppearance() {
    if(disposed)throw new Error("Renderer disposed");
    root.removeAttribute("style");appearanceValues.clear();root.classList.remove("vl-has-gradient");
    root.style.setProperty("--vl-accessibility-text-scale",String(textScale));generatedViews.refreshAppearance({});measureFit();
    onAppearance(Object.freeze({reset:true}));
  }
  function setAccessibility({textScale:value=1}={}) {
    if(disposed)throw new Error("Renderer disposed");
    if(![1,1.5,2].includes(value))throw new Error("Unsupported text scale");textScale=value;
    root.style.setProperty("--vl-accessibility-text-scale",String(value));app.style.setProperty("--vl-accessibility-text-scale",String(value));
    generatedViews.refreshAccessibility(value);measureFit();
  }
  content.tabIndex=-1;app.lang=currentLanguage;showPage("overview");
  function cancelNativeNumericDrafts(){
    for(const draft of nativeNumericDrafts.values())draft.cancel();
    for(const [id,state]of nativeNumericState)nativeNumericViews.get(id)?.(state);
  }
  return Object.freeze({
    stage:"native-component-presentation-port",showPage,select,revealComponent,setLanguage,setAppearance,resetAppearance,setAccessibility,
    setPublicState(value){if(disposed)throw new Error("Renderer disposed");generatedViews.setPublicState(value);},
    refreshNativeNumericMetrics(){if(disposed)throw new Error('Renderer disposed');refreshNumericMetrics();},
    cancelNativeNumericDrafts,
    setNativeDiscreteState(records,bindings,specs,{disabled=false,terminal=false,pendingBinding=null,reason=()=>''}={}){
      if(disposed||!Array.isArray(records)||records.length>128||!(bindings instanceof Map)||!(specs instanceof Map))throw new Error('Invalid native discrete display');
      const byId=new Map(records.map(record=>[record.id,record]));
      for(const [controlId,bindingId]of bindings){
        const record=byId.get(bindingId),spec=specs.get(bindingId),d=descriptor[controlId];
        if(!DISCRETE_CONTROL_IDS.has(controlId)||!record||!spec||
          (spec.kind==='boolean'?d?.type!=='QCheckBox':d?.type!=='QComboBox'||d.items?.length!==spec.choices.length))throw new Error('Invalid native discrete mapping');
        const state={record,spec,disabled,terminal,pending:bindingId===pendingBinding,reason:reason(record)};
        nativeDiscreteState.set(controlId,state);nativeDiscreteViews.get(controlId)?.(state);
      }
    },
    setNativeNumericState(records,bindings,specs,{disabled=false,holdDraft=false,pendingField=null,target='',reason=()=>''}={}){
      if(disposed||!Array.isArray(records)||records.length>128||!(bindings instanceof Map)||!(specs instanceof Map))throw new Error('Invalid native numeric display');
      const byId=new Map(records.map(record=>[record.id,record]));
      for(const [controlId,fieldId]of bindings){
        const record=byId.get(fieldId),spec=specs.get(fieldId);
        if(!descriptor[controlId]?.range||!record||!spec)throw new Error('Invalid native numeric mapping');
        const state={record,spec,disabled,holdDraft,pending:fieldId===pendingField,target,reason:reason(record)};nativeNumericState.set(controlId,state);
        if(record.value!==null)values.set(controlId,Number(record.value));
        nativeNumericViews.get(controlId)?.(state);
      }
    },
    componentAudit(){return Object.freeze({...composition.audit,renderedComponents:componentElements.size,
      renderedIds:Object.freeze([...componentElements.keys()]),newViewsImplemented:"presentation only; theme management disconnected"});},
    dispose(){cancelNativeNumericDrafts();disposed=true;resizeObserver.disconnect();for(const adapter of canvases.values())adapter.dispose();mount.replaceChildren();cache.clear();componentElements.clear();headingSlots.clear();generatedViews.dispose();localized.length=0;
      canvases.clear();policyTables.clear();numericViews.clear();numericGroups.clear();numericState.clear();nativeNumericState.clear();nativeNumericViews.clear();nativeNumericDrafts.clear();nativeDiscreteState.clear();nativeDiscreteViews.clear();values.clear();tabs.clear();tabActivators.clear();appearanceValues.clear();},
  });
}
