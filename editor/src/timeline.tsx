import React, {useMemo, useRef} from 'react';
import type {EditPlan, MediaAnalysis, SceneItem} from './types';
import {itemsForClip, tracksForClip, updateItem, updateTrack} from './plan';

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const TYPE_LABELS: Record<string, string> = {video: 'Video', audio: 'Audio', image: 'Image', gif: 'GIF', text: 'Text', solid: 'Solid', captions: 'Captions', shape: 'Shape', lottie: 'Lottie', rive: 'Rive', three: '3D', sfx: 'SFX'};

type TimelineProps = {
  plan: EditPlan;
  clipId: string;
  frame: number;
  selectedIds: string[];
  media?: MediaAnalysis | null;
  snap: boolean;
  onSeek: (frame: number) => void;
  onPlan: (plan: EditPlan, commit?: boolean) => void;
  onSelect: (ids: string[]) => void;
};

const Waveform: React.FC<{values: number[]}> = ({values}) => (
  <div className="waveform" aria-hidden="true">{values.map((value, index) => <i key={index} style={{height: `${Math.max(4, value * 86)}%`}} />)}</div>
);
const Filmstrip: React.FC<{frames: string[]}> = ({frames}) => (
  <div className="filmstrip" aria-hidden="true">{frames.map((src, index) => <img key={index} src={src} alt="" />)}</div>
);

const useDrag = (
  item: SceneItem,
  plan: EditPlan,
  clipId: string,
  mode: 'move' | 'trim-left' | 'trim-right',
  getWidth: () => number,
  snap: boolean,
  trackLocked: boolean,
  onPlan: TimelineProps['onPlan'],
) => {
  const composition = plan.scene_graph.compositions[clipId];
  return (event: React.PointerEvent) => {
    const width = Math.max(1, getWidth());
    if (item.locked || trackLocked || width <= 1) return;
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const originalFrom = item.from_frame;
    const originalDuration = item.duration_frames;
    const originalEnd = originalFrom + originalDuration;
    const fps = composition.fps;
    let latestPlan = plan;
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);

    const normalize = (frameValue: number) => {
      let value = Math.round(frameValue);
      if (snap) {
        const candidates = [0, composition.duration_frames];
        for (const other of itemsForClip(plan, clipId)) {
          if (other.id === item.id) continue;
          candidates.push(other.from_frame, other.from_frame + other.duration_frames);
        }
        const nearest = candidates.reduce((best, candidate) => Math.abs(candidate - value) < Math.abs(best - value) ? candidate : best, value);
        if (Math.abs(nearest - value) <= Math.max(2, Math.round(fps * 0.12))) value = nearest;
        else value = Math.round(value / 5) * 5;
      }
      return value;
    };

    const move = (moveEvent: PointerEvent) => {
      const delta = ((moveEvent.clientX - startX) / width) * composition.duration_frames;
      let next = plan;
      if (mode === 'move') {
        const from = clamp(normalize(originalFrom + delta), 0, composition.duration_frames - originalDuration);
        next = updateItem(plan, item.id, (value) => ({...value, from_frame: from}));
      } else if (mode === 'trim-left') {
        const from = clamp(normalize(originalFrom + delta), 0, originalEnd - 1);
        const shifted = from - originalFrom;
        next = updateItem(plan, item.id, (value) => ({...value, from_frame: from, duration_frames: originalDuration - shifted, trim_before_seconds: Math.max(0, Number(value.trim_before_seconds || 0) + shifted / fps)}));
      } else {
        const end = clamp(normalize(originalEnd + delta), originalFrom + 1, composition.duration_frames);
        next = updateItem(plan, item.id, (value) => ({...value, duration_frames: end - originalFrom}));
        // Alt/Option + right trim performs a rolling edit with the adjacent item:
        // total track duration stays fixed while the cut point moves.
        if (moveEvent.altKey) {
          const adjacent = itemsForClip(plan, clipId).find((other) => other.track_id === item.track_id && other.id !== item.id && Math.abs(other.from_frame - originalEnd) <= 2 && !other.locked);
          if (adjacent) {
            const shift = end - originalEnd;
            const nextDuration = adjacent.duration_frames - shift;
            if (nextDuration >= 1) {
              next = updateItem(next, adjacent.id, (value) => ({
                ...value,
                from_frame: end,
                duration_frames: nextDuration,
                trim_before_seconds: Math.max(0, Number(value.trim_before_seconds || 0) + shift / fps),
              }));
            }
          }
        }
      }
      latestPlan = next;
      onPlan(next, false);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      onPlan(latestPlan, true);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up, {once: true});
  };
};

const TimelineItem: React.FC<{
  item: SceneItem;
  plan: EditPlan;
  clipId: string;
  selected: boolean;
  getWidth: () => number;
  snap: boolean;
  trackLocked: boolean;
  onPlan: TimelineProps['onPlan'];
  onSelect: TimelineProps['onSelect'];
  selectedIds: string[];
}> = ({item, plan, clipId, selected, getWidth, snap, trackLocked, onPlan, onSelect, selectedIds}) => {
  const composition = plan.scene_graph.compositions[clipId];
  const left = (item.from_frame / composition.duration_frames) * 100;
  const itemWidth = (item.duration_frames / composition.duration_frames) * 100;
  const move = useDrag(item, plan, clipId, 'move', getWidth, snap, trackLocked, onPlan);
  const trimLeft = useDrag(item, plan, clipId, 'trim-left', getWidth, snap, trackLocked, onPlan);
  const trimRight = useDrag(item, plan, clipId, 'trim-right', getWidth, snap, trackLocked, onPlan);
  const locked = item.locked || trackLocked;
  const select = (event: React.MouseEvent) => {
    event.stopPropagation();
    if (event.metaKey || event.ctrlKey || event.shiftKey) onSelect(selected ? selectedIds.filter((id) => id !== item.id) : [...selectedIds, item.id]);
    else onSelect([item.id]);
  };
  return <div
    className={`timeline-item type-${item.type}${selected ? ' selected' : ''}${locked ? ' locked' : ''}`}
    style={{left: `${left}%`, width: `${Math.max(0.35, itemWidth)}%`}}
    onMouseDown={select}
    onPointerDown={move}
    title={`${item.name || TYPE_LABELS[item.type] || item.type} · ${item.from_frame}–${item.from_frame + item.duration_frames}${locked ? ' · locked' : ''}`}
  >
    <button className="trim-handle left" onPointerDown={trimLeft} aria-label="Trim item start" disabled={locked} />
    <span>{item.name || TYPE_LABELS[item.type] || item.type}</span>
    {item.animations && Object.values(item.animations).some((values) => values?.length) && <b className="keyframe-dot">◆</b>}
    <button className="trim-handle right" onPointerDown={trimRight} aria-label="Trim item end. Hold Alt/Option for rolling edit." disabled={locked} />
  </div>;
};

export const Timeline: React.FC<TimelineProps> = ({plan, clipId, frame, selectedIds, media, snap, onSeek, onPlan, onSelect}) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const composition = plan.scene_graph.compositions[clipId];
  const tracks = useMemo(() => tracksForClip(plan, clipId), [plan, clipId]);
  const items = useMemo(() => itemsForClip(plan, clipId), [plan, clipId]);
  const getWidth = () => bodyRef.current?.getBoundingClientRect().width || 1;
  const seekFromPointer = (event: React.PointerEvent | React.MouseEvent) => {
    const rect = bodyRef.current?.getBoundingClientRect();
    if (!rect) return;
    const localX = clamp(event.clientX - rect.left, 0, rect.width);
    onSeek(Math.round((localX / rect.width) * Math.max(0, composition.duration_frames - 1)));
  };
  const startPlayheadDrag = (event: React.PointerEvent) => {
    event.preventDefault();
    const move = (moveEvent: PointerEvent) => {
      const rect = bodyRef.current?.getBoundingClientRect();
      if (!rect) return;
      onSeek(Math.round((clamp(moveEvent.clientX - rect.left, 0, rect.width) / rect.width) * Math.max(0, composition.duration_frames - 1)));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', () => window.removeEventListener('pointermove', move), {once: true});
  };
  return <section className="timeline-shell" aria-label="Timeline editor">
    <div className="timeline-topline">
      <span>{composition.fps} fps</span>
      <span>{(frame / composition.fps).toFixed(2)}s / {(composition.duration_frames / composition.fps).toFixed(2)}s</span>
      <span>{snap ? 'Snap on' : 'Snap off'}</span>
      <span>Alt/Option + trim = rolling edit</span>
    </div>
    <div className="timeline-scroll" ref={scrollRef}>
      <div className="timeline-grid">
        <div className="track-labels">
          {tracks.map((track) => <div className="track-label" key={track.id}>
            <span>{track.name}</span>
            <div>
              <button className={track.hidden ? 'active' : ''} onClick={() => onPlan(updateTrack(plan, track.id, {hidden: !track.hidden}), true)} title="Hide/show track">◉</button>
              <button className={track.muted ? 'active' : ''} onClick={() => onPlan(updateTrack(plan, track.id, {muted: !track.muted}), true)} title="Mute track">M</button>
              <button className={track.locked ? 'active' : ''} onClick={() => onPlan(updateTrack(plan, track.id, {locked: !track.locked}), true)} title="Lock track">⌁</button>
            </div>
          </div>)}
        </div>
        <div className="timeline-body" ref={bodyRef} onMouseDown={(event) => { if (event.target === event.currentTarget) { onSelect([]); seekFromPointer(event); } }}>
          <div className="time-ruler" onPointerDown={seekFromPointer}>{Array.from({length: 11}, (_, index) => <span key={index} style={{left: `${index * 10}%`}}>{((composition.duration_frames / composition.fps) * index / 10).toFixed(index % 2 ? 1 : 0)}s</span>)}</div>
          {tracks.map((track) => {
            const rowItems = items.filter((item) => item.track_id === track.id);
            const isSource = track.kind === 'source';
            return <div className={`track-row${track.hidden ? ' hidden-track' : ''}`} key={track.id} onMouseDown={(event) => { if (event.target === event.currentTarget) { onSelect([]); seekFromPointer(event); } }}>
              {isSource && media?.filmstrip?.length ? <Filmstrip frames={media.filmstrip} /> : null}
              {isSource && media?.waveform?.length ? <Waveform values={media.waveform} /> : null}
              {rowItems.map((item) => <TimelineItem key={item.id} item={item} plan={plan} clipId={clipId} selected={selectedIds.includes(item.id)} selectedIds={selectedIds} getWidth={getWidth} snap={snap} trackLocked={track.locked} onPlan={onPlan} onSelect={onSelect} />)}
            </div>;
          })}
          <div className="playhead" style={{left: `${(frame / Math.max(1, composition.duration_frames - 1)) * 100}%`}} onPointerDown={startPlayheadDrag}><i /></div>
        </div>
      </div>
    </div>
  </section>;
};