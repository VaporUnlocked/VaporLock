/* Trusted generated views. Edits are local presentation preferences only.
 * No theme decoder, filesystem, licence, engine or operational adapter here.
 */
const LANGUAGES=[["en","English"],["zh-CN","简体中文"],["de","Deutsch"],["ru","Русский"],["fr","Français"]];
const COLOURS=[
  ["--vl-accent","Accent color","#7dbcf5"],["--vl-accent-secondary","Secondary accent","#7dbcf5"],
  ["--vl-background","Background color","#080b12"],["--vl-panel","Panel color","rgba(8,11,18,.678431)"],
  ["--vl-text","Text color","#f2f6fa"],["--vl-text-muted","Muted text","#b4bfcd"],
  ["--vl-border","Border color","#708498"],["--vl-shadow-color","Shadow color","rgba(0,0,0,0)"],
  ["--vl-glow-color","Glow color","rgba(125,188,245,0)"],["--vl-gradient-start","Gradient start","#080b12"],
  ["--vl-gradient-end","Gradient end","#7dbcf5"],
];
const NUMBERS=[
  ["--vl-radius","Corner radius",4,0,40,1,"px"],["--vl-blur","Blur",0,0,40,1,"px"],
  ["--vl-spacing","Spacing",9,4,24,1,"px"],["--vl-ui-scale","UI scale",1,.75,1.5,.05,""],
  ["--vl-border-width","Border width",1,0,6,1,"px"],["--vl-shadow-blur","Shadow blur",0,0,64,1,"px"],
  ["--vl-shadow-spread","Shadow spread",0,0,16,1,"px"],["--vl-glow-blur","Glow blur",0,0,40,1,"px"],
  ["--vl-glass-strength","Glass strength",.68,.1,1,.01,""],
  ["--vl-animation-intensity","Animation intensity",0,0,1,.05,""],
  ["--vl-gradient-angle","Gradient angle",135,0,360,1,"deg"],
];
const CONNECTIONS=new Map([["capture","Capture source"],["output","Output device"],["licensing","Licensing service"],["training","Training runtime"]]);
const STATES=new Map([["unknown","Unknown"],["offline","Disconnected"],["connecting","Connecting"],["connected","Connected"]]);
const publicError=()=>{throw new Error("Invalid public presentation state");};
function keys(value,allowed) {
  if(!value||typeof value!=="object"||![Object.prototype,null].includes(Object.getPrototypeOf(value))
    ||Reflect.ownKeys(value).some(key=>typeof key!=="string"||!allowed.includes(key))
    ||Object.values(Object.getOwnPropertyDescriptors(value)).some(d=>!("value" in d)))publicError();
}
export function publicState(value) {
  keys(value,["profile","connections"]);keys(value.profile,["state","name"]);
  const profile=value.profile;
  if(!["unknown","none","selected"].includes(profile.state))publicError();
  if(profile.state==="selected"&&(typeof profile.name!=="string"||!profile.name.trim()||profile.name.length>128||/[\u0000-\u001f\u007f]/.test(profile.name)))publicError();
  if(profile.state!=="selected"&&profile.name!==undefined)publicError();
  if(!Array.isArray(value.connections)||Object.getPrototypeOf(value.connections)!==Array.prototype||value.connections.length>4
    ||Object.keys(value.connections).length!==value.connections.length
    ||Reflect.ownKeys(value.connections).some(key=>key!=="length"&&!/^[0-3]$/.test(key))
    ||Object.values(Object.getOwnPropertyDescriptors(value.connections)).some(d=>!("value" in d)))publicError();
  const seen=new Set(),connections=Array.prototype.map.call(value.connections,item=>{
    keys(item,["id","state"]);
    if(!CONNECTIONS.has(item.id)||!STATES.has(item.state)||seen.has(item.id))publicError();seen.add(item.id);
    return Object.freeze({id:item.id,state:item.state});
  });
  return Object.freeze({profile:Object.freeze(profile.state==="selected"?{state:"selected",name:profile.name}:{state:profile.state}),connections:Object.freeze(connections)});
}

export function createNativeComponentViews({document,node,copy,translate,setLanguage,setAccessibility,setAppearance,resetAppearance,showPage,initialLanguage="en",initialTextScale=1}) {
  if(!LANGUAGES.some(([code])=>code===initialLanguage)||![1,1.5,2].includes(initialTextScale))throw new Error("Invalid initial presentation preferences");
  const pageCache=new Map(),appearanceInputs=new Map();
  let info=publicState({profile:{state:"unknown"},connections:[]});
  let language=initialLanguage,textScale=initialTextScale,appearanceOverrides={};
  let appearanceStatus=null;
  function refreshAppearanceStatus() {
    if(!appearanceStatus)return;
    appearanceStatus.hidden=![...appearanceInputs.values()].some(({input})=>input.getAttribute("aria-invalid")==="true");
    if(!appearanceStatus.hidden)appearanceStatus.textContent=translate("Enter a supported value.");
  }
  const infoRow=node("div","vl-generated-info-row");
  const profileCard=node("section","vl-generated-card"),profileValue=node("strong","vl-generated-info-value");
  profileCard.dataset.vlComponent="profile-card";profileCard.append(copy(node("h2"),"Active profile"),profileValue);
  const connectionCard=node("section","vl-generated-card"),connectionEmpty=node("p","vl-generated-muted"),connectionList=node("dl","vl-generated-connections");
  connectionCard.dataset.vlComponent="connection-status";connectionCard.append(copy(node("h2"),"Connections"),connectionEmpty,connectionList,
    copy(node("p","vl-generated-muted"),"Output permission does not indicate a connection."));
  const connectionViews=new Map();
  for(const [id,label]of CONNECTIONS) {
    const row=node("div"),state=node("dd");row.append(copy(node("dt"),label),state);row.hidden=true;connectionList.append(row);connectionViews.set(id,{row,state});
  }
  infoRow.append(profileCard,connectionCard);
  function refreshPublic() {
    profileValue.textContent=info.profile.state==="selected"?info.profile.name:translate(info.profile.state==="none"?"No active profile":"Unknown");
    connectionEmpty.textContent=translate("No connection information");connectionEmpty.hidden=info.connections.length>0;
    for(const [id,view]of connectionViews) {
      const item=info.connections.find(c=>c.id===id);view.row.hidden=!item;
      if(item)view.state.textContent=translate(STATES.get(item.state));
    }
  }
  function field(parent,source,input,description=null) {
    const row=node("label","vl-generated-field"),caption=node("span","vl-generated-field-copy");
    caption.append(copy(node("strong"),source));if(description)caption.append(copy(node("small","vl-generated-muted"),description));
    copy(input,source,"aria-label");
    if(description){const help=caption.querySelector("small");help.id=`vl-appearance-help-${source.replace(/[^a-zA-Z0-9]/g,"-")}`;input.setAttribute("aria-describedby",help.id);}
    row.append(caption,input);parent.append(row);return row;
  }
  function page(component) {
    const scroll=node("div","vl-native-scroll vl-pageScroll vl-generated-scroll"),body=node("div","vl-generated-page");
    scroll.dataset.vlComponent=component;scroll.append(body);return {scroll,body};
  }
  function title(body,kicker,source) {
    const heading=node("div","vl-generated-page-heading");heading.append(copy(node("span","vl-sectionKicker"),kicker),copy(node("h1","vl-pageTitle"),source));
    heading.querySelector("h1").tabIndex=-1;body.append(heading);
  }
  function general() {
    if(pageCache.has("general"))return pageCache.get("general");
    const {scroll,body}=page("general-settings");title(body,"SETTINGS","General Settings");
    body.append(copy(node("p","vl-generated-muted"),"Language and text size apply across the interface."));
    const card=node("section","vl-generated-card"),languageSelect=node("select"),sizeSelect=node("select");
    for(const [code,label]of LANGUAGES){const option=node("option","",label);option.value=code;languageSelect.append(option);}
    for(const scale of [1,1.5,2]){const option=node("option","",`${scale*100}%`);option.value=String(scale);sizeSelect.append(option);}
    languageSelect.value=language;sizeSelect.value=String(textScale);
    languageSelect.addEventListener("change",()=>setLanguage(languageSelect.value));
    sizeSelect.addEventListener("change",()=>setAccessibility({textScale:Number(sizeSelect.value)}));
    field(card,"Language",languageSelect);field(card,"Text size",sizeSelect,"Text size is a personal accessibility preference.");
    body.append(card);pageCache.set("general",scroll);return scroll;
  }
  function appearance() {
    if(pageCache.has("appearance"))return pageCache.get("appearance");
    const {scroll,body}=page("appearance");title(body,"SETTINGS","Appearance");
    body.append(copy(node("p","vl-generated-muted"),"Customize the Smokey Glass foundation."));
    const rail=node("div","vl-native-tab-rail"),panels=node("div","vl-generated-tab-panels"),buttons=[],contents=[];
    rail.setAttribute("role","tablist");
    const editor=node("section","vl-generated-card"),themes=node("section","vl-generated-card");themes.dataset.vlComponent="my-themes";
    [editor,themes].forEach((panel,index)=>{
      const source=index===0?"Appearance":"My Themes",button=copy(node("button","vl-secondaryTabButton"),source);
      button.type="button";button.id=`vl-appearance-tab-${index}`;button.setAttribute("role","tab");panel.id=`${button.id}-panel`;
      button.setAttribute("aria-controls",panel.id);panel.setAttribute("role","tabpanel");panel.setAttribute("aria-labelledby",button.id);
      button.addEventListener("click",()=>activate(index));button.addEventListener("keydown",event=>{
        if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();activate(event.key==="Home"?0:event.key==="End"?1:1-index,true);
      });buttons.push(button);contents.push(panel);rail.append(button);panels.append(panel);
    });
    function activate(index,focus=false) {
      buttons.forEach((button,i)=>{button.setAttribute("aria-selected",String(index===i));button.tabIndex=index===i?0:-1;});
      contents.forEach((panel,i)=>{panel.hidden=index!==i;});if(focus)buttons[index].focus();
    }
    const status=node("p","vl-generated-muted");status.id="vl-appearance-value-status";status.setAttribute("role","status");status.hidden=true;
    appearanceStatus=status;
    function commit(token,input,value) {
      try{setAppearance({[token]:value});input.removeAttribute("aria-invalid");input.removeAttribute("aria-errormessage");refreshAppearanceStatus();}
      catch(_error){input.setAttribute("aria-invalid","true");input.setAttribute("aria-errormessage",status.id);status.textContent=translate("Enter a supported value.");status.hidden=false;}
    }
    for(const [token,label,fallback]of COLOURS) {
      const input=node("input");input.type="text";input.maxLength=80;input.value=appearanceOverrides[token]??fallback;
      input.addEventListener("input",()=>{try{setAppearance({[token]:input.value.trim()});}catch(_error){/* Keep the last valid appearance while text is incomplete. */}});
      input.addEventListener("change",()=>commit(token,input,input.value.trim()));field(editor,label,input,"Use #RRGGBB, #RRGGBBAA, or rgba(red, green, blue, alpha).");appearanceInputs.set(token,{input,fallback,suffix:""});
    }
    for(const [token,label,fallback,low,high,step,suffix]of NUMBERS) {
      const input=node("input");input.type="number";input.min=String(low);input.max=String(high);input.step=String(step);
      input.value=String(Number.parseFloat(appearanceOverrides[token]??fallback));
      input.addEventListener("input",()=>{if(!input.value.trim())return;try{setAppearance({[token]:suffix?`${Number(input.value)}${suffix}`:Number(input.value)});}catch(_error){/* Incomplete/out-of-range edits retain the previous valid value. */}});
      input.addEventListener("change",()=>commit(token,input,!input.value.trim()?null:suffix?`${Number(input.value)}${suffix}`:Number(input.value)));
      const units=suffix==="px"?"Pixels":suffix==="deg"?"Degrees":"Ratio";
      const description=node("span","vl-generated-muted");description.append(copy(node("span"),units),node("span","",` · ${low}–${high}`));
      const row=field(editor,label,input);row.firstElementChild.append(description);description.id=`vl-appearance-range-${label.replace(/[^a-zA-Z0-9]/g,"-")}`;input.setAttribute("aria-describedby",description.id);
      appearanceInputs.set(token,{input,fallback,suffix});
    }
    const font=node("select");for(const family of ["Segoe UI Variable","Segoe UI","Arial","Tahoma","Verdana"]){const option=node("option","",family);option.value=family;font.append(option);}
    font.value=appearanceOverrides["--vl-font"]??"Segoe UI Variable";
    font.addEventListener("change",()=>commit("--vl-font",font,font.value));field(editor,"Font",font);appearanceInputs.set("--vl-font",{input:font,fallback:"Segoe UI Variable",suffix:""});
    const restore=copy(node("button"),"Restore appearance");restore.type="button";restore.addEventListener("click",resetAppearance);editor.append(status,restore);
    themes.append(copy(node("h2"),"My Themes"),copy(node("p","vl-generated-muted"),"No themes loaded"),copy(node("p","vl-generated-muted"),"Theme management is unavailable in this preview."));
    const actions=node("div","vl-generated-actions");
    for(const source of ["Scan themes","Import .vlui","Export .vlui"]) {const button=copy(node("button"),source);button.type="button";button.disabled=true;actions.append(button);}
    themes.append(actions);body.append(rail,panels);scroll.showThemes=()=>activate(1);activate(0);pageCache.set("appearance",scroll);return scroll;
  }
  refreshPublic();
  return Object.freeze({
    overviewRow:infoRow,
    page:route=>route==="general"?general():appearance(),
    showThemes(){showPage("appearance",true);appearance().showThemes();},
    setPublicState(value){const next=publicState(value);info=next;refreshPublic();},
    refreshLanguage(code){language=code;const select=pageCache.get("general")?.querySelector("select");if(select)select.value=code;refreshAppearanceStatus();refreshPublic();},
    refreshAccessibility(value){textScale=value;const selects=pageCache.get("general")?.querySelectorAll("select");if(selects)selects[1].value=String(value);},
    refreshAppearance(values,changed=null){appearanceOverrides={...values};for(const [token,{input,fallback,suffix}]of appearanceInputs){if(changed&&!changed.includes(token))continue;const value=values[token]??fallback;if(document.activeElement!==input)input.value=input.type==="number"?String(Number.parseFloat(value)):String(value);input.removeAttribute("aria-invalid");input.removeAttribute("aria-errormessage");}refreshAppearanceStatus();},
    dispose(){pageCache.clear();appearanceInputs.clear();connectionViews.clear();},
  });
}
