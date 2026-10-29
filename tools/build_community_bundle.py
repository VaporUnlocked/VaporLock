"""Freeze one unrestricted portable client, reusing verified vendor dependencies.

The dependency source must be a separately obtained portable package. Only its
manifest-listed runtime/cloud and fixed SAM resources are admitted; no settings,
models, profiles, credentials, old GUI or old workers are imported.
"""
from __future__ import annotations
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
import argparse, ast, ctypes, hashlib, json, os, re, shutil, struct, subprocess, sys, time, uuid, zipfile

ROOT=Path(__file__).resolve().parents[1]
RETIRED=frozenset(('licensing','license_monitor','license_recovery','offline_lease','worker_entitlement',
    'start_policy','update_notifier','statistics_reporter','official_models','release_client','catalog_state',
    'release_archive','installer_terms'))
KINDS=('common','cpu','dml','cuda','sdk','sam')
PROVIDER_FOLDERS={'common':'common-opencv-v2','cpu':'cpu','dml':'dml','cuda':'cuda'}
VC_NAMES=('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll',
          'msvcp140_codecvt_ids.dll','concrt140.dll')

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def plain(path):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        if item.exists() or item.is_symlink():
            info=item.lstat()
            if item.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Linked packaging path rejected.')
    return path

def relative(value):
    if type(value) is not str or not value or '\\' in value or ':' in value or value.startswith('/'):
        raise ValueError('Invalid package member.')
    parsed=PurePosixPath(value)
    if len(value)>1024 or parsed.as_posix()!=value or any(p in ('','.','..') or p.endswith(('.',' ')) or any(ord(c)<32 or c in '<>\"|?*' for c in p) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p,re.I) for p in parsed.parts):
        raise ValueError('Ambiguous package member.')
    return value

def write(path,value):
    path=plain(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode('utf8')
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if path.read_bytes()!=raw:raise RuntimeError('Build record readback failed.')
    return hashlib.sha256(raw).hexdigest()

def copy_verified(source,target,entry):
    source=plain(source);target=plain(target)
    if not source.is_file() or source.stat().st_size!=entry['bytes'] or sha(source)!=entry['sha256']:
        raise RuntimeError('A declared build input changed: '+source.name)
    target.parent.mkdir(parents=True,exist_ok=True)
    with source.open('rb') as incoming,target.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing,2**20)
    if target.stat().st_size!=entry['bytes'] or sha(target)!=entry['sha256'] or sha(source)!=entry['sha256']:
        raise RuntimeError('Copied build input failed unchanged/readback verification.')

def record(path):return {'bytes':path.stat().st_size,'sha256':sha(path)}

def dependency_rows(manifest):
    """Closed allowlist: never admit prior executables or customer state."""
    seen=set();result=[]
    prefixes=tuple('runtime/cloud/'+folder+'/' for folder in PROVIDER_FOLDERS.values())
    for row in manifest.get('files',[]):
        name=relative(row.get('path'))
        if name.casefold() in seen:raise ValueError('Duplicate manifest path.')
        seen.add(name.casefold())
        if name.startswith(prefixes) or name.startswith('app/cloud/assets/cloud-annotation/'):
            if type(row.get('bytes')) is not int or row['bytes']<0 or not isinstance(row.get('sha256'),str) or len(row['sha256'])!=64:
                raise ValueError('Invalid dependency identity.')
            result.append(row)
    if not result:raise ValueError('No approved dependency files.')
    return result

def inventory(root,kind,edition=None):
    entries=[]
    for folder,dirs,files in os.walk(plain(root),followlinks=False):
        for name in dirs:plain(Path(folder)/name)
        for name in files:
            path=plain(Path(folder)/name);name=relative(path.relative_to(root).as_posix())
            if name=='inventory.json' or name.startswith('.inventory/'):raise ValueError('Inventory already exists.')
            if path.stat().st_nlink!=1:raise ValueError('Hard-linked build input rejected.')
            entries.append({'path':name,**record(path)})
    entries.sort(key=lambda x:x['path'])
    if not entries or len(entries)>60000 or len({x['path'].casefold() for x in entries})!=len(entries):raise ValueError('Invalid inventory closure.')
    shards=[]
    for number,start in enumerate(range(0,len(entries),512)):
        name=f'.inventory/{number:04d}.json';checksum=write(root/name,entries[start:start+512])
        shards.append({'path':name,'bytes':(root/name).stat().st_size,'sha256':checksum})
    index={'schema':1,'abi':'cp312-win-amd64','kind':kind,'edition':edition,'files':len(entries),
           'bytes':sum(x['bytes'] for x in entries),'shards':shards}
    checksum=write(root/'inventory.json',index)
    return {'sha256':checksum,'files':len(entries),'bytes':index['bytes']}

def validate_inventory(root,expected,kind,edition=None):
    sys.path.insert(0,str(ROOT/'ui'))
    from cloud_production import BundleLease
    with BundleLease(root,expected,kind,edition=edition) as lease:return dict(lease.hashes)

def stage_resources(output):
    sys.path.insert(0,str(ROOT/'ui'))
    from vlui_context import _types
    from vlui_schema import load_bundled_contract
    from vlui_component_contract import load_component_contract
    _types.cache_clear()
    if len(_types())!=72 or len(load_bundled_contract().components)!=49 or len(load_component_contract().schema.components)!=49:
        raise ValueError('Native UI contract resources are incomplete.')
    source=ROOT/'ui_system/runtime/resources.json';data=json.loads(source.read_bytes());pending=[]
    if data.get('resourceVersion')!=1 or len(data.get('resources',[]))!=25:raise ValueError('Invalid UI resource map.')
    for i,row in enumerate(data['resources']):
        name=relative(row['file'])
        if not name.startswith('ui_system/runtime/'):raise ValueError('UI resource outside sanitized runtime.')
        origin=ROOT/name;target='vlui/payload/'+f'r{i:02d}'+origin.suffix
        copy_verified(origin,output/target,row);row['file']=target
    checksum=write(output/'vlui/runtime/resources.json',data)
    # Native validator resources are exact app-owned documents, not old history.
    fixed=['ui_system/runtime/bundled/context-contract.json','ui_system/handoffs/foundation-v1-r1/components-v1.json']
    for prefix in ('ui_system/proposals/contract-v1-r5','ui_system/contracts/component-contract-v1-r1'):
        index=json.loads((ROOT/prefix/'index.json').read_bytes());fixed.append(prefix+'/index.json')
        fixed.extend(prefix+'/'+relative(x['path']) for x in index['files'])
    for name in dict.fromkeys(fixed):copy_verified(ROOT/name,output/name,record(ROOT/name))
    return checksum

def source_snapshot(output):
    output.mkdir(parents=True)
    sources={}
    for origin in sorted((ROOT/'ui').glob('*.py')):
        if origin.stem in RETIRED:raise ValueError('Retired enforcement returned to active source.')
        raw=origin.read_bytes();tree=ast.parse(raw)
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module in RETIRED:raise ValueError('Retired enforcement import in '+origin.name)
        entry=record(origin);copy_verified(origin,output/origin.name,entry);sources[origin.relative_to(ROOT).as_posix()]=entry
    return sources

def stage(output,dependency_source,manifest_sha):
    manifest=plain(dependency_source)/'information/RELEASE_MANIFEST.json'
    if sha(manifest)!=manifest_sha:raise ValueError('Dependency manifest differs from approved identity.')
    declared=json.loads(manifest.read_bytes())
    rows=dependency_rows(declared);package=output/'package'
    package.mkdir()
    print('Copying verified vendor dependency files:',len(rows),flush=True)
    for number,row in enumerate(rows,1):
        copy_verified(dependency_source/row['path'],package/row['path'],row)
        if number%3000==0:print('Verified dependency files copied:',number,flush=True)
    # Validate unmodified provider inventories against their separately declared component map.
    index_path=plain(dependency_source)/'app/cloud/components.json'
    index_row=next((r for r in declared['files'] if r['path']=='app/cloud/components.json'),None)
    if index_row is None or record(index_path)!={k:index_row[k] for k in ('bytes','sha256')}:
        raise ValueError('Baseline component map differs from the approved manifest.')
    index=json.loads(index_path.read_bytes())
    components={};torch_shas=[]
    for kind,folder in PROVIDER_FOLDERS.items():
        target=package/'runtime/cloud'/folder;checksum=index['components'][kind]
        validate_inventory(target,checksum,kind);components[kind]=checksum
        if kind in ('cpu','cuda'):
            descriptor=json.loads((target/'inventory.json').read_bytes())
            for shard in descriptor['shards']:
                torch_shas.extend(x['sha256'] for x in json.loads((target/shard['path']).read_bytes()) if x['path']=='torch/__init__.py')
    # SDK is rebuilt from currently sanitized authored source, not prior frozen code.
    sdk=ROOT/'cloud_source/4.0.0';mapping=json.loads((sdk/'IMPORT_MANIFEST.json').read_bytes())
    target=package/'app/cloud/cloud_source/4.0.0'
    for entry in mapping['files']:
        name=relative(entry['path'])
        if not name.startswith(('runtime_sdk/','training_source/')):raise ValueError('Unexpected Cloud source component.')
        copy_verified(sdk/name,target/name,entry)
    copy_verified(sdk/'IMPORT_MANIFEST.json',target/'IMPORT_MANIFEST.json',record(sdk/'IMPORT_MANIFEST.json'))
    components['sdk']=inventory(target,'sdk')['sha256']
    # Baseline SAM has its own vendor checkpoint/notices and verified cohort.
    sam=package/'app/cloud/assets/cloud-annotation';validate_inventory(sam,index['components']['sam'],'sam');components['sam']=index['components']['sam']
    component_sha=write(package/'app/cloud/components.json',{'schema':1,'abi':'cp312-win-amd64','components':components})
    sources=source_snapshot(output/'source');resources_sha=stage_resources(output/'resources')
    write(output/'stage.json',{'schema':1,'dependencyManifestSHA256':manifest_sha,'dependencyFiles':len(rows),
          'components':components,'componentsSHA256':component_sha,'torchSourceSHAs':torch_shas,
          'sources':sources,'resourcesSHA256':resources_sha,'version':(ROOT/'product_version.txt').read_text().strip()})
    return package

def compiler_environment(environment):
    keep={'SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','COMSPEC','PATHEXT','PROGRAMFILES','PROGRAMFILES(X86)','USERPROFILE'}
    value={k:v for k,v in os.environ.items() if k.upper() in keep}
    windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    value['PATH']=os.pathsep.join((str(environment/'Scripts'),str(windows/'System32'),str(windows)))
    return value

def owned_run(command,log,*,environment=None,timeout=1800,input_bytes=None):
    """Assign a gated compiler root before it may import or freeze anything."""
    sys.path.insert(0,str(ROOT/'ui'));from cloud_process import ProcessJob
    job=ProcessJob(8*1024**3,active_processes=2);process=None;started=time.monotonic()
    try:
        with log.open('xb') as stream:
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stream,stderr=subprocess.STDOUT,
                env=environment,cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            job.assign(process)
            if input_bytes is not None:process.stdin.write(input_bytes);process.stdin.flush()
            process.stdin.close()
            code=process.wait(timeout=timeout)
            query=job.api.QueryInformationJobObject
            query.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p];query.restype=ctypes.c_int
            deadline=time.monotonic()+5
            while True:
                accounting=ctypes.create_string_buffer(48)
                if not query(job.handle,1,accounting,48,None):raise ctypes.WinError(ctypes.get_last_error())
                active=struct.unpack('<4q4I',accounting.raw)[6]
                if active==0:break
                if time.monotonic()>deadline:raise RuntimeError('Owned compiler tree retained descendants after root exit.')
                time.sleep(.025)
            if code:raise RuntimeError('Owned build/probe failed; inspect '+str(log))
        return {'pid':process.pid,'exitCode':code,'assignedBeforeGO':input_bytes is not None,'treeEmptyBeforeClose':True,'elapsedSeconds':round(time.monotonic()-started,3)}
    finally:
        if process is not None and process.poll() is None:
            job.terminate();process.wait(timeout=15)
        job.close()

def freeze(output,label,entry,*,environment,onefile=False,hidden=(),exclude=(),collect=(),data=(),windowed=False,source=None,phase_name=None):
    source=source or output/'source';phase=output/(phase_name or label);phase.mkdir()
    print('Freezing fresh unrestricted component:',label,flush=True)
    arguments=['--noconfirm','--clean','--windowed' if windowed else '--console','--onefile' if onefile else '--onedir',
        '--name',label,'--distpath',str(phase/'d'),'--workpath',str(phase/'w'),'--specpath',str(phase/'s'),'--paths',str(source)]
    for name in hidden:arguments+=['--hidden-import',name]
    for name in exclude:arguments+=['--exclude-module',name]
    for name in collect:arguments+=['--collect-all',name]
    for origin,target in data:arguments+=['--add-data',str(origin)+os.pathsep+target]
    if windowed:arguments+=['--icon',str(ROOT/'assets/VaporLock.ico')]
    arguments.append(str(source/entry))
    bootstrap="import sys; assert sys.stdin.buffer.readline(64)==b'COMMUNITY-FREEZE-GO\\n'; sys.path.insert(0,sys.argv[1]); from PyInstaller.__main__ import run; run(sys.argv[2:])"
    command=[str(Path(getattr(sys,'_base_executable',sys.executable))),'-I','-B','-c',bootstrap,str(environment/'Lib/site-packages'),*arguments]
    result=owned_run(command,phase/'freeze.log',environment=compiler_environment(environment),input_bytes=b'COMMUNITY-FREEZE-GO\n')
    write(phase/'completion.json',result)
    return phase/'d'/((label+'.exe') if onefile else label)

def stdlib_modules():
    excluded={'tkinter','_tkinter','turtle','turtledemo','idlelib','test','ensurepip','venv','antigravity','this','posix','pwd','grp','fcntl','termios','resource','readline','curses','_curses','pty','spwd','syslog','_posixsubprocess','_testcapi','_testinternalcapi','_testbuffer','_xxsubinterpreters'}
    result=set();base=Path(sys.base_prefix)
    for directory in (base/'Lib',base/'DLLs'):
        for folder,dirs,files in os.walk(directory):
            dirs[:]=[d for d in dirs if d not in ('site-packages','__pycache__','test','tests','idlelib','tkinter','turtledemo','ensurepip','venv')]
            for name in files:
                path=Path(folder)/name
                if path.suffix not in ('.py','.pyd'):continue
                parts=list(path.relative_to(directory).parts);leaf=parts.pop().split('.')[0]
                if leaf!='__init__':parts.append(leaf)
                if parts and all(p.isidentifier() for p in parts) and parts[0] in sys.stdlib_module_names and parts[0] not in excluded:result.add('.'.join(parts))
    return sorted(result)

def qt_fix(bundle,environment,*,cloud=False):
    internal=bundle/'_internal';qt=environment/'Lib/site-packages/PySide6'
    names=('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll') if cloud else VC_NAMES
    for path in internal.rglob('icu*.dll'):path.unlink()
    for path in internal.rglob('*.dll'):
        if path.name.lower() in names:shutil.copy2(qt/path.name, path)
    for name in names:
        origin=qt/name
        if origin.is_file():shutil.copy2(origin,internal/name)

def canonical_base_archive(path):
    """ZIP entry ordering is irrelevant to imports; stabilize isolated compiler order."""
    with zipfile.ZipFile(path) as incoming:
        entries=incoming.infolist()
        if len({row.filename for row in entries})!=len(entries):raise ValueError('Duplicate stdlib archive entry.')
        captured=[(row,incoming.read(row.filename)) for row in entries]
        comment=incoming.comment
    temporary=path.with_name(path.name+'.canonical')
    with zipfile.ZipFile(temporary,'x') as outgoing:
        outgoing.comment=comment
        for info,raw in sorted(captured,key=lambda item:item[0].filename):outgoing.writestr(info,raw)
    with zipfile.ZipFile(temporary) as check:
        for info,raw in captured:
            if check.read(info.filename)!=raw:raise RuntimeError('Stdlib canonicalization changed a module.')
    temporary.replace(path)


def freeze_cloud(output):
    plan=json.loads((output/'stage.json').read_bytes());source=output/'source';environment=ROOT/'.venv'
    code=("EDITION='premium'\nPRODUCT_VERSION="+repr(plan['version'])+"\nCLOUD_ROLE='cloud-worker'\nCLOUD_COMPONENTS_SHA256="+repr(plan['componentsSHA256'])+
          '\nCLOUD_TORCH_SOURCE_SHAS='+repr(plan['torchSourceSHAs'])+'\n')
    hidden=['cloud_worker','cloud_annotation_worker','_cffi_backend','cryptography.hazmat.bindings._rust',*stdlib_modules()]
    excluded=['torch','numpy','PIL','onnx','onnxruntime','cloud_sdk','cloudstudio','PySide6','shiboken6','matplotlib','scipy','pandas','xxhash','cv2']
    builds=[];bootstrap_sha=None;round_root=output/('cloud-round-'+uuid.uuid4().hex[:10]);round_root.mkdir()
    for number in (1,2):
        (source/'product_build.py').write_text(code+'CLOUD_BOOTSTRAP_SHA256='+repr(bootstrap_sha)+'\n',encoding='utf8')
        # Same name/layout in both runs; output roots differ, startup exe owns PYZ.
        phase=round_root/('pass'+str(number));phase.mkdir()
        bundle=freeze(phase,'VaporLockCloudTools','cloud_worker_entry.py',environment=environment,hidden=hidden,exclude=excluded,source=source)
        qt_fix(bundle,environment,cloud=True)
        canonical_base_archive(bundle/'_internal/base_library.zip')
        if number==1:bootstrap_sha=inventory(bundle/'_internal','bootstrap')['sha256']
        else:
            first=builds[0]/'_internal';shutil.copy2(first/'inventory.json',bundle/'_internal/inventory.json');shutil.copytree(first/'.inventory',bundle/'_internal/.inventory')
            validate_inventory(bundle/'_internal',bootstrap_sha,'bootstrap')
        builds.append(bundle)
    target=output/'package/app/cloud';shutil.copy2(builds[-1]/'VaporLockCloudTools.exe',target/'VaporLockCloudTools.exe');shutil.copytree(builds[-1]/'_internal',target/'_internal')
    startup=inventory(target,'startup','premium')
    write(output/'cloud-complete.json',{'startup':startup,'bootstrapSHA256':bootstrap_sha,'round':round_root.name,'version':plan['version']})

def freeze_gui(output):
    plan=json.loads((output/'stage.json').read_bytes());cloud=json.loads((output/'cloud-complete.json').read_bytes());source=output/'gui-source';environment=ROOT/'.venv'
    if (output/'gui-plan.json').is_file():
        gui=json.loads((output/'gui-plan.json').read_bytes())
        for name,entry in gui['sources'].items():
            if record(ROOT/name)!=entry or record(source/Path(name).name)!=entry:raise RuntimeError('GUI snapshot changed; prepare a fresh output.')
        resources_sha=gui['resourcesSHA256'];sources=gui['sources']
        if sha(output/'gui-resources/vlui/runtime/resources.json')!=resources_sha:raise RuntimeError('Prepared GUI resources changed.')
    else:
        sources=source_snapshot(source);resources_sha=stage_resources(output/'gui-resources')
        write(output/'gui-plan.json',{'sources':sources,'resourcesSHA256':resources_sha,'cloudStartupSHA256':cloud['startup']['sha256'],'version':plan['version']})
    plan={**plan,'sources':sources,'resourcesSHA256':resources_sha}
    (source/'product_build.py').write_text("EDITION='premium'\nPRODUCT_VERSION="+repr(plan['version'])+"\nCLOUD_ROLE='gui'\nCLOUD_STARTUP_SHA256="+repr(cloud['startup']['sha256'])+'\nVLUI_RESOURCE_MANIFEST_SHA256='+repr(plan['resourcesSHA256'])+'\n',encoding='utf8')
    data=[(output/'gui-resources/vlui','vlui'),(output/'gui-resources/ui_system','ui_system')]
    for name in ('VaporLock.ico','VaporLock.png','Background.png','check-dark.svg','check-light.svg','arrow-up-dark.svg','arrow-down-dark.svg','arrow-up-light.svg','arrow-down-light.svg'):
        origin=ROOT/'assets'/name
        if origin.is_file():data.append((origin,'assets'))
    data.append((ROOT/'Background.png','assets'))
    for folder in ('themes','icons'):
        if (ROOT/'assets'/folder).is_dir():data.append((ROOT/'assets'/folder,'assets/'+folder))
    for language in ('de','fr','ru','zh_CN'):data.append((environment/'Lib/site-packages/PySide6/translations'/('qtbase_'+language+'.qm'),'translations'))
    bundle=freeze(output,'VaporLockControlCenter','vapor_lock_gui.py',environment=environment,windowed=True,
        hidden=('tuning_tools','motion_compensation','local_control_api','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebChannel','cryptography.hazmat.backends.openssl'),collect=('xxhash',),data=data,source=source,phase_name='g-'+uuid.uuid4().hex[:6],
        exclude=('PySide6.QtQml','PySide6.QtQuick','PySide6.QtQuickWidgets','PySide6.QtQuickControls2','PySide6.QtQuick3D'))
    qt_fix(bundle,environment)
    target=output/'package/app';target.mkdir(exist_ok=True)
    for member in bundle.iterdir():
        if member.is_dir():shutil.copytree(member,target/member.name)
        else:shutil.copy2(member,target/member.name)
    write(output/'gui-complete.json',{'executableSHA256':sha(target/'VaporLockControlCenter.exe'),'version':plan['version']})

def freeze_helpers(output):
    source=output/'source';destination=output/'package/app'
    helpers=[('VaporLockDatasetTools','dataset_worker.py','.venv-annotation-dml',('onnxruntime','PIL','xxhash'),()),
             ('VaporLockDatasetCuda','dataset_cuda_worker.py','.venv-annotation-cuda',('onnxruntime','PIL','xxhash'),()),
             ('VaporLockTrainingTools','training_worker.py','.venv',(),((source/'training_runner.py','.'),(source/'training_security.py','.'))),
             ('VaporLockAccelerationSetup','acceleration_install.py','.venv',(),())]
    results={}
    for name,entry,env,collect,data in helpers:
        artifact=freeze(output,name,entry,environment=ROOT/env,onefile=True,collect=collect,data=data)
        shutil.copy2(artifact,destination/artifact.name);results[name]=sha(destination/artifact.name)
    write(output/'helpers-complete.json',results)

def component_sources(output):
    """Verify the authored files actually present in completed PyInstaller analyses."""
    plan=json.loads((output/'stage.json').read_bytes());sources=plan['sources'];result={}
    gui_plan=json.loads((output/'gui-plan.json').read_bytes()) if (output/'gui-plan.json').is_file() else None
    def strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,(tuple,list)):
            for item in value:yield from strings(item)
        elif isinstance(value,dict):
            for key,item in value.items():yield from strings(key);yield from strings(item)
    for analysis in output.rglob('Analysis-00.toc'):
        raw=analysis.read_bytes()
        if len(raw)>16*1024**2:raise ValueError('Compiler analysis exceeds bound.')
        values=ast.literal_eval(raw.decode('utf8'));names=set()
        is_gui='VaporLockControlCenter' in analysis.parts
        selected_sources=gui_plan['sources'] if is_gui else sources
        selected_root=output/('gui-source' if is_gui else 'source')
        for value in strings(values):
            try:
                path=Path(value)
                if not path.is_absolute():continue
                relative_name=path.relative_to(selected_root).as_posix()
            except (ValueError,OSError):continue
            if '/' not in relative_name and relative_name.endswith('.py') and relative_name!='product_build.py':names.add('ui/'+relative_name)
        if not names:raise RuntimeError('Compiler analysis has no authored source closure.')
        for name in names:
            if name not in selected_sources or record(ROOT/name)!=selected_sources[name]:raise RuntimeError('An imported component source changed: '+name)
        result[analysis.relative_to(output).as_posix()]={name:selected_sources[name] for name in sorted(names)}
    if not result:raise RuntimeError('No completed compiler analysis.')
    return result


def audit_components(output):
    result=component_sources(output)
    write(output/'component-source-audit.json',{'schema':1,'analyses':result,'checkedAtUnix':int(time.time())})
    return result


def verify_inputs(output):
    plan_path=output/'gui-plan.json' if (output/'gui-plan.json').is_file() else output/'stage.json'
    plan=json.loads(plan_path.read_bytes())
    if list(output.rglob('Analysis-00.toc')):
        component_sources(output)
    else:
        for name,entry in plan['sources'].items():
            if record(ROOT/name)!=entry:raise RuntimeError('Authored source changed during packaging: '+name)
    resources='gui-resources' if plan_path.name=='gui-plan.json' else 'resources'
    if sha(output/resources/'vlui/runtime/resources.json')!=plan['resourcesSHA256']:raise RuntimeError('Prepared UI changed.')


def probe_environment(output):
    result=compiler_environment(ROOT/'.venv')
    for key,leaf in (('LOCALAPPDATA','local'),('APPDATA','roaming'),('USERPROFILE','user'),('TEMP','temp'),('TMP','temp')):
        path=output/'private-probe-state'/leaf;path.mkdir(parents=True,exist_ok=True);result[key]=str(path)
    return result


def probe(output):
    """Dependency/readiness probes only: no capture, output, model or GPU workload."""
    sys.path.insert(0,str(ROOT/'ui'))
    import queue
    from cloud_production import BundleLease
    from cloud_annotation_child import AnnotationChild
    from PyInstaller.archive.readers import CArchiveReader
    package=output/'package';directory=output/'probes';directory.mkdir()
    gui=json.loads((output/'gui-plan.json').read_bytes());cloud=json.loads((output/'cloud-complete.json').read_bytes())
    environment=probe_environment(output)
    owned_run([str(package/'app/VaporLockControlCenter.exe'),'--verify-qt-startup',str(directory/'qt-startup.json')],directory/'qt-startup.log',environment=environment,timeout=90)
    report=json.loads((directory/'qt-startup.json').read_bytes())
    if (report.get('edition'),report.get('version'),report.get('cloud_role'),report.get('cloud_startup_sha256'),report.get('resource_manifest_sha256'),report.get('browser_resources'))!=('premium',gui['version'],'gui',cloud['startup']['sha256'],gui['resourcesSHA256'],25):raise RuntimeError('Frozen GUI metadata mismatch.')
    if report.get('platform')!='windows' or report.get('rendered') is not True or report.get('product_controls_created') is not False:raise RuntimeError('Frozen GUI dependency probe failed.')
    for name,value in report['modules'].items():
        loaded=Path(value).resolve()
        if name=='icuuc.dll':
            if loaded!=(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/icuuc.dll').resolve():raise RuntimeError('GUI imported non-system ICU.')
        elif not loaded.is_relative_to((package/'app/_internal').resolve()):raise RuntimeError('GUI relied on an external native runtime.')
    class ProbeChild(AnnotationChild):
        def _stderr(self):
            captured=bytearray()
            try:
                while True:
                    raw=self.process.stderr.read(4096)
                    if not raw:break
                    if len(captured)<32768:captured.extend(raw[:32768-len(captured)])
            finally:
                self.diagnostics=bytes(captured);self._stderr_done.set()
    events=[];child=None
    with BundleLease(package/'app/cloud',cloud['startup']['sha256'],'startup',edition='premium'):
        try:
            inherited=dict(os.environ)
            try:
                os.environ.clear();os.environ.update(environment)
                child=ProbeChild([str(package/'app/cloud/VaporLockCloudTools.exe'),'--annotation-gated'],cwd=package)
            finally:
                os.environ.clear();os.environ.update(inherited)
            child.submit({'operation':'annotation','device':'cpu'})
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                try:event=child.events.get_nowait()
                except queue.Empty:event=None
                if event:
                    events.append(event)
                    if event['type']=='annotation_ready':child.submit({'command':'close'})
                    if event['type']=='error':break
                if child.poll() is not None and child.begin_exit_cleanup():break
                time.sleep(.01)
            if [e['type'] for e in events]!=['annotation_ready','annotation_closed'] or child.poll()!=0 or not child.begin_exit_cleanup():
                (directory/'cloud-diagnostics.log').write_bytes(getattr(child,'diagnostics',b''))
                write(directory/'cloud-failure.json',{'events':events,'exitCode':child.poll(),'failure':child.failure})
                raise RuntimeError('Frozen Cloud readiness/close probe failed; inspect private probe evidence.')
        finally:
            if child is not None:
                if child.poll() is None:child.terminate();child.process.wait(timeout=10)
                child.close()
    modules={}
    for executable in ('VaporLockControlCenter.exe','VaporLockDatasetTools.exe','VaporLockDatasetCuda.exe','VaporLockTrainingTools.exe','VaporLockAccelerationSetup.exe','cloud/VaporLockCloudTools.exe'):
        reader=CArchiveReader(str(package/'app'/executable));pyz=next(k for k in reader.toc if k.endswith('.pyz'));names=reader.open_embedded_archive(pyz).toc
        if RETIRED.intersection(names):raise RuntimeError('Retired enforcement appeared in frozen component.')
        modules[executable]={'sha256':sha(package/'app'/executable),'retiredModulesAbsent':True}
    write(output/'frozen-probes.json',{'schema':1,'guiDependencyPass':True,'cloudReadyClosePass':True,'cloudEvents':events,'frozenModules':modules,'gpuWorkloadExecuted':False,'captureExecuted':False,'controllerInstalled':False})
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--dependency-source',type=Path);p.add_argument('--dependency-manifest-sha256')
    p.add_argument('--phase',choices=('stage','stage-helpers','cloud','gui','helpers','audit','probe','all'),default='all');a=p.parse_args()
    if sys.version_info[:2]!=(3,12):p.error('The packaged runtime ABI requires Python 3.12 x64.')
    output=plain(a.output)
    if output.parent!=ROOT/'build' or not output.name.startswith('community-bundle-'):p.error('Use a fresh build/community-bundle-<nonce> output.')
    if a.phase=='stage-helpers':
        if output.exists():p.error('Fresh helper output already exists.')
        output.mkdir();(output/'package/app').mkdir(parents=True)
        sources=source_snapshot(output/'source');resources_sha=stage_resources(output/'resources')
        write(output/'stage.json',{'sources':sources,'resourcesSHA256':resources_sha,'vendorDependenciesCopied':False})
    if a.phase in ('stage','all'):
        if output.exists():p.error('Fresh stage output already exists; preserve prior evidence.')
        if a.dependency_source is None or not a.dependency_manifest_sha256:p.error('Stage requires an explicit dependency source and approved manifest SHA256.')
        output.mkdir();stage(output,plain(a.dependency_source),a.dependency_manifest_sha256)
    if a.phase in ('cloud','all'):freeze_cloud(output)
    if a.phase in ('gui','all'):freeze_gui(output)
    if a.phase in ('helpers','all'):freeze_helpers(output)
    if a.phase=='probe':probe(output)
    if a.phase=='audit':audit_components(output)
    else:verify_inputs(output)
    print('Community bundle phase completed:',a.phase,flush=True)

if __name__=='__main__':main()
