"""Short-lived native dialog guards; no renderer authority."""
from dataclasses import asdict


def editable(center, action='setNumericSetting', *, stopped=False):
    owner=getattr(center,'_live_owner',None)
    if (getattr(center,'_tuning_locked',False) or getattr(center,'_native_closing',False)
            or False or getattr(center,'_profile_transition_pending',False)
            or (owner is not None and (owner._transition is not None or not owner.authorize(action)))):
        return False
    if stopped and (center.engine.active or center._policy_start_pending
            or center.xbox_compatible_bridge.preparing or center._pending_controller_start is not None
            or not center.xbox_compatible_bridge.safely_stopped()
            or getattr(center.engine,'_controller_host_session',None) is not None or center.model_probe_busy()
            or (owner is not None and owner.background_conflict())):
        return False
    return True


class EditGuard:
    def __init__(self,center,action='setNumericSetting',*,stopped=False):
        self.center=center;self.action=action;self.stopped=stopped
        self.settings=center.settings;self.values=asdict(center.settings)
        self.profile=center._active_profile_path
        self.epoch=center._tuning_epoch
        self.generation=getattr(center,'_start_intent_generation',0)
        self.owner=getattr(center,'_live_owner',None)
        self.stamp=self.owner.stamp() if self.owner else None

    def current(self):
        c=self.center
        return (editable(c,self.action,stopped=self.stopped)
            and self.settings is c.settings and self.values==asdict(c.settings)
            and self.profile==c._active_profile_path and self.epoch==c._tuning_epoch
            and self.generation==getattr(c,'_start_intent_generation',0)
            and self.owner is getattr(c,'_live_owner',None)
            and (self.owner is None or self.stamp==self.owner.stamp()))
