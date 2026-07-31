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
    VISUAL_ASSET_PLANNING = "visual_asset_planning"
    ACQUIRING_ASSETS = "acquiring_assets"
    GENERATING_VIDEO_CLIPS = "generating_video_clips"
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
    PRODUCTION_SCENE_PLAN = "production_scene_plan"
    PRODUCTION_VISUAL_ASSET_PLAN = "production_visual_asset_plan"
    PRODUCTION_IMAGE_ACQUISITION_MANIFEST = "production_image_acquisition_manifest"
    PRODUCTION_VIDEO_CLIP_MANIFEST = "production_video_clip_manifest"
    PRODUCTION_SPEECH_GENERATION_MANIFEST = "production_speech_generation_manifest"
    PRODUCTION_AUDIO_DESIGN_MANIFEST = "production_audio_design_manifest"
    MEDIA_COMPOSITION_PLAN = "media_composition_plan"
    MEDIA_COMPOSITION_MANIFEST = "media_composition_manifest"
    LOCAL_RENDER_REQUEST = "local_render_request"
    FFMPEG_EXECUTION_PLAN = "ffmpeg_execution_plan"
    RENDER_EXECUTION_MANIFEST = "render_execution_manifest"
    FINAL_RENDER_VALIDATION = "final_render_validation"
    SOURCE_IMAGE = "source_image"
    SOURCE_VIDEO_CLIP = "source_video_clip"
    SOURCE_VIDEO = "source_video"
    NARRATION = "narration"
    MUSIC = "music"
    SOUND_EFFECT = "sound_effect"
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
