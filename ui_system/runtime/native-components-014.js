/* Fixed native source grouping. This is build-owned presentation metadata.
 * A theme cannot select source nodes, routes, operations or this factory input.
 * Preparation checks the actual immutable bytes before parsing or branding.
 */
const PINS=Object.freeze({
  map:"673635b43e4899332e957fc7616f2b6910a81c40b445601c5438105c9ac2e1e6",
  registry:"22a3c761d9afc132e6307c2398cbbcee4b8f57ab783daf67b1f8db53dfe50891",
  presentation:"7de01ca76c1184b8bd9856cf35840b85db77f2257dcf6f556e55a9a2bec4e8b9",
});
const LIMITS={map:32768,registry:131072,presentation:3145728};
const plans=new WeakMap();
const byteLength=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype),"byteLength").get;
const fail=()=>{throw new Error("Invalid fixed native component resources");};
function freeze(value) {
  if(value&&typeof value==="object"&&!Object.isFrozen(value)) {
    for(const item of Object.values(value))freeze(item);Object.freeze(value);
  }
  return value;
}
async function pinnedJSON(input,key) {
  if(!input||Object.getPrototypeOf(input)!==Uint8Array.prototype)fail();
  const length=byteLength.call(input);
  if(!length||length>LIMITS[key])fail();
  const copy=new Uint8Array(length);copy.set(input);
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",copy)),n=>n.toString(16).padStart(2,"0")).join("");
  if(digest!==PINS[key])fail();
  return JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(copy));
}
function widgetRoots(layout) {
  const result=[];
  for(const item of layout.items) {
    if(item.widget)result.push(item.widget);
    if(item.layout)result.push(...widgetRoots(item.layout));
  }
  return result;
}

export async function prepareNativeComponentPlan({mapBytes,registryBytes,presentationBytes}={}) {
  const [map,registry,presentation]=await Promise.all([
    pinnedJSON(mapBytes,"map"),pinnedJSON(registryBytes,"registry"),pinnedJSON(presentationBytes,"presentation"),
  ]);
  if(map.mapVersion!==1||registry.componentRegistryVersion!==1||presentation.descriptorVersion!==1
    ||map.registrySHA256!==PINS.registry||map.descriptorSHA256!==PINS.presentation
    ||map.completeOperationalBindings!==false||map.mediaCSSLayoutActivation!==false
    ||map.components.length!==49||registry.components.length!==49)fail();
  const nodes=presentation.nodes,records=new Map(map.components.map(c=>[c.id,c]));
  const definitions=new Map(registry.components.map(c=>[c.id,c]));
  if(records.size!==49||definitions.size!==49)fail();
  const layouts=new Map(),roots=new Map(),groups=new Map(),headers=new Map();
  function walk(layout) {
    if(!layout)return;
    if(layouts.has(layout.id)||!Array.isArray(layout.items)||layout.items.length>512)fail();
    layouts.set(layout.id,layout);for(const item of layout.items)if(item.layout)walk(item.layout);
  }
  for(const d of Object.values(nodes))if(d.layout)walk(d.layout);
  for(const rec of records.values()) {
    const def=definitions.get(rec.id);
    if(!def||rec.defaultParent!==def.defaultParent||rec.originPage!==def.originPage)fail();
    const seen=new Set();let parent=rec.id;
    while(parent!==null) {
      if(seen.has(parent)||!records.has(parent))fail();seen.add(parent);parent=records.get(parent).defaultParent;
    }
    for(const source of rec.sourceRoots) {
      const page=presentation.pages[rec.route];
      if(!nodes[source]||!page||!(source===page||source.startsWith(page+"/"))||roots.has(source))fail();
      roots.set(source,rec.id);
    }
    if(rec.sourceRoots.length<2)continue;
    const selected=new Set(rec.sourceRoots),parents=new Set(rec.sourceRoots.map(id=>nodes[id].parent));
    if(parents.size!==1)fail();
    let found=null;
    for(const layout of layouts.values()) {
      const matching=layout.items.map((item,index)=>{
        const members=item.widget?[item.widget]:item.layout?widgetRoots(item.layout):[];
        return members.length&&members.every(id=>selected.has(id))?{index,members}:null;
      }).filter(Boolean);
      const covered=new Set(matching.flatMap(item=>item.members));
      if(covered.size!==selected.size||[...selected].some(id=>!covered.has(id)))continue;
      const first=matching[0].index,last=matching.at(-1).index;
      if(last-first+1!==matching.length||layout.type!=="QVBoxLayout"||found)fail();
      found={layout:layout.id,first,last,id:rec.id};
    }
    if(!found)fail();
    if(!groups.has(found.layout))groups.set(found.layout,[]);groups.get(found.layout).push(found);
  }
  for(const ranges of groups.values()) {
    ranges.sort((a,b)=>a.first-b.first);
    if(ranges.some((range,i)=>i&&range.first<=ranges[i-1].last))fail();
  }
  for(const [route,anchor]of Object.entries(map.workspaceHeaderAnchors)) {
    const [kicker,title]=anchor.suppressDefaultTextSources;
    const candidates=[...layouts.values()].filter(l=>l.items.some((item,i)=>item.widget===kicker&&l.items[i+1]?.widget===title));
    if(candidates.length!==1||nodes[kicker].parent!==anchor.slotSource||nodes[title].parent!==anchor.slotSource
      ||anchor.headerOwnsSources!==false||anchor.descriptionAndTelemetryRemainPageOwned!==true)fail();
    const layout=candidates[0],first=layout.items.findIndex(item=>item.widget===kicker);
    // Targeting inserts at the fixed layout captured from the current native source.
    // Its policyMainSurface remains a measurement ancestor, never replaced.
    if(route==="selection"&&layout.id!=="layout-513")fail();
    headers.set(layout.id,{route,first,last:first+1,kicker,title,slotSource:anchor.slotSource});
  }
  const owners=new Map();
  for(const [source,d]of Object.entries(nodes)) {
    let cursor=source;
    for(let depth=0;cursor&&depth<64;depth++,cursor=nodes[cursor]?.parent) {
      if(roots.has(cursor)){owners.set(source,roots.get(cursor));break;}
    }
    if(Object.values(presentation.pages).some(page=>source===page||source.startsWith(page+"/"))&&!owners.has(source))fail();
  }
  const api=freeze({
    presentation,registry,records:map.components,headers:[...headers.values()],
    rootComponent:source=>roots.get(source)||null,
    sourceOwner:source=>owners.get(source)||null,
    component:id=>records.get(id)||null,
    layoutItems(layout) {
      if(layouts.get(layout.id)!==layout)fail();
      const ranges=groups.get(layout.id)||[],heading=headers.get(layout.id),result=[];
      for(let index=0;index<layout.items.length;index++) {
        const group=ranges.find(range=>range.first===index);
        if(group) {
          result.push({componentGroup:group.id,layout:{...layout,id:`component-${group.id}`,
            margins:[0,0,0,0],items:layout.items.slice(group.first,group.last+1)}});
          index=group.last;
        }else if(heading?.first===index) {
          result.push({headingSlot:heading});index=heading.last;
        }else result.push(layout.items[index]);
      }
      return result;
    },
    audit:freeze({components:49,sourceNodes:owners.size,nativeGroups:map.components.filter(c=>c.sourceRoots.length).length,
      combinedGroups:[...groups.values()].flat().length,headerSlots:headers.size,protectedSources:0,
      completeOperationalBindings:false,mediaCSSLayoutActivation:false}),
  });
  const brand=Object.freeze({stage:"fixed-native-component-plan",pins:PINS});plans.set(brand,api);return brand;
}

export function useNativeComponentPlan(brand) {
  const plan=plans.get(brand);if(!plan)fail();return plan;
}
