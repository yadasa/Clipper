import React, {useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react';
import {Player, type CallbackListener, type PlayerRef} from '@remotion/player';
import {renderMediaOnWeb} from '@remotion/web-renderer';
import type {User} from 'firebase/auth';
import {ClipperComposition} from './composition';
import {Inspector} from './inspector';
import {Timeline} from './timeline';
import {analyzeMedia} from './media';
import {applyTemplate, MOTION_TEMPLATES} from './templates';
import {
  addShapeItem,
  addTextItem,
  addTransition,
  deleteItems,
  duplicateItems,
  ensureV3,
  itemsForClip,
  nextRevision,
  RATIOS,
  splitItem,
  updateItem,
} from './plan';
import {
  listRevisions,
  loadProject,
  observeUser,
  queueRerender,
  saveRevision,
  saveWorkingEdit,
  toggleAuth,
  uploadEditorAsset,
} from './firebase';
import type {EditPlan, MediaAnalysis, ProjectRecord, Ratio, SceneAsset, SceneItem} from './types';

const deepEqual = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const clone = <T,>(value: T): T => structuredClone(value);

const useCurrentPlayerFrame = (ref: React.RefObject<PlayerRef | null>) => {
  const subscribe = useCallback((onStoreChange: () => void) => {
    const current = ref.current;
    if (!current) return () => undefined;
    const updater: CallbackListener<'frameupdate'> = () => onStoreChange();
    current.addEventListener('frameupdate', updater);
    return () => current.removeEventListener('frameupdate', updater);
  }, [ref]);
  return useSyncExternalStore(subscribe, () => ref.current?.getCurrentFrame() ?? 0, () => 0);
};

type HistoryState = {
  current: EditPlan;
  committed: EditPlan;
  past: EditPlan[];
  future: EditPlan[];
};

const usePlanHistory = (initial: EditPlan) => {
  const [state, setState] = useState<HistoryState>({current: initial, committed: initial, past: [], future: []});
  const apply = useCallback((next: EditPlan, commit = true) => {
    setState((previous) => {
      if (deepEqual(previous.current, next)) return previous;
      if (!commit) return {...previous, current: next};
      const past = deepEqual(previous.committed, next) ? previous.past : [...previous.past, previous.committed].slice(-60);
      return {current: next, committed: next, past, future: []};
    });
  }, []);
  const commitCurrent = useCallback((message = 'Edit') => {
    setState((previous) => {
      if (deepEqual(previous.committed, previous.current)) return previous;
      const next = nextRevision(previous.current, message);
      return {current: next, committed: next, past: [...previous.past, previous.committed].slice(-60), future: []};
    });
  }, []);
  const undo = useCallback(() => setState((previous) => {
    const prior = previous.past.at(-1);
    if (!prior) return previous;
    return {current: prior, committed: prior, past: previous.past.slice(0, -1), future: [previous.committed, ...previous.future].slice(0, 60)};
  }), []);
  const redo = useCallback(() => setState((previous) => {
    const next = previous.future[0];
    if (!next) return previous;
    return {current: next, committed: next, past: [...previous.past, previous.committed].slice(-60), future: previous.future.slice(1)};
  }), []);
  const reset = useCallback((plan: EditPlan) => setState({current: plan, committed: plan, past: [], future: []}), []);
  return {state, apply, commitCurrent, undo, redo, reset};
};

const projectIdFromUrl = () => new URLSearchParams(location.search).get('project') || '';

const safeDownload = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
};

const effectiveLock = (plan: EditPlan, item?: SceneItem | null) => !!item && (item.locked || plan.scene_graph.tracks[item.track_id]?.locked);

const CanvasHandles: React.FC<{
  plan: EditPlan;
  clipId: string;
  item: SceneItem | null;
  onPlan: (plan: EditPlan, commit?: boolean) => void;
  onCommit: () => void;
}> = ({plan, clipId, item, onPlan, onCommit}) => {
  const hostRef = useRef<HTMLDivElement>(null);
  if (!item || effectiveLock(plan, item) || ['audio', 'sfx'].includes(item.type)) return null;
  const composition = plan.scene_graph.compositions[clipId];
  const startDrag = (event: React.PointerEvent, mode: 'move' | 'scale' | 'rotate') => {
    event.preventDefault();
    event.stopPropagation();
    const rect = hostRef.current?.parentElement?.getBoundingClientRect();
    if (!rect) return;
    const start = {x: event.clientX, y: event.clientY, transform: clone(item.transform)};
    let latest = plan;
    const move = (moveEvent: PointerEvent) => {
      const dx = ((moveEvent.clientX - start.x) / Math.max(1, rect.width)) * composition.width;
      const dy = ((moveEvent.clientY - start.y) / Math.max(1, rect.height)) * composition.height;
      latest = updateItem(plan, item.id, (value) => {
        const transform = {...value.transform};
        if (mode === 'move') {
          transform.x = start.transform.x + dx;
          transform.y = start.transform.y + dy;
        } else if (mode === 'scale') {
          const delta = (dx + dy) / Math.max(composition.width, composition.height);
          transform.scale_x = Math.max(0.08, start.transform.scale_x + delta * 2.2);
          transform.scale_y = Math.max(0.08, start.transform.scale_y + delta * 2.2);
        } else {
          const centerX = rect.left + rect.width / 2;
          const centerY = rect.top + rect.height / 2;
          const a0 = Math.atan2(start.y - centerY, start.x - centerX);
          const a1 = Math.atan2(moveEvent.clientY - centerY, moveEvent.clientX - centerX);
          transform.rotation = start.transform.rotation + ((a1 - a0) * 180) / Math.PI;
        }
        return {...value, transform};
      });
      onPlan(latest, false);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      onPlan(latest, true);
      onCommit();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up, {once: true});
  };
  return <div ref={hostRef} className="canvas-handles" aria-label="Canvas transform controls">
    <button className="canvas-move" onPointerDown={(event) => startDrag(event, 'move')} title="Drag layer">Move</button>
    <button className="canvas-scale" onPointerDown={(event) => startDrag(event, 'scale')} title="Scale layer">↘</button>
    <button className="canvas-rotate" onPointerDown={(event) => startDrag(event, 'rotate')} title="Rotate layer">↻</button>
  </div>;
};

const AssetBin: React.FC<{
  plan: EditPlan;
  project: ProjectRecord;
  user: User;
  clipId: string;
  frame: number;
  onPlan: (plan: EditPlan, commit?: boolean) => void;
  onSelect: (ids: string[]) => void;
}> = ({plan, project, user, clipId, frame, onPlan, onSelect}) => {
  const [uploading, setUploading] = useState(false);
  const assets = Object.values(plan.scene_graph.assets).filter((asset) => asset.role !== 'source');
  const addAssetToTimeline = (asset: SceneAsset) => {
    const next = clone(plan);
    const composition = next.scene_graph.compositions[clipId];
    const type = asset.type.startsWith('audio') ? 'audio' : asset.type === 'image' ? 'image' : asset.type === 'gif' ? 'gif' : 'video';
    const kind = type === 'audio' ? 'music' : 'broll';
    const id = `${clipId}:item:${kind}:${crypto.randomUUID().slice(0, 8)}`;
    next.scene_graph.items[id] = {
      id, clip_id: clipId, track_id: `${clipId}:track:${kind}`, type, asset_id: asset.id, name: asset.name || 'Asset',
      from_frame: Math.max(0, Math.min(composition.duration_frames - 1, frame)), duration_frames: Math.min(composition.fps * 4, composition.duration_frames - Math.max(0, frame)),
      trim_before_seconds: 0, transform: {x: 0, y: 0, scale_x: type === 'image' ? 0.55 : 1, scale_y: type === 'image' ? 0.55 : 1, rotation: 0, opacity: 1, anchor_x: 0.5, anchor_y: 0.5, border_radius: type === 'image' ? 24 : 0, crop: {left: 0, right: 0, top: 0, bottom: 0}},
      animations: {}, volume: type === 'audio' ? 0.2 : 0, fade_in_frames: type === 'audio' ? 12 : 0, fade_out_frames: type === 'audio' ? 12 : 0, enabled: true, locked: false,
    };
    onPlan(next, true);
    onSelect([id]);
  };
  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setUploading(true);
    try {
      const asset = await uploadEditorAsset(file, project, user);
      const next = clone(plan);
      next.scene_graph.assets[asset.id] = asset;
      onPlan(next, true);
      addAssetToTimeline({...asset});
    } finally {
      setUploading(false);
    }
  };
  return <details className="asset-bin">
    <summary>Asset bin <span>{assets.length}</span></summary>
    <label className={`asset-upload${uploading ? ' busy' : ''}`}><input type="file" accept="video/*,audio/*,image/*,.gif,.json" onChange={upload} disabled={uploading} />{uploading ? 'Uploading…' : '+ Upload media'}</label>
    <div className="asset-list">{assets.slice(0, 24).map((asset) => <button key={asset.id} onClick={() => addAssetToTimeline(asset)}><span>{asset.type}</span><strong>{asset.name || asset.id}</strong></button>)}</div>
  </details>;
};

const EditorWorkspace: React.FC<{user: User; project: ProjectRecord; initialPlan: EditPlan}> = ({user, project, initialPlan}) => {
  const history = usePlanHistory(ensureV3(initialPlan));
  const plan = history.state.current;
  const clips = plan.clips.filter((clip) => clip.enabled !== false);
  const [clipId, setClipId] = useState(clips[0]?.id || Object.keys(plan.scene_graph.compositions)[0] || '');
  const [ratio, setRatio] = useState<Ratio>((plan.scene_graph.compositions[clipId]?.base_ratio as Ratio) || '9:16');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [snap, setSnap] = useState(true);
  const [guides, setGuides] = useState(false);
  const [status, setStatus] = useState('Ready');
  const [dirty, setDirty] = useState(false);
  const [revisions, setRevisions] = useState<Array<Record<string, unknown>>>([]);
  const [media, setMedia] = useState<MediaAnalysis | null>(null);
  const [draftProgress, setDraftProgress] = useState<number | null>(null);
  const [renderBackend, setRenderBackend] = useState<'auto' | 'ffmpeg' | 'remotion'>('auto');
  const [showAssets, setShowAssets] = useState(false);
  const playerRef = useRef<PlayerRef>(null);
  const frame = useCurrentPlayerFrame(playerRef);
  const composition = plan.scene_graph.compositions[clipId];
  const selectedItem = selectedIds.length === 1 ? plan.scene_graph.items[selectedIds[0]] || null : null;
  const sourceUrl = String(plan.scene_graph.assets['source:primary']?.remote_url || '');

  useEffect(() => {
    const saved = localStorage.getItem(`clipper:edit:${project.id}`);
    if (!saved) return;
    try {
      const parsed = ensureV3(JSON.parse(saved));
      if (Number(parsed.revision?.sequence || 0) >= Number(plan.revision?.sequence || 0)) history.reset(parsed);
    } catch { /* corrupted drafts are ignored */ }
    // Initial hydration only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      localStorage.setItem(`clipper:edit:${project.id}`, JSON.stringify(plan));
      if (dirty) void saveWorkingEdit(project, user, plan).catch(() => undefined);
    }, 1100);
    return () => window.clearTimeout(id);
  }, [plan, project, user, dirty]);

  useEffect(() => {
    if (!sourceUrl) return;
    let cancelled = false;
    setMedia(null);
    analyzeMedia(sourceUrl).then((analysis) => { if (!cancelled) setMedia(analysis); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [sourceUrl]);

  useEffect(() => {
    void listRevisions(project, user).then(setRevisions).catch(() => setRevisions([]));
  }, [project, user]);

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editingText = !!target && ['INPUT','TEXTAREA','SELECT'].includes(target.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !event.shiftKey) { event.preventDefault(); history.undo(); return; }
      if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || (event.key.toLowerCase() === 'z' && event.shiftKey))) { event.preventDefault(); history.redo(); return; }
      if (editingText) return;
      if (event.key === 'Delete' || event.key === 'Backspace') { if (selectedIds.length) { event.preventDefault(); applyPlan(deleteItems(plan, selectedIds), true, 'Delete layers'); setSelectedIds([]); } }
      if (event.key === ' ') { event.preventDefault(); if (playerRef.current?.isPlaying()) playerRef.current.pause(); else playerRef.current?.play(); }
      if (event.key === 'ArrowLeft') playerRef.current?.seekTo(Math.max(0, frame - (event.shiftKey ? composition.fps : 1)));
      if (event.key === 'ArrowRight') playerRef.current?.seekTo(Math.min(composition.duration_frames - 1, frame + (event.shiftKey ? composition.fps : 1)));
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  });

  const applyPlan = useCallback((next: EditPlan, commit = true, message = 'Edit') => {
    history.apply(next, commit);
    if (commit) {
      setDirty(true);
      setStatus(message);
    }
  }, [history]);

  const commitTransient = useCallback(() => {
    history.commitCurrent('Transform');
    setDirty(true);
  }, [history]);

  const seek = (nextFrame: number) => playerRef.current?.seekTo(Math.max(0, Math.min(composition.duration_frames - 1, nextFrame)));

  const changeRatio = (value: Ratio) => {
    setRatio(value);
    const next = clone(plan);
    const dims = RATIOS[value];
    next.scene_graph.compositions[clipId] = {...next.scene_graph.compositions[clipId], ...dims, base_ratio: value};
    applyPlan(next, true, `Canvas ${value}`);
  };

  const doSave = async () => {
    setStatus('Saving revision…');
    const revised = nextRevision(plan, 'Saved from visual editor');
    const saved = await saveRevision(project, user, revised, 'Saved from visual editor');
    history.reset(saved);
    localStorage.setItem(`clipper:edit:${project.id}`, JSON.stringify(saved));
    setDirty(false);
    setRevisions(await listRevisions(project, user));
    setStatus('Saved');
  };

  const restoreRevision = (id: string) => {
    const revision = revisions.find((value) => String(value.revisionId || '') === id);
    if (!revision?.plan) return;
    history.reset(nextRevision(ensureV3(revision.plan as EditPlan), `Restored ${id}`));
    setDirty(true);
    setStatus('Revision restored locally');
  };

  const addText = () => { const result = addTextItem(plan, clipId, frame); applyPlan(result.plan, true, 'Added text'); setSelectedIds([result.id]); };
  const addShape = () => { const result = addShapeItem(plan, clipId, frame, 'callout'); applyPlan(result.plan, true, 'Added shape'); setSelectedIds([result.id]); };
  const doSplit = () => {
    if (selectedIds.length !== 1) return;
    const next = splitItem(plan, selectedIds[0], frame);
    applyPlan(next, true, 'Split layer');
  };
  const doDuplicate = () => { const result = duplicateItems(plan, selectedIds); applyPlan(result.plan, true, 'Duplicated layers'); setSelectedIds(result.ids); };
  const doDelete = () => { applyPlan(deleteItems(plan, selectedIds), true, 'Deleted layers'); setSelectedIds([]); };

  const draftRender = async () => {
    if (draftProgress !== null) return;
    setDraftProgress(0);
    setStatus('Rendering browser draft…');
    try {
      const result = await renderMediaOnWeb({
        composition: {component: ClipperComposition, id: `clipper-${clipId}`, durationInFrames: composition.duration_frames, fps: composition.fps, width: composition.width, height: composition.height, calculateMetadata: null},
        inputProps: {plan, clipId, ratio, showGuides: false, selectedItemIds: []},
        scale: composition.height > 900 ? 0.5 : 1,
        videoBitrate: 'medium',
        hardwareAcceleration: 'prefer-hardware',
        pageResponsiveness: 'high',
        onProgress: ({progress}) => setDraftProgress(progress),
        logLevel: 'warn',
      });
      const blob = await result.getBlob();
      safeDownload(blob, `${clipId}-${ratio.replace(':', 'x')}-draft.mp4`);
      setStatus('Browser draft ready');
    } catch (error) {
      setStatus(`Browser draft unavailable: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setDraftProgress(null);
    }
  };

  const finalRender = async () => {
    if (dirty) await doSave();
    const jobId = await queueRerender(project, user, plan, {ratios: [ratio], backend: renderBackend});
    setStatus(`Final render queued · ${jobId.slice(0, 8)}`);
  };

  const applyTemplateChoice = (id: string) => applyPlan(applyTemplate(plan, id), true, `Applied ${id} template`);

  if (!composition) return <div className="error-state">This edit plan has no playable composition.</div>;
  const displayRatio = composition.width / composition.height;
  const playerWidth = displayRatio >= 1 ? 760 : Math.min(470, 760 * displayRatio);

  return <div className="editor-shell">
    <header className="editor-topbar">
      <div className="brand-lockup"><a href="/" aria-label="Back to Clipper">←</a><div><small>CLIPPER / VISUAL EDITOR</small><strong>{project.sourceName || 'Untitled project'}</strong></div></div>
      <div className="save-state"><i className={dirty ? 'dirty' : ''} />{status}</div>
      <div className="top-actions">
        <button onClick={history.undo} disabled={!history.state.past.length} title="Undo Ctrl/Cmd+Z">↶</button>
        <button onClick={history.redo} disabled={!history.state.future.length} title="Redo">↷</button>
        <button className="secondary" onClick={doSave}>{dirty ? 'Save' : 'Saved'}</button>
        <button className="primary" onClick={finalRender}>Final render</button>
      </div>
    </header>

    <div className="editor-toolbar">
      <select value={clipId} onChange={(event) => {setClipId(event.target.value); setSelectedIds([]); seek(0);}} aria-label="Clip"><option disabled value="">Choose clip</option>{clips.map((clip) => <option value={clip.id} key={clip.id}>{clip.title || clip.id}</option>)}</select>
      <select value={ratio} onChange={(event) => changeRatio(event.target.value as Ratio)} aria-label="Aspect ratio">{Object.keys(RATIOS).map((value) => <option value={value} key={value}>{value}</option>)}</select>
      <select value={plan.scene_graph.templates.active || 'clean-talking-head'} onChange={(event) => applyTemplateChoice(event.target.value)} aria-label="Motion template">{MOTION_TEMPLATES.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select>
      <span className="tool-separator" />
      <button onClick={addText}>+ Text</button><button onClick={addShape}>+ Shape</button>
      <button onClick={() => applyPlan(addTransition(plan, clipId, frame, 'fade'), true, 'Added transition')}>+ Transition</button>
      <button onClick={() => setShowAssets((value) => !value)} className={showAssets ? 'active' : ''}>Assets</button>
      <span className="tool-separator" />
      <button onClick={doSplit} disabled={selectedIds.length !== 1}>Split</button>
      <button onClick={doDuplicate} disabled={!selectedIds.length}>Duplicate</button>
      <button onClick={doDelete} disabled={!selectedIds.length}>Delete</button>
      <span className="tool-spacer" />
      <label className="toolbar-toggle"><input type="checkbox" checked={snap} onChange={(event) => setSnap(event.target.checked)} />Snap</label>
      <label className="toolbar-toggle"><input type="checkbox" checked={guides} onChange={(event) => setGuides(event.target.checked)} />Safe zones</label>
    </div>

    <main className="editor-main">
      {showAssets && <aside className="left-drawer"><AssetBin plan={plan} project={project} user={user} clipId={clipId} frame={frame} onPlan={applyPlan} onSelect={setSelectedIds} /><details open className="revision-panel"><summary>Revision history <span>{revisions.length}</span></summary>{revisions.slice(0, 20).map((revision) => <button key={String(revision.id)} onClick={() => restoreRevision(String(revision.revisionId))}><strong>#{String(revision.sequence || 0)}</strong><span>{String(revision.message || 'Revision')}</span></button>)}</details></aside>}
      <section className="preview-column">
        <div className="preview-stage" onMouseDown={() => selectedIds.length && setSelectedIds([])}>
          <div className="player-wrap" style={{width: playerWidth, aspectRatio: `${composition.width}/${composition.height}`}} onMouseDown={(event) => event.stopPropagation()}>
            <Player
              ref={playerRef}
              component={ClipperComposition}
              inputProps={{plan, clipId, ratio, showGuides: guides, selectedItemIds: selectedIds}}
              durationInFrames={composition.duration_frames}
              fps={composition.fps}
              compositionWidth={composition.width}
              compositionHeight={composition.height}
              controls
              clickToPlay
              spaceKeyToPlayOrPause={false}
              style={{width: '100%', height: '100%'}}
            />
            <CanvasHandles plan={plan} clipId={clipId} item={selectedItem} onPlan={applyPlan} onCommit={commitTransient} />
          </div>
        </div>
        <div className="preview-actions">
          <div><button onClick={() => seek(0)}>⏮</button><button onClick={() => playerRef.current?.isPlaying() ? playerRef.current.pause() : playerRef.current?.play()}>{playerRef.current?.isPlaying() ? 'Pause' : 'Play'}</button><button onClick={() => seek(Math.min(composition.duration_frames - 1, frame + composition.fps))}>+1s</button></div>
          <div className="draft-actions"><select value={renderBackend} onChange={(event) => setRenderBackend(event.target.value as typeof renderBackend)}><option value="auto">Auto renderer</option><option value="ffmpeg">FFmpeg / NVENC</option><option value="remotion">Remotion</option></select><button onClick={draftRender} disabled={draftProgress !== null}>{draftProgress === null ? 'Browser draft' : `Draft ${Math.round(draftProgress * 100)}%`}</button></div>
        </div>
      </section>
      <Inspector plan={plan} clipId={clipId} selectedIds={selectedIds} frame={frame} onPlan={(next, commit) => applyPlan(next, commit ?? true, 'Inspector edit')} />
    </main>

    <Timeline plan={plan} clipId={clipId} frame={frame} selectedIds={selectedIds} media={media} snap={snap} onSeek={seek} onPlan={(next, commit) => applyPlan(next, commit ?? true, 'Timeline edit')} onSelect={setSelectedIds} />
  </div>;
};

export const App: React.FC = () => {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [loaded, setLoaded] = useState<{project: ProjectRecord; plan: EditPlan} | null>(null);
  const [error, setError] = useState('');
  const projectId = useMemo(projectIdFromUrl, []);

  useEffect(() => {
    let unsub: (() => void) | undefined;
    void observeUser((nextUser) => setUser(nextUser)).then((value) => {unsub = value;});
    return () => unsub?.();
  }, []);

  useEffect(() => {
    if (!user || !projectId) return;
    setError('');
    setLoaded(null);
    loadProject(projectId, user).then(setLoaded).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [user, projectId]);

  if (user === undefined) return <div className="gate"><div className="gate-card"><span className="loader" /><h1>Opening Clipper editor</h1><p>Loading your project workspace…</p></div></div>;
  if (!user) return <div className="gate"><div className="gate-card"><small>CLIPPER / VISUAL EDITOR</small><h1>Sign in to edit</h1><p>Your project plans and source media stay scoped to your Clipper account.</p><button className="primary" onClick={toggleAuth}>Sign in with Google</button><a href="/">Back to library</a></div></div>;
  if (!projectId) return <div className="gate"><div className="gate-card"><h1>No project selected</h1><p>Open a finished project from the Clipper library, then choose Edit.</p><a className="button-link" href="/">Open project library</a></div></div>;
  if (error) return <div className="gate"><div className="gate-card error"><h1>Couldn’t open this edit</h1><p>{error}</p><button onClick={() => location.reload()}>Retry</button><a href="/">Back to library</a></div></div>;
  if (!loaded) return <div className="gate"><div className="gate-card"><span className="loader" /><h1>Loading project</h1><p>Hydrating the edit plan and media assets…</p></div></div>;
  return <EditorWorkspace user={user} project={loaded.project} initialPlan={loaded.plan} />;
};
