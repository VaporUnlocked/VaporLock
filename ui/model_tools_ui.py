"""Native-owned model tools shared by the native and HTML entry points."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import queue
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QListWidget, QListWidgetItem, QFileDialog, QScrollArea)

from pipeline_tools import model_identity, engine_identity, recommended_rates, profile_summary


class ModelTools(QDialog):
    def __init__(self, center):
        super().__init__(center)
        self.center = center
        self._static = []
        self._locale = None
        self._return_focus = None
        self.busy = False
        self._operation = None
        self._identity_thread = None
        self._cancelled = threading.Event()
        self._results = queue.SimpleQueue()
        self._recommendation = None
        self._generation = 0
        self._status_copy=('Ready. Changes remain unsaved until you save the profile.',{})
        self._performance_copy='Run measurements to see current → proposed settings. Apply is unavailable until measurements are complete.'
        self._measured_display=None
        self._next = None
        self._identity_deadline = None
        self._failures = []
        self.setWindowTitle(center.t('Model tools'))
        self.setMinimumSize(660, 500)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        layout.addWidget(self.tabs)
        self.status = self.label('Ready. Changes remain unsaved until you save the profile.')
        self.status.setAccessibleName(center.t('Model tools status'))
        layout.addWidget(self.status)
        self.cancel_button = QPushButton(center.t('Cancel operation'))
        self.cancel_button.clicked.connect(center.stop_capture)
        layout.addWidget(self.cancel_button)
        self._performance_page()
        self._switch_page()
        self._discovery_page()
        self._static = [(widget,source) for widget,source in self._static
                        if widget not in (self.status,self.performance,self.committed,self.details)]
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._refresh_buttons()

    def set_status(self, source, **values):
        self._status_copy=(source,values)
        self.status.setText(self.center.tf(source,**values))

    def render_performance(self):
        c=self.center
        if self._measured_display is None:
            self.performance.setText(c.t(self._performance_copy)); return
        results,proposal,original,input_details=self._measured_display
        lines=[c.tf('{provider}: {rate} inferences/s, {ms} ms per inference',
            provider=c.engine.provider_label(r['provider']),rate=f"{r['fps']:.1f}",ms=f"{r['inference_ms']:.1f}") for r in results]
        lines.append(c.tf('Verified model input: {details}',details=input_details))
        lines.append(c.tf('Capture region: {width} × {height}; monitor: {monitor}',
            width=getattr(original,'region_width',0),height=getattr(original,'region_height',0),monitor=getattr(original,'monitor',0)))
        names={'provider':'Inference provider','capture_fps':'Capture rate','inference_fps':'Inference rate','frame_skip':'Frame skipping'}
        for provider,reason in self._failures:
            lines.append(c.engine.provider_label(provider)+': '+c.t(reason))
        for field,value in proposal.items():
            previous=getattr(original,field)
            if field=='provider': previous,value=self.provider_name(previous),self.provider_name(value)
            lines.append(f'{c.t(names[field])}: {previous} → {value}')
        self.performance.setText('\n'.join(lines))

    def provider_name(self, value):
        return self.center.t('Automatic (DirectML preferred)') if value=='auto' else self.center.engine.provider_label(value)

    def label(self, text):
        label = QLabel(self.center.t(text))
        self._static.append((label,text))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def page(self, name):
        page = QWidget(); layout = QVBoxLayout(page)
        scroll=QScrollArea(); scroll.setWidgetResizable(True);scroll.setWidget(page)
        scroll.setStyleSheet('QScrollArea { border: 0; }')
        self.tabs.addTab(scroll, self.center.t(name))
        return layout

    def button(self, layout, text, callback):
        button = QPushButton(self.center.t(text)); button.clicked.connect(callback)
        self._static.append((button,text))
        layout.addWidget(button)
        return button

    def _performance_page(self):
        layout = self.page('Performance setup')
        layout.addWidget(self.label('Measure and Apply stop the current session. Output stays stopped until you use Start.'))
        layout.addWidget(self.label('Measures the current capture region with output, previews and dataset writing disabled. Measurement uses at least 120 capture/inference requests per second and no frame skipping.'))
        self.measure_button = self.button(layout, 'Measure performance', self.measure)
        self.performance = self.label('Run measurements to see current → proposed settings. Apply is unavailable until measurements are complete.')
        layout.addWidget(self.performance)
        self.apply_button = self.button(layout, 'Apply measured recommendation', self.apply_recommendation)
        layout.addWidget(self.label('A conservative starting point with 10% rate headroom, capped at 120. Lower rates reduce load but can increase response delay. Results describe this machine and capture scene; detection accuracy is not measured.'))
        layout.addWidget(self.label('Model input size is defined by the ONNX model. Capture-region size is separate. Keep the current input size unless a different export has been validated; no quality-preserving resize is assumed.'))
        layout.addStretch()

    def _switch_page(self):
        layout = self.page('Model switching')
        self.committed = self.label('Current model')
        layout.addWidget(self.committed)
        candidate_label=self.label('Candidate model — selecting it does not replace the current configuration.')
        layout.addWidget(candidate_label)
        self.candidate = QLineEdit(self.center.settings.model_path)
        candidate_label.setBuddy(self.candidate)
        self.candidate.setMaxLength(4096)
        layout.addWidget(self.candidate)
        self.button(layout, 'Browse…', self.browse)
        self.provider = QComboBox()
        provider_label=self.label('Candidate provider'); provider_label.setBuddy(self.provider)
        layout.addWidget(provider_label)
        for name,value in (('Automatic (DirectML preferred)','auto'),('DirectML','dml'),('CUDA','cuda'),('TensorRT RTX','trt'),('CPU','cpu')):
            self.provider.addItem(self.center.t(name), value)
        self.provider.setCurrentIndex(max(0,self.provider.findData(self.center.settings.provider)))
        layout.addWidget(self.provider)
        self.switch_button = self.button(layout, 'Validate and switch', self.switch)
        self.test_signal_button = self.button(layout, 'Use test signal', self.use_test_signal)
        layout.addWidget(self.label('Test signal removes the detection model without model validation. Output remains stopped.'))
        layout.addWidget(self.label('Stops the current session, validates the candidate with output disabled, then commits once. Failure keeps the previous configuration. Output stays stopped; use Start when ready.'))
        self.candidate.textChanged.connect(lambda: self.invalidate_candidate())
        self.provider.currentIndexChanged.connect(lambda: self.invalidate_candidate())
        layout.addStretch()

    def _discovery_page(self):
        layout = self.page('Discovery')
        search_label=self.label('Search models and profiles'); layout.addWidget(search_label)
        self.search = QLineEdit(); search_label.setBuddy(self.search)
        self.search.setPlaceholderText(self.center.t('Search models and profiles'))
        self.search.setMaxLength(160); layout.addWidget(self.search)
        self.items = QListWidget(); layout.addWidget(self.items)
        self.details = self.label('Select an item to preview its requirements. Compatibility is unknown until validated.')
        layout.addWidget(self.details)
        self.use_button = self.button(layout, 'Use selected model as candidate', self.use_selected)
        self.load_button = self.button(layout, 'Open selected profile', self.load_selected)
        self.button(layout, 'Refresh', self.refresh_discovery)
        self.search.textChanged.connect(self.filter_discovery)
        self.items.currentItemChanged.connect(lambda *_: self.preview_selected())
        self.refresh_discovery()

    def refresh_discovery(self):
        from model_discovery import discover_entries
        import vapor_lock_gui as gui
        previous = self.items.currentItem().data(Qt.ItemDataRole.UserRole) if self.items.currentItem() else None
        self.items.clear()
        entries, limited = discover_entries(gui.ROOT/'models', gui.PROFILE_DIRECTORY)
        for kind,label,path in entries:
            item=QListWidgetItem(f'{self.center.t(kind)}: {label}')
            item.setData(Qt.ItemDataRole.UserRole,(kind,str(path))); self.items.addItem(item)
            if previous==(kind,str(path)): self.items.setCurrentItem(item)
        self.filter_discovery()
        if limited: self.details.setText(self.center.t('Discovery reached its limit. Use Browse or Open profile for other files.'))

    def filter_discovery(self):
        query=self.search.text().casefold(); visible=0
        for i in range(self.items.count()):
            item=self.items.item(i); match=query in item.text().casefold()
            item.setHidden(not match); visible+=int(match)
        if not visible: self.details.setText(self.center.t('No matching models or profiles.'))
        self.preview_selected()

    def preview_selected(self):
        item=self.items.currentItem()
        self.use_button.setEnabled(bool(item and not item.isHidden() and item.data(Qt.ItemDataRole.UserRole)[0]=='Model') and not self.busy)
        self.load_button.setEnabled(bool(item and not item.isHidden() and item.data(Qt.ItemDataRole.UserRole)[0]=='Profile') and not self.busy)
        if not item or item.isHidden():
            visible=any(not self.items.item(i).isHidden() for i in range(self.items.count()))
            self.details.setText(self.center.t('Select an item to preview its requirements. Compatibility is unknown until validated.'
                                              if visible else 'No matching models or profiles.'))
            return
        kind,path=item.data(Qt.ItemDataRole.UserRole)
        try:
            if kind=='Profile': settings=self.center.prepare_profile_path(Path(path))
            else:
                settings=deepcopy(self.center.settings); settings.model_path=path
            info=profile_summary(settings)
            info['provider']=self.provider_name(info['provider'])
            info['compatibility']=self.center.t(info['compatibility'])
            if info['model']=='Test signal':info['model']=self.center.t('Test signal')
            self.details.setText(self.center.tf('Model: {model}\nProvider: {provider}\nCapture region: {capture}\nCompatibility: {compatibility}', **info))
        except (OSError,ValueError,TypeError,RecursionError):
            self.details.setText(self.center.t('Profile could not be read safely.'))

    def use_selected(self):
        item=self.items.currentItem()
        if item and not item.isHidden() and not self.busy:
            kind,path=item.data(Qt.ItemDataRole.UserRole)
            if kind=='Model': self.candidate.setText(path); self.tabs.setCurrentIndex(1)

    def load_selected(self):
        item=self.items.currentItem()
        if item and not item.isHidden() and not self.busy:
            kind,path=item.data(Qt.ItemDataRole.UserRole)
            if kind=='Profile': self.center.load_profile_path(Path(path))

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,self.center.t('Choose ONNX model'),self.candidate.text(),'ONNX models (*.onnx)')
        if path: self.candidate.setText(path)

    def invalidate_candidate(self):
        if self.busy and self._operation=='switch': self.center.stop_capture()
        self._refresh_buttons()

    def invalidate(self):
        self._recommendation=None
        self._measured_display=None
        self._performance_copy='Settings changed. Rerun measurements before Apply.'
        self.render_performance()
        self._refresh_buttons()

    def cancel(self):
        self._generation+=1; self._cancelled.set(); self._next=None
        self._identity_deadline=None
        if self.busy: self.set_status('Cancellation requested. Previous configuration retained.')
        self.busy=False; self._operation=None
        self._refresh_buttons()

    def _refresh_buttons(self):
        if self._recommendation is not None and (self._recommendation[3]!=getattr(self.center.engine,'_launch_generation',0)
                or not 0 <= time.monotonic()-self._recommendation[4] <= 300):
            self.invalidate(); return
        locale=getattr(self.center,'ui_language','en')
        if self._locale != locale:
            self._locale=locale
            for widget,source in self._static: widget.setText(self.center.t(source))
            self.setWindowTitle(self.center.t('Model tools'))
            self.set_status(self._status_copy[0],**self._status_copy[1])
            self.render_performance()
            for i in range(self.items.count()):
                item=self.items.item(i);kind,path=item.data(Qt.ItemDataRole.UserRole)
                # Preserve the relative discovery label across locale changes.
                label=item.data(Qt.ItemDataRole.UserRole+1)
                if label is None: label=item.text().partition(': ')[2];item.setData(Qt.ItemDataRole.UserRole+1,label)
                item.setText(f'{self.center.t(kind)}: {label}')
            self.filter_discovery()
            self.provider.blockSignals(True)
            for index,name in enumerate(('Automatic (DirectML preferred)','DirectML','CUDA','TensorRT RTX','CPU')):
                self.provider.setItemText(index,self.center.t(name))
            self.provider.blockSignals(False)
            self.cancel_button.setText(self.center.t('Cancel operation'))
            self.search.setPlaceholderText(self.center.t('Search models and profiles'))
            for widget,name in ((self.candidate,'Candidate model'),(self.provider,'Candidate provider'),
                                (self.search,'Search models and profiles'),(self.items,'Models and profiles'),
                                (self.status,'Model tools status')):
                widget.setAccessibleName(self.center.t(name))
            for index,name in enumerate(('Performance setup','Model switching','Discovery')):
                self.tabs.setTabText(index,self.center.t(name))
        cleanup=self._identity_thread is not None and self._identity_thread.is_alive()
        enabled=not self.busy and not cleanup and not bool(getattr(self.center,'_model_probes',[]))
        self.measure_button.setEnabled(enabled)
        self.switch_button.setEnabled(enabled and bool(self.candidate.text().strip()))
        self.test_signal_button.setEnabled(enabled)
        self.apply_button.setEnabled(enabled and self._recommendation is not None)
        self.cancel_button.setEnabled(self.busy or cleanup)
        self.committed.setText(self.center.tf('Current model: {model}\nProvider: {provider}',
            model=self.center.settings.model_path or self.center.t('Test signal'),provider=self.provider_name(self.center.settings.provider)))

    def _begin(self, operation, callback):
        c=self.center
        if (self.busy or (self._identity_thread is not None and self._identity_thread.is_alive())
                or c.model_probe_busy() or c._profile_transition_pending or c._native_closing
                or c.acceleration.active or c._calibration_active or False):
            return False
        owner=getattr(c,'_live_owner',None)
        if owner is not None and owner.background_conflict(): return False
        from vlui_native_injection import native_injection
        if native_injection() is not None: return False
        c.stop_capture()
        self.busy=True; self._operation=operation; self._generation+=1
        self._cancelled=threading.Event()
        self._settings=deepcopy(c.settings); self._stamp=asdict(c.settings)
        self._session_generation=c.engine._launch_generation
        self._deadline=time.monotonic()+5
        self._next=callback
        self.set_status('Waiting for the previous session to stop…')
        self._refresh_buttons(); c.set_start_enabled(False); c.stop_button.setEnabled(True)
        return True

    def _current(self):
        return (self.busy and not self._cancelled.is_set() and not self.center._native_closing
                and not False
                and not self.center.acceleration.active and not self.center._profile_transition_pending
                and self._stamp==asdict(self.center.settings)
                and self._session_generation==self.center.engine._launch_generation)

    def _poll(self):
        self._refresh_buttons()
        if not self.busy: self.center.set_start_enabled(not self.center.engine.active)
        if self.busy and not self._current():
            self.center.cancel_model_probes(); return
        if self.busy and self._identity_deadline is not None and time.monotonic()>self._identity_deadline:
            self._cancelled.set(); self._identity_deadline=None
            self._fail('Model identity check timed out.'); return
        if self._next is not None:
            c=self.center
            stopped=(not c.engine.active and not c.xbox_compatible_bridge.preparing
                and c.xbox_compatible_bridge.safely_stopped()
                and c._pending_controller_start is None and not c._policy_start_pending
                and getattr(c.engine,'_controller_host_session',None) is None)
            if stopped:
                callback,self._next=self._next,None; callback()
            elif time.monotonic()>self._deadline:
                self._fail('Previous session did not stop. Configuration retained.'); return
        while not self._results.empty():
            generation,callback,result,error=self._results.get()
            if generation!=self._generation or not self._current(): continue
            self._identity_deadline=None
            if error: self._fail(error)
            else: callback(result)

    def _identity(self, settings, callback):
        import vapor_lock_gui as gui
        self.set_status('Checking model identity…')
        generation=self._generation; cancel=self._cancelled; result_queue=self._results
        self._identity_deadline=time.monotonic()+90
        path=gui.resolve_product_path(settings.model_path)
        engines={p:executable for p in ('dml','cuda','trt','cpu')
                 if (executable:=self.center.engine.executable_for(p)) is not None}
        def work():
            try:
                result=(model_identity(path,cancelled=cancel.is_set),engine_identity(engines,cancelled=cancel.is_set))
                error=None
            except Exception:
                # Worker boundary: all failures must return a sanitized terminal
                # result, including a file changing during metadata parsing.
                result=None; error='Model identity is unavailable or changed. Use native inspection for unsupported model dependencies.'
            result_queue.put((generation,callback,result,error))
        self._identity_thread=threading.Thread(target=work,name='VaporLockModelIdentity',daemon=True)
        self._identity_thread.start()

    def _fail(self, message, details=()):
        self.busy=False; self._operation=None; self._next=None
        stopped=not self.center.engine.active and self.center.xbox_compatible_bridge.safely_stopped()
        suffix='Previous configuration retained — output stopped.' if stopped else 'Previous configuration retained; Stop is still pending.'
        self.set_status('{message}\n{outcome}',message=self.center.t(message),outcome=self.center.t(suffix))
        if details: self.status.setText(self.status.text()+'\n'+'\n'.join(details[:4]))
        self._refresh_buttons(); self.center.set_start_enabled(not self.center.engine.active)

    def _complete(self, message):
        self.busy=False; self._operation=None
        self.set_status(message)
        self._refresh_buttons(); self.center.set_start_enabled(not self.center.engine.active)

    def measure(self):
        self._recommendation=None
        self._failures=[]
        self._begin('measure',lambda:self._identity(self._settings,self._measure_ready))

    def _measure_ready(self, identity):
        c=self.center; settings=deepcopy(self._settings)
        settings.capture_fps=max(120,settings.capture_fps); settings.inference_fps=max(120,settings.inference_fps)
        settings.frame_skip=0; results=[]; failures=[]; input_details=''
        providers=[p for p in ('dml','cuda','trt','cpu') if c.engine.executable_for(p)]
        self._failures=[(p,'Unavailable') for p in ('dml','cuda','trt','cpu') if p not in providers]
        def next_provider():
            if not self._current(): return
            if not providers:
                proposal=recommended_rates(results)
                if proposal is None: self._fail('No provider produced usable live timing data.',failures); return
                def inspected(result):
                    nonlocal input_details
                    if not self._current(): return
                    if not result.ok: self._fail('Candidate validation failed.'); return
                    input_details=result.inspection
                    self._identity(self._settings,lambda latest:finish(latest))
                c._probe_model(settings,proposal['provider'],inspected,inspection=True)
                return
            provider=providers.pop(0)
            self.set_status('Measuring {provider}…',provider=c.engine.provider_label(provider))
            def done(result):
                if not self._current(): return
                if result.ok: results.append({'provider':provider,**result.measurement})
                else:
                    failures.append(c.engine.provider_label(provider)+': '+c.t(result.reason))
                    self._failures.append((provider,result.reason))
                next_provider()
            c._probe_model(settings,provider,done)
        def finish(latest):
            if latest!=identity: self._fail('Model changed. Rerun measurements.'); return
            proposal=recommended_rates(results)
            if proposal is None: self._fail('No provider produced usable live timing data.'); return
            self._recommendation=(identity,self._stamp,proposal,self._session_generation,time.monotonic())
            self._measured_display=(results,proposal,self._settings,input_details)
            self.render_performance()
            self._complete('Measurements ready. Review the proposed changes before Apply.')
        next_provider()

    def apply_recommendation(self):
        recipe=self._recommendation
        if recipe is None: return
        if (recipe[1]!=asdict(self.center.settings) or recipe[3]!=self.center.engine._launch_generation
                or not 0 <= time.monotonic()-recipe[4] <= 300): self.invalidate(); return
        def ready():
            self._identity(self._settings,lambda identity:self._commit(recipe[2]) if identity==recipe[0]
                           else self._fail('Model changed. Rerun measurements.'))
        self._begin('apply',ready)

    def switch(self):
        candidate=deepcopy(self.center.settings)
        candidate.model_path=self.candidate.text().strip(); candidate.provider=self.provider.currentData()
        if not candidate.model_path: return
        self._begin('switch',lambda:self._identity(candidate,lambda identity:self._validate_candidate(candidate,identity)))

    def use_test_signal(self):
        provider=self.provider.currentData()
        self._begin('switch',lambda:self._commit({'model_path':'','provider':provider}))

    def _validate_candidate(self, candidate, identity):
        providers=list(self.center.engine.candidate_providers(candidate))
        failures=[]
        def next_provider():
            if not self._current(): return
            if not providers: self._fail('Candidate validation failed.',failures); return
            provider=providers.pop(0)
            self.set_status('Validating {provider}…',provider=self.center.engine.provider_label(provider))
            def inspected(result):
                if not self._current(): return
                if not result.ok:
                    failures.append(self.center.engine.provider_label(provider)+': '+self.center.t(result.reason))
                    next_provider(); return
                validation=deepcopy(candidate)
                validation.capture_fps=max(30,validation.capture_fps)
                validation.inference_fps=max(30,validation.inference_fps)
                validation.frame_skip=0
                self.center._probe_model(validation,provider,measured)
            def measured(result):
                if not self._current(): return
                if not result.ok:
                    failures.append(self.center.engine.provider_label(provider)+': '+self.center.t(result.reason))
                    next_provider(); return
                self._identity(candidate,lambda latest:self._commit({'model_path':candidate.model_path,'provider':candidate.provider})
                    if latest==identity else self._fail('Model changed during validation.'))
            self.center._probe_model(candidate,provider,inspected,inspection=True)
        next_provider()

    def _commit(self, fields):
        if not self._current() or self.center.engine.active: return
        self.busy=False; self._operation=None
        try:
            self.center.commit_model_tool_settings(fields)
        except ValueError:
            self._fail('Configuration could not be applied.'); return
        self._recommendation=None
        self._complete('Configuration applied — output stopped. Use Start when ready; Save to keep these settings.')

    def reject(self):
        # QDialog Escape goes through reject(), not closeEvent automatically.
        self.close()

    def closeEvent(self, event):
        if self.busy: self.center.stop_capture()
        self.hide()
        from shiboken6 import isValid
        if self._return_focus is not None and isValid(self._return_focus): self._return_focus.setFocus()
        event.ignore()
