import React from 'react';
import type {EditPlan, SceneItem} from './types';
import {addKeyframe, setCaptionText, updateItem} from './plan';

const NumberField: React.FC<{label: string; value: number; step?: number; min?: number; max?: number; onChange: (value: number) => void}> = ({label, value, step = 1, min, max, onChange}) => (
  <label className="inspector-field"><span>{label}</span><input type="number" value={Number.isFinite(value) ? value : 0} step={step} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} /></label>
);

const RangeField: React.FC<{label: string; value: number; min: number; max: number; step?: number; onChange: (value: number) => void; onCommit?: () => void}> = ({label, value, min, max, step = 0.01, onChange, onCommit}) => (
  <label className="inspector-range"><span>{label}<b>{Number(value).toFixed(step < 1 ? 2 : 0)}</b></span><input type="range" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} onPointerUp={onCommit} onKeyUp={onCommit} /></label>
);

type InspectorProps = {
  plan: EditPlan;
  clipId: string;
  selectedIds: string[];
  frame: number;
  onPlan: (plan: EditPlan, commit?: boolean) => void;
  onCommit?: () => void;
};

const ItemInspector: React.FC<{plan: EditPlan; item: SceneItem; frame: number; onPlan: InspectorProps['onPlan']; onCommit?: () => void}> = ({plan, item, frame, onPlan, onCommit}) => {
  const composition = plan.scene_graph.compositions[item.clip_id];
  const patch = (changes: Partial<SceneItem>, commit = true) => onPlan(updateItem(plan, item.id, (value) => ({...value, ...changes})), commit);
  const patchTransform = (changes: Partial<SceneItem['transform']>, commit = true) => onPlan(updateItem(plan, item.id, (value) => ({...value, transform: {...value.transform, ...changes}})), commit);
  const setLocked = (locked: boolean) => {
    const next = structuredClone(plan);
    if (next.scene_graph.items[item.id]) next.scene_graph.items[item.id].locked = locked;
    onPlan(next, true);
  };
  const localFrame = Math.max(0, frame - item.from_frame);
  const addFrame = (property: string, value: number) => onPlan(addKeyframe(plan, item.id, property, localFrame, value), true);
  const style = item.style || {};
  const locked = item.locked || plan.scene_graph.tracks[item.track_id]?.locked;
  return <div className="inspector-stack">
    <div className="inspector-heading"><div><small>{item.type}</small><h3>{item.name || item.type}</h3></div><label className="mini-check"><input type="checkbox" checked={item.enabled} disabled={locked} onChange={(event) => patch({enabled: event.target.checked})} />On</label></div>
    {plan.scene_graph.tracks[item.track_id]?.locked && <p className="inspector-note">This track is locked. Unlock the track in the timeline to edit its layers.</p>}
    <fieldset disabled={locked} style={{display:'contents'}}>
      <div className="inspector-grid two-col">
        <NumberField label="Start frame" value={item.from_frame} min={0} max={composition.duration_frames - 1} onChange={(value) => patch({from_frame: value})} />
        <NumberField label="Duration" value={item.duration_frames} min={1} max={composition.duration_frames} onChange={(value) => patch({duration_frames: value})} />
        <NumberField label="X" value={item.transform.x} onChange={(value) => patchTransform({x: value})} />
        <NumberField label="Y" value={item.transform.y} onChange={(value) => patchTransform({y: value})} />
      </div>
      <RangeField label="Scale X" value={item.transform.scale_x} min={0.1} max={3} onChange={(value) => patchTransform({scale_x: value}, false)} onCommit={onCommit} />
      <RangeField label="Scale Y" value={item.transform.scale_y} min={0.1} max={3} onChange={(value) => patchTransform({scale_y: value}, false)} onCommit={onCommit} />
      <RangeField label="Rotation" value={item.transform.rotation} min={-180} max={180} step={1} onChange={(value) => patchTransform({rotation: value}, false)} onCommit={onCommit} />
      <RangeField label="Opacity" value={item.transform.opacity} min={0} max={1} onChange={(value) => patchTransform({opacity: value}, false)} onCommit={onCommit} />
      <RangeField label="Corner radius" value={item.transform.border_radius} min={0} max={160} step={1} onChange={(value) => patchTransform({border_radius: value}, false)} onCommit={onCommit} />
      <details className="inspector-details">
        <summary>Crop</summary>
        {(['left','right','top','bottom'] as const).map((side) => <RangeField key={side} label={side[0].toUpperCase() + side.slice(1)} value={item.transform.crop?.[side] || 0} min={0} max={0.8} onChange={(value) => patchTransform({crop: {...item.transform.crop, [side]: value}}, false)} onCommit={onCommit} />)}
      </details>
      {(item.type === 'video' || item.type === 'audio' || item.type === 'sfx') && <details className="inspector-details" open>
        <summary>Audio</summary>
        <RangeField label="Volume" value={Number(item.volume ?? 1)} min={0} max={2} onChange={(value) => patch({volume: value}, false)} onCommit={onCommit} />
        <div className="inspector-grid two-col"><NumberField label="Fade in frames" value={Number(item.fade_in_frames || 0)} min={0} onChange={(value) => patch({fade_in_frames: value})} /><NumberField label="Fade out frames" value={Number(item.fade_out_frames || 0)} min={0} onChange={(value) => patch({fade_out_frames: value})} /></div>
      </details>}
      {item.type === 'text' && <details className="inspector-details" open>
        <summary>Text</summary>
        <label className="inspector-field"><span>Content</span><textarea value={String(item.text || '')} onChange={(event) => patch({text: event.target.value}, false)} onBlur={onCommit} /></label>
        <div className="inspector-grid two-col">
          <NumberField label="Font size" value={Number(style.fontSize || 86)} min={12} max={400} onChange={(value) => patch({style: {...style, fontSize: value}})} />
          <NumberField label="Weight" value={Number(style.fontWeight || 800)} min={100} max={1000} step={100} onChange={(value) => patch({style: {...style, fontWeight: value}})} />
        </div>
        <label className="inspector-field"><span>Font family</span><input value={String(style.fontFamily || '')} onChange={(event) => patch({style: {...style, fontFamily: event.target.value},}, false)} onBlur={onCommit} /></label>
        <div className="color-row"><label>Text <input type="color" value={String(style.color || '#ffffff')} onChange={(event) => patch({style: {...style, color: event.target.value}})} /></label><label>Box <input type="color" value={String(style.background || '#11100e').startsWith('#') ? String(style.background || '#11100e') : '#11100e'} onChange={(event) => patch({style: {...style, background: event.target.value}})} /></label></div>
      </details>}
      {item.type === 'shape' && <details className="inspector-details" open>
        <summary>Shape</summary>
        <label className="inspector-field"><span>Kind</span><select value={String(item.shape?.kind || 'rect')} onChange={(event) => patch({shape: {...item.shape, kind: event.target.value}})}><option value="rect">Rectangle</option><option value="circle">Circle</option><option value="triangle">Triangle</option><option value="arrow">Arrow</option><option value="callout">Callout</option><option value="star">Star</option></select></label>
        <div className="color-row"><label>Fill <input type="color" value={String(item.shape?.fill || '#d6a77a')} onChange={(event) => patch({shape: {...item.shape, fill: event.target.value}})} /></label><label>Stroke <input type="color" value={String(item.shape?.stroke || '#4d392b')} onChange={(event) => patch({shape: {...item.shape, stroke: event.target.value}})} /></label></div>
      </details>}
      <details className="inspector-details">
        <summary>Keyframes at playhead</summary>
        <p className="inspector-note">Add a keyframe at local frame {localFrame}. Values interpolate deterministically during preview and Remotion render.</p>
        <div className="keyframe-buttons"><button onClick={() => addFrame('x', item.transform.x)}>X</button><button onClick={() => addFrame('y', item.transform.y)}>Y</button><button onClick={() => addFrame('scale', item.transform.scale_x)}>Scale</button><button onClick={() => addFrame('rotation', item.transform.rotation)}>Rotate</button><button onClick={() => addFrame('opacity', item.transform.opacity)}>Opacity</button></div>
        {Object.entries(item.animations || {}).filter(([, values]) => values?.length).map(([property, values]) => <div className="keyframe-list" key={property}><strong>{property}</strong><span>{values.map((value) => `${value.frame}:${String(value.value)}`).join(' · ')}</span></div>)}
      </details>
    </fieldset>
    <label className="mini-check"><input type="checkbox" checked={item.locked} onChange={(event) => setLocked(event.target.checked)} />Lock layer</label>
  </div>;
};

const CaptionInspector: React.FC<{plan: EditPlan; item: SceneItem; frame: number; onPlan: InspectorProps['onPlan']; onCommit?: () => void}> = ({plan, item, frame, onPlan, onCommit}) => {
  const document = item.caption_document_id ? plan.scene_graph.caption_documents[item.caption_document_id] : null;
  if (!document) return <div className="inspector-empty">No caption document is attached.</div>;
  const fps = plan.scene_graph.compositions[item.clip_id].fps;
  const timeMs = ((frame - item.from_frame) / fps) * 1000;
  return <div className="caption-editor"><p className="inspector-note">Correct text directly. Timing for untouched words stays unchanged.</p>{document.tokens.map((token) => <label key={token.id} className={timeMs >= token.start_ms && timeMs <= token.end_ms ? 'active' : ''}><span>{(token.start_ms / 1000).toFixed(2)}</span><input value={token.text} onChange={(event) => onPlan(setCaptionText(plan, document.id, token.id, event.target.value), false)} onBlur={onCommit} /></label>)}</div>;
};

export const Inspector: React.FC<InspectorProps> = ({plan, clipId, selectedIds, frame, onPlan, onCommit}) => {
  if (!selectedIds.length) {
    const composition = plan.scene_graph.compositions[clipId];
    return <aside className="inspector"><div className="inspector-stack"><div className="inspector-heading"><div><small>composition</small><h3>{composition.name || clipId}</h3></div></div><div className="inspector-grid two-col"><div className="stat"><span>Canvas</span><strong>{composition.width}×{composition.height}</strong></div><div className="stat"><span>FPS</span><strong>{composition.fps}</strong></div><div className="stat"><span>Frames</span><strong>{composition.duration_frames}</strong></div><div className="stat"><span>Ratio</span><strong>{composition.base_ratio || '9:16'}</strong></div></div><p className="inspector-note">Select a layer on the canvas or timeline to edit it. Shift/Ctrl-click selects more than one.</p></div></aside>;
  }
  if (selectedIds.length > 1) return <aside className="inspector"><div className="inspector-stack"><div className="inspector-heading"><div><small>selection</small><h3>{selectedIds.length} layers</h3></div></div><p className="inspector-note">Move, duplicate or delete the selection together from the toolbar. Select one layer for precise inspector controls.</p></div></aside>;
  const item = plan.scene_graph.items[selectedIds[0]];
  if (!item) return <aside className="inspector"><div className="inspector-empty">Selection is no longer available.</div></aside>;
  return <aside className="inspector"><ItemInspector plan={plan} item={item} frame={frame} onPlan={onPlan} onCommit={onCommit} />{item.type === 'captions' && <CaptionInspector plan={plan} item={item} frame={frame} onPlan={onPlan} onCommit={onCommit} />}</aside>;
};