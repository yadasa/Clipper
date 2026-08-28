import {describe, expect, it} from 'vitest';
import type {EditPlan} from './types';
import {addKeyframe, addTextItem, buildPages, ensureV3, splitItem, valueAtFrame} from './plan';
import {applyTemplate} from './templates';

const legacy = (): EditPlan => ({
  version: 2,
  schema: 'legacy',
  project_id: 'project',
  brand: {},
  defaults: {},
  clips: [{id: 'clip_001', enabled: true, start: 10, end: 20, title: 'A clip', transcript: 'one two three four five six seven eight', ratios: ['9:16']}],
  scene_graph: undefined as never,
});

describe('Edit Plan V3', () => {
  it('migrates V2 data into editable tracks without losing legacy clip timing', () => {
    const plan = ensureV3(legacy());
    expect(plan.version).toBe(3);
    expect(plan.clips[0].start).toBe(10);
    expect(plan.scene_graph.compositions.clip_001.duration_frames).toBe(300);
    expect(Object.values(plan.scene_graph.tracks).map((track) => track.kind)).toContain('captions');
    expect(Object.values(plan.scene_graph.items).some((item) => item.type === 'video')).toBe(true);
  });

  it('splits media without changing combined duration', () => {
    const plan = ensureV3(legacy());
    const source = Object.values(plan.scene_graph.items).find((item) => item.type === 'video')!;
    const next = splitItem(plan, source.id, 120);
    const pieces = Object.values(next.scene_graph.items).filter((item) => item.type === 'video');
    expect(pieces).toHaveLength(2);
    expect(pieces.reduce((sum, item) => sum + item.duration_frames, 0)).toBe(source.duration_frames);
  });

  it('interpolates generic keyframes deterministically', () => {
    const plan = ensureV3(legacy());
    const source = Object.values(plan.scene_graph.items).find((item) => item.type === 'video')!;
    const first = addKeyframe(plan, source.id, 'opacity', 0, 0, 'linear');
    const second = addKeyframe(first, source.id, 'opacity', 10, 1, 'linear');
    expect(valueAtFrame(second.scene_graph.items[source.id].animations.opacity, 5, 1)).toBeCloseTo(0.5, 4);
  });

  it('pages long captions and keeps token identity', () => {
    const plan = ensureV3(legacy());
    const document = Object.values(plan.scene_graph.caption_documents)[0];
    const pages = buildPages(document, 12).pages;
    expect(pages.length).toBeGreaterThan(1);
    expect(new Set(pages.flatMap((page) => page.token_ids)).size).toBe(document.tokens.length);
  });

  it('applies a motion template without deleting user items', () => {
    const plan = ensureV3(legacy());
    const withText = addTextItem(plan, 'clip_001', 20, 'Custom').plan;
    const before = Object.keys(withText.scene_graph.items).length;
    const styled = applyTemplate(withText, 'hype-tech');
    expect(styled.scene_graph.templates.active).toBe('hype-tech');
    expect(Object.keys(styled.scene_graph.items)).toHaveLength(before);
    expect(styled.brand.accent).toBeTruthy();
  });
});
