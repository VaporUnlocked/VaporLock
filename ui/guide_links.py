"""Fixed native-control to full bundled guide topic mapping."""
from functools import lru_cache
import re

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QToolButton
from batch1_controls import symbol_icon, symbol_color
from settings_guide import guide_path

GROUPS = {
    'anchor': 'anchor_mode anchor_x anchor_y anchor_smoothing anchor_settle_radius',
    'target-switching': 'strategy switching lock_persistence_ms minimum_track_age near_candidate_wait_ms class_policies',
    'losing-target': 'match_iou smoothing box_smoothing track_retention_ms lost_target_timeout_ms occlusion_handling occlusion_grace_ms occlusion_reacquire_radius predict_lost prediction_lead_ms prediction_limit_ms',
    'too-slow': 'gain max_step max_speed input_units_per_pixel_x input_units_per_pixel_y output_rate',
    'buzzing': 'deadzone movement_smoothing',
    'nothing-detected': 'confidence class_filter',
    'duplicate-boxes': 'nms_enabled nms_iou nms_class_agnostic max_detections',
    'wrong-crop': 'monitor region_x region_y region_width region_height',
    'slow-processing': 'capture_fps inference_fps frame_skip ort_intra_op_threads ort_inter_op_threads provider',
    'blank-preview': 'preview_enabled preview_fps preview_quality',
    'missing-overlay': 'overlay_enabled overlay_boxes overlay_motion overlay_fov desktop_overlay_enabled desktop_overlay_visible desktop_overlay_opacity desktop_overlay_labels desktop_overlay_status desktop_overlay_motion desktop_overlay_layout desktop_overlay_scale',
    'controller-activation': 'activation_mode activation_key activation_source controller_slot controller_binding controller_trigger_press controller_trigger_release',
    'no-movement': 'movement_enabled output_transport aim_reference aim_reference_x aim_reference_y',
    'snap-action': 'event_action_enabled event_action_mode event_action_button event_action_cooldown_ms event_action_requires_activation snap_radius',
    'compensation': 'motion_compensation_enabled motion_compensation_mode motion_compensation_strength motion_compensation_vertical motion_compensation_horizontal',
    'no-samples': 'dataset_enabled dataset_directory dataset_capture_confidence_enabled dataset_min_confidence dataset_interval_ms dataset_capture_requires_activation dataset_jpeg_quality',
    'annotation-missing-objects': 'dataset_annotation_passes dataset_annotation_confidence dataset_annotation_iou dataset_annotation_nms_iou',
    'no-labels': 'dataset_annotation_provider dataset_annotation_model dataset_annotation_replace',
    'dataset-archive': 'dataset_export_val_split',
    'training-runtime': 'training_device training_batch training_workers training_cache training_amp',
    'training-stalled': 'training_epochs training_patience training_optimizer training_lr0 training_lrf training_weight_decay training_warmup_epochs training_close_mosaic training_fraction',
    'serial-hardware': 'hardware_serial_port hardware_serial_baud',
    'kmbox-net': 'kmbox_net_address kmbox_net_port kmbox_net_device_code',
    'kmbox-a': 'kmbox_a_vendor_id kmbox_a_product_id',
    'virtual-controller': 'controller_output_opt_in virtual_controller_mode virtual_controller_stick virtual_controller_strength virtual_controller_manual_priority',
    'virtual-mouse': 'virtual_mouse_opt_in',
    'model-shape': 'model_output_format model_coordinate_units',
    'boxes-misaligned': 'resize_mode',
    'segmentation-masks': 'overlay_segmentation_masks segmentation_mask_limit segmentation_mask_threshold',
}
FIELDS = {field: topic for topic, fields in GROUPS.items() for field in fields.split()}
TITLES = {
    'Detection model': 'model-not-listed', 'Inference provider': 'slow-processing',
    'Controller activation': 'controller-activation', 'Activation source': 'controller-activation',
    'Activation mode': 'controller-activation', 'Activation key': 'controller-activation',
    'Controller slot': 'controller-activation', 'Trigger thresholds': 'controller-activation',
    'Annotation backend': 'no-labels', 'Profile name': 'settings-not-sticking',
}
TOPICS = frozenset(GROUPS) | frozenset(TITLES.values())


@lru_cache(maxsize=16)
def _anchors(path, size, mtime):
    if size > 2 * 1024 * 1024:
        return frozenset()
    return frozenset(re.findall(r'\bid="([a-z0-9-]+)"', path.read_text(encoding='utf-8')))


def guide_topic(root, language, topic):
    if type(topic) is not str or topic not in TOPICS:
        return None
    try:
        path = guide_path(root, language)
        stat = path.stat()
        return topic if topic in _anchors(path, stat.st_size, stat.st_mtime_ns) else None
    except (OSError, UnicodeError, ValueError):
        return None


def add_guide_button(center, layout, title, control):
    # Titles are untranslated, code-owned setting labels. Fields are code-owned
    # widget properties. Neither source may supply an arbitrary URL or fragment.
    topic = FIELDS.get(control.property('guideField')) or TITLES.get(title)
    from vapor_lock_gui import ROOT
    if not topic or guide_topic(ROOT, center.ui_language, topic) is None:
        return None
    button = QToolButton()
    button.setObjectName('guideBulb')
    button.setIcon(symbol_icon('bulb', color=symbol_color(center)))
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(28, 28)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setProperty('_vaporlock_i18n_skip', True)
    button.setProperty('guideTitle', title)
    description = center.tf('Open guide: {topic}', topic=center.t(title))
    button.setToolTip(description)
    button.setAccessibleName(description)
    button.clicked.connect(lambda: center.open_settings_guide(topic))
    layout.addWidget(button)
    return button
