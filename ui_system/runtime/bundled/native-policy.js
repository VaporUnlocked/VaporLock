/* Explicit presentation adapter for tuning_tools.ClassPolicyEditor.
 * Rows stay local until Apply rules. No dynamic field names or native dispatch.
 */
const FIELDS=["class_id","priority","anchor_mode","anchor_x","anchor_y"];
const TITLES=["Class ID","Priority","Anchor","X offset","Y offset"];
const ANCHORS=["center","top","bottom","upper-quarter"];
export function createNativePolicyTable(document,{translate=source=>source,onApply=()=>{},onStatus=()=>{}}={}) {
  const surface=document.createElement("div"),table=document.createElement("table"),head=document.createElement("thead"),body=document.createElement("tbody"),header=document.createElement("tr");
  surface.className="vl-native-policy-table";surface.tabIndex=0;table.append(head,body);head.append(header);surface.append(table);
  const rows=[];let selected=-1;
  for(const title of TITLES) {const cell=document.createElement("th");cell.scope="col";cell.dataset.source=title;header.append(cell);}
  function choose(index) {selected=index;rows.forEach((row,i)=>{row.element.classList.toggle("vl-native-row-selected",i===index);row.element.setAttribute("aria-selected",String(i===index));});}
  function add(value) {
    if(rows.length>=128)return;
    const used=new Set(rows.map(row=>Number(row.controls[0].value)));
    if(!value) {let next=0;while(used.has(next))next++;value={class_id:next,priority:0,anchor_mode:"center",anchor_x:0,anchor_y:0};}
    const element=document.createElement("tr"),controls=[];
    for(let i=0;i<FIELDS.length;i++) {
      const cell=document.createElement("td"),control=document.createElement(i===2?"select":"input");
      if(i===2)for(const anchor of ANCHORS) {const option=document.createElement("option");option.value=anchor;option.textContent=anchor;control.append(option);}
      else {control.type="number";control.min=i===0?"0":i===1?"-1000000":"-1";control.max=i<2?"1000000":"1";control.step=i<2?"1":"0.001";}
      control.value=String(value[FIELDS[i]]??0);control.dataset.source=TITLES[i];
      control.addEventListener("focus",()=>choose(rows.findIndex(row=>row.element===element)));
      cell.append(control);element.append(cell);controls.push(control);
    }
    element.addEventListener("click",()=>choose(rows.findIndex(row=>row.element===element)));
    rows.push({element,controls});body.append(element);refreshLanguage();choose(rows.length-1);
  }
  function validatedRows() {
    const seen=new Set();return rows.map(({controls})=>{
      const value={class_id:Number(controls[0].value),priority:Number(controls[1].value),anchor_mode:controls[2].value,anchor_x:Number(controls[3].value),anchor_y:Number(controls[4].value)};
      if(controls.some(control=>!control.checkValidity())||!Number.isInteger(value.class_id)||value.class_id<0||value.class_id>1000000||seen.has(value.class_id))throw new Error("Class IDs must be unique integers between 0 and 1000000.");
      if(!Number.isInteger(value.priority)||value.priority< -1000000||value.priority>1000000||!ANCHORS.includes(value.anchor_mode))throw new Error("Invalid priority or anchor mode.");
      if(![value.anchor_x,value.anchor_y].every(n=>Number.isFinite(n)&&n>=-1&&n<=1))throw new Error("Class anchor offsets must be finite values between -1 and 1.");
      seen.add(value.class_id);return value;
    });
  }
  function remove() {if(selected<0)return;rows[selected].element.remove();rows.splice(selected,1);choose(Math.min(selected,rows.length-1));}
  function apply() {try{const value=validatedRows();onApply(value);onStatus("Rules applied");}catch(error){onStatus(error.message);}}
  function refreshLanguage() {
    surface.setAttribute("aria-label",translate("Class rules"));
    for(const cell of header.children)cell.textContent=translate(cell.dataset.source);
    for(const row of rows)for(const control of row.controls) {
      control.setAttribute("aria-label",translate(control.dataset.source));
      if(control.tagName==="SELECT")for(const option of control.options)option.textContent=translate(option.value);
    }
  }
  refreshLanguage();return Object.freeze({element:surface,add,remove,apply,refreshLanguage});
}
