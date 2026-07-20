"""Controlled vocabularies for the production domain."""

from enum import StrEnum


class ProductionJobStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_RETRY = "waiting_for_retry"
    NEEDS_USER_ACTION = "needs_user_action"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class ProductionStage(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    SCRIPTING = "scripting"
    SCENE_PLANNING = "scene_planning"
    ACQUIRING_ASSETS = "acquiring_assets"
    GENERATING_NARRATION = "generating_narration"
    PREPARING_MUSIC = "preparing_music"
    GENERATING_SUBTITLES = "generating_subtitles"
    BUILDING_TIMELINE = "building_timeline"
    RENDERING_LONG_FORM = "rendering_long_form"
    VALIDATING_RENDER = "validating_render"
    HANDING_OFF_TO_CLIPS = "handing_off_to_clips"
    WAITING_FOR_CLIPS = "waiting_for_clips"
    COMPLETED = "completed"


class AssetType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class MotionType(StrEnum):
    STATIC = "static"
    SLOW_ZOOM_IN = "slow_zoom_in"
    SLOW_ZOOM_OUT = "slow_zoom_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"


class TransitionType(StrEnum):
    CUT = "cut"
    CROSS_DISSOLVE = "cross_dissolve"
    FADE_TO_BLACK = "fade_to_black"


class ArtifactType(StrEnum):
    PRODUCTION_PLAN = "production_plan"
    PRODUCTION_SCRIPT = "production_script"
    SOURCE_IMAGE = "source_image"
    SOURCE_VIDEO = "source_video"
    NARRATION = "narration"
    MUSIC = "music"
    SUBTITLES = "subtitles"
    EDIT_PACKAGE = "edit_package"
    EDITOR_PROJECT = "editor_project"
    LONG_FORM_RENDER = "long_form_render"
    CLIP = "clip"
    MANIFEST = "manifest"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    INVALID = "invalid"
    OBSOLETE = "obsolete"
