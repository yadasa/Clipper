import React, {useMemo} from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio, Video} from '@remotion/media';
import {Lottie} from '@remotion/lottie';
import type {EditPlan, SceneAsset, SceneItem, TransitionSpec} from './types';
import {getTemplate} from './templates';
import {itemsForClip, tracksForClip, transitionsForClip, valueAtFrame} from './plan';

export type ClipperCompositionProps = {
  plan: EditPlan;
  clipId: string;
  ratio?: string;
  showGuides?: boolean;
  selectedItemIds?: string[];
};

const sourceForAsset = (asset?: SceneAsset) => {
  if (!asset) return '';
  if (asset.remote_url) return asset.remote_url;
  if (asset.public_path) return staticFile(String(asset.public_path).replace(/^\/+/, ''));
  if (asset.path && /^(https?:|blob:|data:)/.test(asset.path)) return asset.path;
  return '';
};

const animationNumber = (item: SceneItem, property: string, localFrame: number, fallback: number) => valueAtFrame(item.animations?.[property], localFrame, fallback);

const visualStyle = (item: SceneItem, localFrame: number): React.CSSProperties => {
  const base = item.transform;
  const x = animationNumber(item, 'x', localFrame, base.x);
  const y = animationNumber(item, 'y', localFrame, base.y);
  const scale = animationNumber(item, 'scale', localFrame, 1);
  const scaleX = animationNumber(item, 'scale_x', localFrame, base.scale_x) * scale;
  const scaleY = animationNumber(item, 'scale_y', localFrame, base.scale_y) * scale;
  const rotation = animationNumber(item, 'rotation', localFrame, base.rotation);
  const opacity = animationNumber(item, 'opacity', localFrame, base.opacity);
  return {
    position: 'absolute',
    inset: 0,
    opacity,
    transformOrigin: `${base.anchor_x * 100}% ${base.anchor_y * 100}%`,
    transform: `translate3d(${x}px, ${y}px, 0) scale(${scaleX}, ${scaleY}) rotate(${rotation}deg)`,
    borderRadius: `${base.border_radius || 0}px`,
    overflow: base.border_radius ? 'hidden' : undefined,
  };
};

const mediaObjectStyle = (item: SceneItem): React.CSSProperties => {
  const crop = item.transform.crop || {left: 0, right: 0, top: 0, bottom: 0};
  const x = (Number(crop.left || 0) - Number(crop.right || 0)) * 50;
  const y = (Number(crop.top || 0) - Number(crop.bottom || 0)) * 50;
  const zoomX = 1 / Math.max(0.05, 1 - Number(crop.left || 0) - Number(crop.right || 0));
  const zoomY = 1 / Math.max(0.05, 1 - Number(crop.top || 0) - Number(crop.bottom || 0));
  return {width: '100%', height: '100%', objectFit: 'cover', transform: `translate(${x}%, ${y}%) scale(${zoomX}, ${zoomY})`};
};

const fadeVolume = (item: SceneItem, localFrame: number) => {
  let volume = Number(item.volume ?? 1);
  const fadeIn = Math.max(0, Number(item.fade_in_frames || 0));
  const fadeOut = Math.max(0, Number(item.fade_out_frames || 0));
  if (fadeIn && localFrame < fadeIn) volume *= localFrame / fadeIn;
  if (fadeOut && localFrame > item.duration_frames - fadeOut) volume *= Math.max(0, (item.duration_frames - localFrame) / fadeOut);
  return Math.max(0, Math.min(2, volume));
};

const CaptionLayer: React.FC<{plan: EditPlan; item: SceneItem; localFrame: number}> = ({plan, item, localFrame}) => {
  const {fps, height} = useVideoConfig();
  const document = item.caption_document_id ? plan.scene_graph.caption_documents[item.caption_document_id] : undefined;
  if (!document) return null;
  const timeMs = (localFrame / fps) * 1000;
  const page = document.pages?.find((value) => timeMs >= value.start_ms && timeMs <= value.end_ms) || document.pages?.[0];
  if (!page) return null;
  const tokenMap = new Map(document.tokens.map((token) => [token.id, token]));
  const tokens = page.token_ids.map((id) => tokenMap.get(id)).filter(Boolean);
  const active = tokens.find((token) => token && timeMs >= token.start_ms && timeMs <= token.end_ms)?.id;
  const template = getTemplate(plan.scene_graph.templates?.active);
  const style = item.style || {};
  const fontSize = Math.round(height * Number(style.fontSizeRatio || template.caption.fontSizeRatio));
  const bottom = Math.round(height * Number(style.bottomRatio || template.caption.bottomRatio));
  const uppercase = Boolean(style.uppercase ?? template.caption.uppercase);
  return (
    <div style={{...visualStyle(item, localFrame), pointerEvents: 'none'}}>
      <div style={{position: 'absolute', left: '7%', right: '7%', bottom, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '0.18em', fontFamily: String(style.fontFamily || template.fontFamily), fontSize, lineHeight: 1.05, fontWeight: 850, textAlign: 'center', textShadow: '0 3px 12px rgba(0,0,0,.78)'}}>
        {tokens.map((token) => {
          if (!token) return null;
          const isActive = token.id === active;
          return <span key={token.id} style={{color: isActive ? String(style.accent || template.accent) : String(style.color || template.text), transform: `scale(${isActive ? Number(style.activeScale || template.caption.activeScale) : 1})`, transition: 'transform 80ms linear'}}>{uppercase ? token.text.toUpperCase() : token.text}</span>;
        })}
      </div>
    </div>
  );
};

const ShapeLayer: React.FC<{item: SceneItem; localFrame: number}> = ({item, localFrame}) => {
  const shape = item.shape || {};
  const kind = shape.kind || 'rect';
  const fill = shape.fill || '#d6a77a';
  const stroke = shape.stroke || 'transparent';
  const strokeWidth = shape.strokeWidth || 0;
  return (
    <div style={{...visualStyle(item, localFrame), display: 'grid', placeItems: 'center', pointerEvents: 'none'}}>
      <svg viewBox="0 0 100 100" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
        {kind === 'circle' && <circle cx="50" cy="50" r="42" fill={fill} stroke={stroke} strokeWidth={strokeWidth} />}
        {kind === 'triangle' && <polygon points="50,8 94,92 6,92" fill={fill} stroke={stroke} strokeWidth={strokeWidth} />}
        {kind === 'arrow' && <path d="M8 42h54V24l30 26-30 26V58H8z" fill={fill} stroke={stroke} strokeWidth={strokeWidth} />}
        {kind === 'callout' && <path d="M8 12h84v58H55L38 90l3-20H8z" fill={fill} stroke={stroke} strokeWidth={strokeWidth} />}
        {kind === 'star' && <path d="M50 5l12 29 31 3-24 20 8 31-27-17-27 17 8-31L7 37l31-3z" fill={fill} stroke={stroke} strokeWidth={strokeWidth} />}
        {!['circle','triangle','arrow','callout','star'].includes(kind) && <rect x="8" y="18" width="84" height="64" rx="8" fill={fill} stroke={stroke} strokeWidth={strokeWidth} />}
      </svg>
    </div>
  );
};

const TextLayer: React.FC<{plan: EditPlan; item: SceneItem; localFrame: number}> = ({plan, item, localFrame}) => {
  const {height} = useVideoConfig();
  const template = getTemplate(plan.scene_graph.templates?.active);
  const style = item.style || {};
  const fontSize = Number(style.fontSize || Math.round(height * Number(style.fontSizeRatio || template.hook.fontSizeRatio)));
  const maxWidth = `${Number(style.maxWidthRatio || template.hook.maxWidthRatio) * 100}%`;
  return (
    <div style={{...visualStyle(item, localFrame), display: 'grid', placeItems: 'start center', paddingTop: '8%', pointerEvents: 'none'}}>
      <div style={{maxWidth, padding: style.background && style.background !== 'transparent' ? '0.22em 0.38em' : 0, borderRadius: Number(style.borderRadius || 16), background: String(style.background || 'transparent'), color: String(style.color || template.text), fontFamily: String(style.fontFamily || template.fontFamily), fontSize, fontWeight: Number(style.fontWeight || 850), lineHeight: Number(style.lineHeight || 1.02), letterSpacing: Number(style.letterSpacing || -0.5), textAlign: (style.textAlign as React.CSSProperties['textAlign']) || 'center', textShadow: '0 3px 18px rgba(0,0,0,.55)'}}>
        {String(item.text || '')}
      </div>
    </div>
  );
};

const Layer: React.FC<{plan: EditPlan; item: SceneItem; selected: boolean}> = ({plan, item, selected}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const localFrame = frame - item.from_frame;
  if (!item.enabled || localFrame < 0 || localFrame >= item.duration_frames) return null;
  const asset = item.asset_id ? plan.scene_graph.assets[item.asset_id] : undefined;
  const src = sourceForAsset(asset);
  let content: React.ReactNode = null;
  if (item.type === 'video' && src) {
    content = <div style={visualStyle(item, localFrame)}><Video src={src} trimBefore={Math.max(0, Math.round(Number(item.trim_before_seconds || 0) * fps))} volume={fadeVolume(item, localFrame)} style={mediaObjectStyle(item)} /></div>;
  } else if (item.type === 'image' && src) {
    content = <div style={visualStyle(item, localFrame)}><Img src={src} style={mediaObjectStyle(item)} /></div>;
  } else if ((item.type === 'audio' || item.type === 'sfx') && src) {
    content = <Audio src={src} trimBefore={Math.max(0, Math.round(Number(item.trim_before_seconds || 0) * fps))} volume={fadeVolume(item, localFrame)} />;
  } else if (item.type === 'text') {
    content = <TextLayer plan={plan} item={item} localFrame={localFrame} />;
  } else if (item.type === 'captions') {
    content = <CaptionLayer plan={plan} item={item} localFrame={localFrame} />;
  } else if (item.type === 'shape') {
    content = <ShapeLayer item={item} localFrame={localFrame} />;
  } else if (item.type === 'solid') {
    content = <div style={{...visualStyle(item, localFrame), background: String(item.style?.color || '#d6a77a')}} />;
  } else if (item.type === 'lottie' && item.animation_data && typeof item.animation_data === 'object') {
    content = <div style={visualStyle(item, localFrame)}><Lottie animationData={item.animation_data as never} style={{width: '100%', height: '100%'}} /></div>;
  } else if (item.type === 'rive' || item.type === 'three') {
    content = <div style={{...visualStyle(item, localFrame), display: 'grid', placeItems: 'center'}}><div style={{padding: 18, borderRadius: 18, background: 'rgba(24,20,16,.72)', color: '#f7efe7', fontFamily: 'Inter, sans-serif', fontWeight: 700}}>{item.type === 'rive' ? 'Rive layer' : '3D layer'}</div></div>;
  }
  if (!content) return null;
  return <>{content}{selected && <div style={{position: 'absolute', inset: 6, border: '3px solid rgba(214,167,122,.95)', borderRadius: 8, pointerEvents: 'none'}} />}</>;
};

const transitionStyle = (transition: TransitionSpec, frame: number): React.CSSProperties => {
  const half = Math.max(1, transition.duration_frames / 2);
  const distance = Math.abs(frame - transition.frame);
  const progress = Math.max(0, Math.min(1, 1 - distance / half));
  const strength = Number(transition.strength || 1);
  if (transition.type === 'fade') return {opacity: 1 - progress * 0.45 * strength};
  if (transition.type === 'zoom') return {transform: `scale(${1 + progress * 0.055 * strength})`};
  if (transition.type === 'blur') return {filter: `blur(${progress * 10 * strength}px)`};
  if (transition.type === 'push') {
    const axis = transition.direction === 'up' || transition.direction === 'down' ? 'Y' : 'X';
    const sign = transition.direction === 'left' || transition.direction === 'up' ? -1 : 1;
    return {transform: `translate${axis}(${sign * progress * 5 * strength}%)`};
  }
  return {};
};

const SafeZones: React.FC = () => <AbsoluteFill style={{pointerEvents: 'none'}}>
  <div style={{position: 'absolute', inset: '5% 5% 12%', border: '2px dashed rgba(255,255,255,.35)', borderRadius: 20}} />
  <div style={{position: 'absolute', left: '8%', right: '8%', bottom: '16%', height: 1, background: 'rgba(214,167,122,.45)'}} />
</AbsoluteFill>;

export const ClipperComposition: React.FC<ClipperCompositionProps> = ({plan, clipId, showGuides = false, selectedItemIds = []}) => {
  const frame = useCurrentFrame();
  const config = plan.scene_graph.compositions[clipId];
  const tracks = useMemo(() => tracksForClip(plan, clipId), [plan, clipId]);
  const trackMap = useMemo(() => new Map(tracks.map((track) => [track.id, track])), [tracks]);
  const items = useMemo(() => itemsForClip(plan, clipId).sort((a, b) => (trackMap.get(a.track_id)?.z_index || 0) - (trackMap.get(b.track_id)?.z_index || 0)), [plan, clipId, trackMap]);
  const transition = transitionsForClip(plan, clipId).find((value) => Math.abs(frame - value.frame) <= value.duration_frames / 2);
  const wrapperStyle = transition ? transitionStyle(transition, frame) : {};
  return (
    <AbsoluteFill style={{background: config?.background || '#11100e', overflow: 'hidden'}}>
      <AbsoluteFill style={wrapperStyle}>
        {items.map((item) => {
          const track = trackMap.get(item.track_id);
          if (track?.hidden || (track?.muted && (item.type === 'audio' || item.type === 'sfx'))) return null;
          return <Layer key={item.id} plan={plan} item={item} selected={selectedItemIds.includes(item.id)} />;
        })}
      </AbsoluteFill>
      {showGuides && <SafeZones />}
    </AbsoluteFill>
  );
};
