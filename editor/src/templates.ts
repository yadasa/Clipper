import type {EditPlan, SceneItem} from './types';

export type MotionTemplate = {
  id: string;
  name: string;
  description: string;
  background: string;
  accent: string;
  text: string;
  fontFamily: string;
  caption: {fontSizeRatio: number; bottomRatio: number; activeScale: number; uppercase: boolean};
  hook: {fontSizeRatio: number; maxWidthRatio: number; entrance: 'spring' | 'fade' | 'pop'};
  transition: 'cut' | 'fade' | 'push' | 'zoom' | 'blur';
  punchScale: number;
};

export const MOTION_TEMPLATES: MotionTemplate[] = [
  {
    id: 'clean-talking-head', name: 'Clean Talking Head', description: 'Warm minimal captions and restrained motion.',
    background: '#11100e', accent: '#d6a77a', text: '#ffffff', fontFamily: 'Inter, Arial, sans-serif',
    caption: {fontSizeRatio: 0.052, bottomRatio: 0.16, activeScale: 1.05, uppercase: false},
    hook: {fontSizeRatio: 0.062, maxWidthRatio: 0.84, entrance: 'spring'}, transition: 'cut', punchScale: 1.07,
  },
  {
    id: 'hype-tech', name: 'Hype Tech', description: 'Faster motion, bigger hooks and punchier captions.',
    background: '#0c0d0f', accent: '#d4b08a', text: '#ffffff', fontFamily: 'Arial Black, Inter, sans-serif',
    caption: {fontSizeRatio: 0.058, bottomRatio: 0.15, activeScale: 1.1, uppercase: true},
    hook: {fontSizeRatio: 0.074, maxWidthRatio: 0.88, entrance: 'pop'}, transition: 'zoom', punchScale: 1.11,
  },
  {
    id: 'documentary', name: 'Documentary', description: 'Lower-key motion, clear type and soft fades.',
    background: '#171512', accent: '#c7a985', text: '#f4efe7', fontFamily: 'Georgia, serif',
    caption: {fontSizeRatio: 0.046, bottomRatio: 0.13, activeScale: 1.02, uppercase: false},
    hook: {fontSizeRatio: 0.055, maxWidthRatio: 0.78, entrance: 'fade'}, transition: 'fade', punchScale: 1.04,
  },
  {
    id: 'luxury', name: 'Luxury', description: 'Editorial typography, generous spacing and subtle motion.',
    background: '#1a1510', accent: '#c9aa7d', text: '#fff9ef', fontFamily: 'Georgia, Times New Roman, serif',
    caption: {fontSizeRatio: 0.044, bottomRatio: 0.14, activeScale: 1.025, uppercase: false},
    hook: {fontSizeRatio: 0.058, maxWidthRatio: 0.76, entrance: 'fade'}, transition: 'blur', punchScale: 1.045,
  },
  {
    id: 'meme', name: 'Meme / Reaction', description: 'Large readable type and energetic pop motion.',
    background: '#0f0e0d', accent: '#e0b07d', text: '#ffffff', fontFamily: 'Arial Black, Impact, sans-serif',
    caption: {fontSizeRatio: 0.064, bottomRatio: 0.12, activeScale: 1.12, uppercase: true},
    hook: {fontSizeRatio: 0.08, maxWidthRatio: 0.9, entrance: 'pop'}, transition: 'push', punchScale: 1.13,
  },
];

export const getTemplate = (id?: string | null) => MOTION_TEMPLATES.find((template) => template.id === id) || MOTION_TEMPLATES[0];

const hookAnimations = (item: SceneItem, entrance: MotionTemplate['hook']['entrance']) => {
  const last = Math.max(1, Math.min(item.duration_frames - 1, 9));
  if (entrance === 'fade') return {opacity: [{frame: 0, value: 0, easing: 'ease-out' as const}, {frame: last, value: 1, easing: 'ease-out' as const}]};
  if (entrance === 'pop') return {
    opacity: [{frame: 0, value: 0, easing: 'ease-out' as const}, {frame: 3, value: 1, easing: 'ease-out' as const}],
    scale: [{frame: 0, value: 0.82, easing: 'spring' as const}, {frame: last, value: 1, easing: 'spring' as const}],
  };
  return {
    opacity: [{frame: 0, value: 0, easing: 'ease-out' as const}, {frame: 4, value: 1, easing: 'ease-out' as const}],
    scale: [{frame: 0, value: 0.94, easing: 'spring' as const}, {frame: last, value: 1, easing: 'spring' as const}],
  };
};

export const applyTemplate = (planInput: EditPlan, templateId: string): EditPlan => {
  const plan = structuredClone(planInput);
  const template = getTemplate(templateId);
  plan.scene_graph.templates.active = template.id;
  plan.scene_graph.templates.overrides = {};
  plan.brand = {...plan.brand, accent: template.accent, primary_text: template.text, font: template.fontFamily};
  for (const composition of Object.values(plan.scene_graph.compositions)) composition.background = template.background;
  for (const item of Object.values(plan.scene_graph.items)) {
    if (item.type === 'captions') {
      item.style = {...(item.style || {}), color: template.text, accent: template.accent, fontFamily: template.fontFamily, fontSizeRatio: template.caption.fontSizeRatio, bottomRatio: template.caption.bottomRatio, activeScale: template.caption.activeScale, uppercase: template.caption.uppercase};
    }
    if (item.type === 'text' && item.style?.role === 'hook') {
      item.style = {...item.style, color: template.text, accent: template.accent, fontFamily: template.fontFamily, fontSizeRatio: template.hook.fontSizeRatio, maxWidthRatio: template.hook.maxWidthRatio};
      item.animations = hookAnimations(item, template.hook.entrance);
    }
  }
  plan.defaults = {...plan.defaults, template: template.id, transition_preset: template.transition};
  return plan;
};
