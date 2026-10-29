"""Windows single-group CPU inventory. No process affinity is changed here."""
import ctypes
from ctypes import wintypes as W
import hashlib,json,os,struct
from batch7_settings import validate


def inventory():
    if os.name!='nt':raise ValueError('CPU placement requires Windows.')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetCurrentProcess.restype=W.HANDLE
    k.GetActiveProcessorGroupCount.restype=W.WORD
    if k.GetActiveProcessorGroupCount()!=1:raise ValueError('Explicit placement currently supports one processor group only.')
    k.GetProcessAffinityMask.argtypes=[W.HANDLE,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
    process_mask=ctypes.c_size_t();system_mask=ctypes.c_size_t()
    if not k.GetProcessAffinityMask(k.GetCurrentProcess(),ctypes.byref(process_mask),ctypes.byref(system_mask)):
        raise ValueError('CPU affinity inventory is unavailable.')
    k.GetSystemCpuSetInformation.argtypes=[ctypes.c_void_p,W.ULONG,ctypes.POINTER(W.ULONG),W.HANDLE,W.ULONG]
    size=W.ULONG();k.GetSystemCpuSetInformation(None,0,ctypes.byref(size),k.GetCurrentProcess(),0)
    if not 1<=size.value<=1024*1024:raise ValueError('CPU inventory is unavailable.')
    buffer=ctypes.create_string_buffer(size.value)
    if not k.GetSystemCpuSetInformation(buffer,size,ctypes.byref(size),k.GetCurrentProcess(),0):raise ValueError('CPU inventory changed.')
    data=buffer.raw[:size.value];offset=0;rows=[];all_rows=[]
    while offset<len(data):
        if len(data)-offset<8:raise ValueError('Invalid CPU inventory.')
        length,kind=struct.unpack_from('<II',data,offset)
        if length<8 or offset+length>len(data):raise ValueError('Invalid CPU inventory.')
        if kind==0:
            if length<32:raise ValueError('Invalid CPU inventory.')
            identifier,group,cpu,core,cache,node,efficiency,flags=struct.unpack_from('<IHBBBBBB',data,offset+8)
            all_rows.append((identifier,group,cpu,core,efficiency))
            if group==0 and cpu<64 and process_mask.value&(1<<cpu) and not(flags&2 and not flags&4):
                rows.append(dict(id=identifier,cpu=cpu,core=core,efficiency=efficiency))
        offset+=length
    if not rows or len(rows)>64 or len({r['cpu'] for r in rows})!=len(rows):raise ValueError('CPU inventory is unsupported.')
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Cryptography',0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
        machine=winreg.QueryValueEx(key,'MachineGuid')[0]
    identity=hashlib.sha256(json.dumps([machine,all_rows,process_mask.value],separators=(',',':')).encode()).hexdigest()
    return identity,rows


def wire(settings,provider):
    placement=settings.inference_cpu_placement;validate({'inference_cpu_placement':placement})
    if provider!='cpu' or not placement:return ''
    identity,rows=inventory();by_cpu={r['cpu']:r['id'] for r in rows}
    if identity!=placement['identity'] or any(cpu not in by_cpu for cpu in placement['cpus']):
        raise ValueError('Saved CPU placement is unavailable. Choose CPUs again or select System default.')
    return ','.join(str(by_cpu[cpu]) for cpu in placement['cpus'])


def add_controls(center,layout,provider):
    from PySide6.QtWidgets import QPushButton,QLabel,QVBoxLayout,QWidget
    control=QWidget();column=QVBoxLayout(control);column.setContentsMargins(0,0,0,0)
    button=QPushButton(center.t('CPU execution placement…'));button.setObjectName('cpuPlacementButton')
    status=QLabel();status.setWordWrap(True);column.addWidget(button);column.addWidget(status)
    center.cpu_placement_status=status
    def refresh():
        selected=center.settings.inference_cpu_placement
        text=center.t('System default') if not selected else 'Group 0 · '+', '.join(str(i) for i in selected['cpus'])
        if selected:
            saved_key=(selected['identity'],tuple(selected['cpus']))
            if getattr(control,'_saved_cpu_key',None)!=saved_key:
                try:wire(center.settings,'cpu');control._available=True
                except (OSError,ValueError):control._available=False
                control._saved_cpu_key=saved_key
            if not control._available:text+=' · '+center.t('Unavailable: choose CPUs again or select System default.')
        if center.settings.provider not in ('auto','cpu'):text+=' · '+center.t('Inactive with GPU inference')
        else:text+=' · '+center.t('Applies to CPU model execution only; no speed guarantee.')
        observed=getattr(center,'_cpu_placement_observation',None)
        effective=center.t('Effective placement: unavailable')
        if (observed and center.engine.session_running and center.engine.active_provider=='cpu'
                and observed[0]==center.engine._launch_generation):
            effective=(center.t('Effective placement: system default') if observed[1]==0 else
                center.tf('Effective placement: {count} CPUs for ORT workers and model calls.',count=observed[1]))
        status.setText(center.t('Requested placement')+': '+text+'\n'+effective)
    button.clicked.connect(lambda: (edit(center),refresh()))
    provider.currentIndexChanged.connect(lambda _i:refresh());refresh()
    center.refresh_cpu_placement_status=refresh
    center.setting(layout,'CPU execution placement','Advanced stopped-session control. Capture, preprocessing, output and GUI keep their normal scheduling.',control)


def observe(center,data):
    count=data.get('cpu_placement_count')
    valid=center.engine.session_running and center.engine.active_provider=='cpu' and type(count) is int and 0<=count<=64
    center._cpu_placement_observation=(center.engine._launch_generation,count) if valid else None
    callback=getattr(center,'refresh_cpu_placement_status',None)
    if callback:callback()


def edit(center):
    from batch4_guard import editable,EditGuard
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QCheckBox,QDialogButtonBox,QListWidget,QListWidgetItem
    if not editable(center,stopped=True):return
    guard=EditGuard(center,stopped=True);dialog=QDialog(center);dialog.setWindowTitle(center.t('CPU execution placement'));dialog.resize(540,520)
    layout=QVBoxLayout(dialog);default=QCheckBox(center.t('System default'));saved=center.settings.inference_cpu_placement
    default.setChecked(not saved);layout.addWidget(default)
    description=QLabel(center.t('Select Group 0 logical CPUs for ORT workers and the model-call thread. Other stages are unchanged. Placement can reduce performance.'))
    description.setWordWrap(True);layout.addWidget(description);choices=QListWidget();layout.addWidget(choices)
    status=QLabel();status.setWordWrap(True);status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(status)
    identity='';rows=[]
    try:
        identity,rows=inventory()
        for row in rows:
            item=QListWidgetItem(f"Group 0 · CPU {row['cpu']} · core {row['core']} · efficiency class {row['efficiency']}")
            item.setData(Qt.ItemDataRole.UserRole,row['cpu']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if saved and identity==saved['identity'] and row['cpu'] in saved['cpus'] else Qt.CheckState.Unchecked);choices.addItem(item)
        if saved and identity!=saved['identity']:status.setText(center.t('Saved CPU placement is unavailable. Choose CPUs again or select System default.'))
    except (OSError,ValueError):status.setText(center.t('CPU placement is unavailable on this system. System default remains available.'))
    choices.setEnabled(not default.isChecked());default.toggled.connect(lambda value:choices.setEnabled(not value))
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
    def apply():
        if not guard.current():dialog.reject();return
        cpus=[choices.item(i).data(Qt.ItemDataRole.UserRole) for i in range(choices.count()) if choices.item(i).checkState()==Qt.CheckState.Checked]
        if not default.isChecked():
            try:
                fresh,freshrows=inventory()
                if fresh!=identity or not cpus or not set(cpus).issubset({r['cpu'] for r in freshrows}):raise ValueError()
            except (OSError,ValueError):status.setText(center.t('CPU inventory changed or no CPUs are selected. Reopen this dialog.'));return
        center.change('inference_cpu_placement',{} if default.isChecked() else dict(identity=identity,cpus=sorted(cpus)));dialog.accept()
    buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply);buttons.rejected.connect(dialog.reject);dialog.exec()
