import type {CaptionDocument, EditPlan, Keyframe, LegacyClip, Ratio, SceneGraph, SceneItem, SceneTrack, Transform, TransitionSpec} from './types';

export const RATIOS: Record<Ratio, {width: number; height: number}> = {
  '9:16': {width: 1080, height: 1920},
  '4:5': {width: 1080, height: 1350},
  '1:1': {width: 1080, height: 1080},
  '16:9': {width: 1920, height: 1080},
};

export const DEFAULT_TRANSFORM: Transform = {
  x: 0,
  y: 0,
  scale_x: 1,
  scale_y: 1,
  rotation: 0,
  opacity: 1,
  anchor_x: 0.5,
  anchor_y: 0.5,
  border_radius: 0,
  crop: {left: 0, right: 0, top: 0, bottom: 0},
};

const clone = <T,>(value: T): T => structuredClone(value);
const trackId = (clipId: string, kind: string) => `${clipId}:track:${kind}`;
const itemId = (clipId: string, kind: string, suffix = 'main') => `${clipId}:item:${kind}:${suffix}`;

const createTracks = (clipId: string): SceneTrack[] => [
  ['source', 'Source video'], ['broll', 'B-roll'], ['graphics', 'Graphics'], ['captions', 'Captions'], ['music', 'Music'], ['sfx', 'Sound effects'], ['transitions', 'Transitions'],
].map(([kind, name], index) => ({id: trackId(clipId, kind), clip_id: clipId, kind, name, z_index: (index + 1) * 10, hidden: false, muted: false, locked: false}));

const fallbackCaptionDocument = (clip: LegacyClip): CaptionDocument => {
  const words = String(clip.transcript || '').trim().split(/\s+/).filter(Boolean);
  const durationMs = Math.max(1, Math.round((clip.end - clip.start) * 1000));
  const step = words.length ? durationMs / words.length : durationMs;
  const tokens = words.map((text, index) => ({
    id: `${clip.id}:word:${index}`,
    text,
    start_ms: Math.round(index * step),
    end_ms: Math.max(Math.round(index * step) + 1, Math.round((index + 1) * step)),
    source: 'fallback-even-spacing',
  }));
  return {id: `captions:${clip.id}`, clip_id: clip.id, text: words.join(' '), tokens, pages: [], timing_quality: tokens.length ? 'fallback' : 'empty'};
};

export const buildPages = (document: CaptionDocument, maxChars = 34): CaptionDocument => {
  const pages: CaptionDocument['pages'] = [];
  let current: typeof document.tokens = [];
  const flush = () => {
    if (!current.length) return;
    pages.push({
      id: `page:${pages.length}`,
      start_ms: current[0].start_ms,
      end_ms: current.at(-1)!.end_ms,
      token_ids: current.map((token) => token.id),
      text: current.map((token) => token.text).join(' ').replace(/\s+([,.;:!?%])/g, '$1'),
    });
    current = [];
  };
  for (const token of document.tokens) {
    const next = [...current, token];
    const text = next.map((value) => value.text).join(' ');
    if (current.length && (text.length > maxChars || next.length > 7 || next.at(-1)!.end_ms - next[0].start_ms > 2200)) flush();
    current.push(token);
    if (/[.!?]["'’”)]?$/.test(token.text) && current.length >= 2) flush();
  }
  flush();
  return {...document, pages, text: document.tokens.map((token) => token.text).join(' ').replace(/\s+([,.;:!?%])/g, '$1')};
};

export const ensureV3 = (input: EditPlan): EditPlan => {
  const plan = clone(input);
  plan.version = 3;
  plan.schema = 'clipper.edit-plan.v3';
  if (plan.scene_graph?.compositions && plan.scene_graph?.items) {
    for (const doc of Object.values(plan.scene_graph.caption_documents || {})) {
      const built = buildPages(doc);
      plan.scene_graph.caption_documents[built.id] = built;
    }
    return plan;
  }
  const graph: SceneGraph = {
    schema: 'clipper.scene-graph.v1',
    assets: {'source:primary': {id: 'source:primary', type: 'video', role: 'source', name: 'Primary source', path: null, storage_path: null, remote_url: null, metadata: {}}},
    compositions: {}, tracks: {}, items: {}, caption_documents: {}, transitions: {},
    templates: {active: 'clean-talking-head', overrides: {}},
    render: {preferred_backend: 'auto', draft_backend: 'browser', final_backends: ['ffmpeg', 'remotion'], quality: 'delivery'},
  };
  for (const clip of plan.clips || []) {
    const ratio = clip.ratios?.[0] || '9:16';
    const dims = RATIOS[ratio];
    const fps = 30;
    const duration = Math.max(1, Math.round((clip.end - clip.start) * fps));
    graph.compositions[clip.id] = {id: clip.id, name: clip.title, fps, ...dims, duration_frames: duration, base_ratio: ratio, ratio_variants: clip.ratios || [ratio], safe_zone_target: 'auto', background: '#11100e'};
    for (const track of createTracks(clip.id)) graph.tracks[track.id] = track;
    graph.items[itemId(clip.id, 'video')] = {
      id: itemId(clip.id, 'video'), clip_id: clip.id, track_id: trackId(clip.id, 'source'), type: 'video', asset_id: 'source:primary', name: 'Primary source',
      from_frame: 0, duration_frames: duration, trim_before_seconds: clip.start, transform: clone(DEFAULT_TRANSFORM), animations: {}, enabled: true, locked: false,
    };
    graph.items[itemId(clip.id, 'captions')] = {
      id: itemId(clip.id, 'captions'), clip_id: clip.id, track_id: trackId(clip.id, 'captions'), type: 'captions', caption_document_id: `captions:${clip.id}`,
      from_frame: 0, duration_frames: duration, transform: clone(DEFAULT_TRANSFORM), animations: {}, enabled: true, locked: false, style: {preset: clip.caption_preset || 'karaoke', safe_zone: true},
    };
    graph.items[itemId(clip.id, 'text', 'hook')] = {
      id: itemId(clip.id, 'text', 'hook'), clip_id: clip.id, track_id: trackId(clip.id, 'graphics'), type: 'text', name: 'Hook', text: clip.hook_text || clip.title,
      from_frame: 0, duration_frames: Math.min(duration, Math.round(2.8 * fps)), transform: clone(DEFAULT_TRANSFORM), animations: {opacity: [{frame: 0, value: 0, easing: 'ease-out'}, {frame: Math.min(5, duration - 1), value: 1, easing: 'ease-out'}]}, enabled: true, locked: false, style: {role: 'hook', safe_zone: true},
    };
    const caption = buildPages(fallbackCaptionDocument(clip));
    graph.caption_documents[caption.id] = caption;
  }
  plan.scene_graph = graph;
  return plan;
};

export const itemsForClip = (plan: EditPlan, clipId: string) => Object.values(plan.scene_graph.items).filter((item) => item.clip_id === clipId);
export const tracksForClip = (plan: EditPlan, clipId: string) => Object.values(plan.scene_graph.tracks).filter((track) => track.clip_id === clipId).sort((a, b) => a.z_index - b.z_index);
export const transitionsForClip = (plan: EditPlan, clipId: string) => Object.values(plan.scene_graph.transitions).filter((transition) => transition.clip_id === clipId).sort((a, b) => a.frame - b.frame);

export const updateItem = (plan: EditPlan, itemIdValue: string, updater: (item: SceneItem) => SceneItem): EditPlan => {
  const next = clone(plan);
  const item = next.scene_graph.items[itemIdValue];
  if (!item || item.locked) return plan;
  const composition = next.scene_graph.compositions[item.clip_id];
  const updated = updater(clone(item));
  updated.from_frame = Math.max(0, Math.min(composition.duration_frames - 1, Math.round(updated.from_frame)));
  updated.duration_frames = Math.max(1, Math.min(composition.duration_frames - updated.from_frame, Math.round(updated.duration_frames)));
  updated.transform.opacity = Math.max(0, Math.min(1, Number(updated.transform.opacity ?? 1)));
  updated.transform.scale_x = Math.max(0.01, Number(updated.transform.scale_x ?? 1));
  updated.transform.scale_y = Math.max(0.01, Number(updated.transform.scale_y ?? 1));
  next.scene_graph.items[itemIdValue] = updated;
  return next;
};

export const updateTrack = (plan: EditPlan, id: string, changes: Partial<SceneTrack>): EditPlan => {
  const next = clone(plan);
  if (!next.scene_graph.tracks[id]) return plan;
  next.scene_graph.tracks[id] = {...next.scene_graph.tracks[id], ...changes};
  return next;
};

export const splitItem = (plan: EditPlan, itemIdValue: string, frame: number): EditPlan => {
  const item = plan.scene_graph.items[itemIdValue];
  if (!item || item.locked) return plan;
  const local = Math.round(frame) - item.from_frame;
  if (local <= 0 || local >= item.duration_frames) return plan;
  const next = clone(plan);
  const left = next.scene_graph.items[itemIdValue];
  const originalDuration = left.duration_frames;
  left.duration_frames = local;
  const suffix = crypto.randomUUID().slice(0, 8);
  const rightId = `${itemIdValue}:split:${suffix}`;
  next.scene_graph.items[rightId] = {
    ...clone(left), id: rightId, name: `${left.name || left.type} split`, from_frame: item.from_frame + local,
    duration_frames: originalDuration - local,
    trim_before_seconds: Number(item.trim_before_seconds || 0) + local / (next.scene_graph.compositions[item.clip_id]?.fps || 30),
  };
  return next;
};

export const deleteItems = (plan: EditPlan, ids: string[]): EditPlan => {
  const next = clone(plan);
  for (const id of ids) if (!next.scene_graph.items[id]?.locked) delete next.scene_graph.items[id];
  return next;
};

export const duplicateItems = (plan: EditPlan, ids: string[]): {plan: EditPlan; ids: string[]} => {
  const next = clone(plan);
  const created: string[] = [];
  for (const id of ids) {
    const item = next.scene_graph.items[id];
    if (!item) continue;
    const copyId = `${id}:copy:${crypto.randomUUID().slice(0, 8)}`;
    next.scene_graph.items[copyId] = {...clone(item), id: copyId, from_frame: item.from_frame + 5, locked: false};
    created.push(copyId);
  }
  return {plan: next, ids: created};
};

export const addTextItem = (plan: EditPlan, clipId: string, frame: number, text = 'New text'): {plan: EditPlan; id: string} => {
  const next = clone(plan);
  const composition = next.scene_graph.compositions[clipId];
  const id = itemId(clipId, 'text', crypto.randomUUID().slice(0, 8));
  next.scene_graph.items[id] = {
    id, clip_id: clipId, track_id: trackId(clipId, 'graphics'), type: 'text', text, name: 'Text',
    from_frame: Math.max(0, Math.min(composition.duration_frames - 1, frame)), duration_frames: Math.min(composition.duration_frames - frame, composition.fps * 3),
    transform: clone(DEFAULT_TRANSFORM), animations: {}, enabled: true, locked: false,
    style: {fontSize: Math.round(composition.height * 0.055), fontWeight: 800, color: '#ffffff', background: 'transparent', safe_zone: true},
  };
  return {plan: next, id};
};

export const addShapeItem = (plan: EditPlan, clipId: string, frame: number, kind = 'rect'): {plan: EditPlan; id: string} => {
  const next = clone(plan);
  const composition = next.scene_graph.compositions[clipId];
  const id = itemId(clipId, 'shape', crypto.randomUUID().slice(0, 8));
  next.scene_graph.items[id] = {
    id, clip_id: clipId, track_id: trackId(clipId, 'graphics'), type: 'shape', name: 'Shape', from_frame: frame,
    duration_frames: Math.min(composition.duration_frames - frame, composition.fps * 3), transform: {...clone(DEFAULT_TRANSFORM), scale_x: 0.4, scale_y: 0.2}, animations: {}, enabled: true, locked: false,
    shape: {kind, fill: '#d6a77a', stroke: '#4d392b', strokeWidth: 2},
  };
  return {plan: next, id};
};

export const addTransition = (plan: EditPlan, clipId: string, frame: number, type: TransitionSpec['type'] = 'fade'): EditPlan => {
  const next = clone(plan);
  const composition = next.scene_graph.compositions[clipId];
  const id = `${clipId}:transition:${crypto.randomUUID().slice(0, 8)}`;
  next.scene_graph.transitions[id] = {id, clip_id: clipId, frame: Math.max(1, Math.min(composition.duration_frames - 1, frame)), duration_frames: Math.max(4, Math.round(composition.fps * 0.25)), type, strength: 1};
  return next;
};

export const setCaptionText = (plan: EditPlan, documentId: string, tokenId: string, text: string): EditPlan => {
  const next = clone(plan);
  const document = next.scene_graph.caption_documents[documentId];
  if (!document) return plan;
  const token = document.tokens.find((value) => value.id === tokenId);
  if (!token) return plan;
  token.text = text;
  next.scene_graph.caption_documents[documentId] = buildPages(document);
  return next;
};

export const addKeyframe = (plan: EditPlan, itemIdValue: string, property: string, frame: number, value: unknown, easing: Keyframe['easing'] = 'ease-out'): EditPlan => updateItem(plan, itemIdValue, (item) => {
  const values = [...(item.animations[property] || [])].filter((entry) => entry.frame !== frame);
  values.push({frame, value, easing});
  values.sort((a, b) => a.frame - b.frame);
  item.animations[property] = values;
  return item;
});

const easing = (t: number, name?: string) => {
  const value = Math.max(0, Math.min(1, t));
  if (name === 'step') return value < 1 ? 0 : 1;
  if (name === 'ease-in') return value * value;
  if (name === 'ease-out') return 1 - (1 - value) * (1 - value);
  if (name === 'ease-in-out') return value < 0.5 ? 2 * value * value : 1 - Math.pow(-2 * value + 2, 2) / 2;
  if (name === 'spring') return 1 - Math.cos(value * Math.PI * 2.5) * Math.exp(-6 * value);
  return value;
};

export const valueAtFrame = (frames: Keyframe[] | undefined, frame: number, fallback: number): number => {
  const numeric = (frames || []).filter((entry) => Number.isFinite(Number(entry.value))).map((entry) => ({...entry, value: Number(entry.value)})).sort((a, b) => a.frame - b.frame);
  if (!numeric.length) return fallback;
  if (frame <= numeric[0].frame) return numeric[0].value;
  if (frame >= numeric.at(-1)!.frame) return numeric.at(-1)!.value;
  for (let index = 0; index < numeric.length - 1; index++) {
    const left = numeric[index];
    const right = numeric[index + 1];
    if (frame < left.frame || frame > right.frame) continue;
    const t = easing((frame - left.frame) / Math.max(1, right.frame - left.frame), right.easing || left.easing);
    return left.value + (right.value - left.value) * t;
  }
  return fallback;
};

export const nextRevision = (plan: EditPlan, message: string, createdBy = 'user'): EditPlan => {
  const next = clone(plan);
  const previous = plan.revision || {};
  next.revision = {id: crypto.randomUUID(), parent_id: previous.id || null, sequence: Number(previous.sequence || 0) + 1, message: message.slice(0, 240), created_by: createdBy};
  return next;
};

export const normalizedPlanForSave = (plan: EditPlan): EditPlan => {
  const next = ensureV3(plan);
  for (const [id, composition] of Object.entries(next.scene_graph.compositions)) {
    composition.duration_frames = Math.max(1, Math.round(composition.duration_frames));
    composition.fps = Math.max(1, Math.min(240, Math.round(composition.fps)));
    next.scene_graph.compositions[id] = composition;
  }
  for (const item of Object.values(next.scene_graph.items)) {
    item.from_frame = Math.max(0, Math.round(item.from_frame));
    item.duration_frames = Math.max(1, Math.round(item.duration_frames));
    item.transform.opacity = Math.max(0, Math.min(1, item.transform.opacity));
  }
  return next;
};
