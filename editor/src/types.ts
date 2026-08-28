export type Ratio = '9:16' | '4:5' | '1:1' | '16:9';
export type EasingName = 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out' | 'spring' | 'step';
export type ItemType = 'video' | 'audio' | 'image' | 'gif' | 'text' | 'solid' | 'captions' | 'shape' | 'lottie' | 'rive' | 'three' | 'sfx';

export type Keyframe = {
  frame: number;
  value: unknown;
  easing?: EasingName;
};

export type Transform = {
  x: number;
  y: number;
  scale_x: number;
  scale_y: number;
  rotation: number;
  opacity: number;
  anchor_x: number;
  anchor_y: number;
  border_radius: number;
  crop: {left: number; right: number; top: number; bottom: number};
};

export type SceneAsset = {
  id: string;
  type: 'video' | 'audio' | 'image' | 'gif' | 'json' | string;
  role?: string;
  name?: string;
  path?: string | null;
  storage_path?: string | null;
  remote_url?: string | null;
  public_path?: string | null;
  metadata?: Record<string, unknown>;
};

export type CompositionConfig = {
  id: string;
  name?: string;
  fps: number;
  width: number;
  height: number;
  duration_frames: number;
  base_ratio?: Ratio;
  ratio_variants?: Ratio[];
  safe_zone_target?: string;
  background?: string;
};

export type SceneTrack = {
  id: string;
  clip_id: string;
  kind: string;
  name: string;
  z_index: number;
  hidden: boolean;
  muted: boolean;
  locked: boolean;
};

export type SceneItem = {
  id: string;
  clip_id: string;
  track_id: string;
  type: ItemType;
  asset_id?: string;
  caption_document_id?: string;
  name?: string;
  text?: string;
  from_frame: number;
  duration_frames: number;
  trim_before_seconds?: number;
  source_intervals?: Array<{start: number; end: number}>;
  transform: Transform;
  animations: Record<string, Keyframe[]>;
  volume?: number;
  fade_in_frames?: number;
  fade_out_frames?: number;
  enabled: boolean;
  locked: boolean;
  style?: Record<string, unknown>;
  shape?: {kind?: string; fill?: string; stroke?: string; strokeWidth?: number};
  animation_data?: unknown;
  automation?: Record<string, unknown>;
  [key: string]: unknown;
};

export type CaptionToken = {
  id: string;
  text: string;
  start_ms: number;
  end_ms: number;
  confidence?: number | null;
  source?: string;
};

export type CaptionPage = {
  id: string;
  start_ms: number;
  end_ms: number;
  token_ids: string[];
  text: string;
};

export type CaptionDocument = {
  id: string;
  clip_id: string;
  language?: string | null;
  text: string;
  tokens: CaptionToken[];
  pages: CaptionPage[];
  source?: string;
  timing_quality?: string;
};

export type TransitionSpec = {
  id: string;
  clip_id: string;
  frame: number;
  duration_frames: number;
  type: 'cut' | 'fade' | 'push' | 'zoom' | 'blur';
  direction?: 'left' | 'right' | 'up' | 'down';
  strength?: number;
};

export type SceneGraph = {
  schema: string;
  assets: Record<string, SceneAsset>;
  compositions: Record<string, CompositionConfig>;
  tracks: Record<string, SceneTrack>;
  items: Record<string, SceneItem>;
  caption_documents: Record<string, CaptionDocument>;
  transitions: Record<string, TransitionSpec>;
  templates: {active?: string; overrides?: Record<string, unknown>};
  render: {preferred_backend?: string; draft_backend?: string; final_backends?: string[]; quality?: string};
};

export type LegacyClip = {
  id: string;
  enabled: boolean;
  start: number;
  end: number;
  title: string;
  transcript?: string;
  ratios: Ratio[];
  caption_preset?: string;
  hook_text?: string | null;
  [key: string]: unknown;
};

export type EditPlan = {
  version: number;
  schema: string;
  project_id: string;
  brand: Record<string, unknown>;
  music_path?: string | null;
  defaults: Record<string, unknown>;
  clips: LegacyClip[];
  scene_graph: SceneGraph;
  revision?: {id?: string; parent_id?: string | null; sequence?: number; message?: string; created_by?: string};
  automation_mode?: string;
  [key: string]: unknown;
};

export type ProjectRecord = {
  id: string;
  userId: string;
  jobId?: string;
  localProjectId?: string;
  sourceName?: string;
  sourceStoragePath?: string | null;
  sourceUrl?: string | null;
  ratios?: Ratio[];
  outputs?: Array<{
    clipId: string;
    aspectRatio: Ratio;
    storagePath: string;
    thumbnailStoragePath?: string | null;
    filename?: string;
  }>;
  clipMetadata?: Record<string, {candidate?: LegacyClip; hookText?: string; visualCues?: Array<Record<string, unknown>>; autoProfile?: Record<string, unknown>}>;
  editPlanStoragePath?: string;
  status?: string;
  [key: string]: unknown;
};

export type MediaAnalysis = {
  duration: number;
  width: number;
  height: number;
  fps?: number | null;
  waveform: number[];
  filmstrip: string[];
};
