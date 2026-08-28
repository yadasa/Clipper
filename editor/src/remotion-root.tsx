import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {ClipperComposition, type ClipperCompositionProps} from './composition';
import {RATIOS} from './plan';

const placeholderPlan: ClipperCompositionProps['plan'] = {
  version: 3,
  schema: 'clipper.edit-plan.v3',
  project_id: 'placeholder',
  brand: {},
  defaults: {},
  clips: [{id: 'placeholder', enabled: true, start: 0, end: 1, title: 'Placeholder', ratios: ['9:16']}],
  scene_graph: {
    schema: 'clipper.scene-graph.v1', assets: {}, tracks: {}, items: {}, caption_documents: {}, transitions: {},
    compositions: {placeholder: {id: 'placeholder', fps: 30, width: 1080, height: 1920, duration_frames: 30, base_ratio: '9:16', ratio_variants: ['9:16']}},
    templates: {active: 'clean-talking-head', overrides: {}}, render: {preferred_backend: 'auto'},
  },
};

const Root: React.FC = () => <Composition
  id="ClipperPlan"
  component={ClipperComposition}
  defaultProps={{plan: placeholderPlan, clipId: 'placeholder', ratio: '9:16', showGuides: false, selectedItemIds: []}}
  calculateMetadata={({props}) => {
    const clipId = props.clipId || Object.keys(props.plan.scene_graph.compositions)[0] || 'placeholder';
    const config = props.plan.scene_graph.compositions[clipId] || placeholderPlan.scene_graph.compositions.placeholder;
    const ratio = String(props.ratio || config.base_ratio || '9:16') as keyof typeof RATIOS;
    const dimensions = RATIOS[ratio] || {width: config.width, height: config.height};
    return {
      width: dimensions.width,
      height: dimensions.height,
      fps: config.fps || 30,
      durationInFrames: Math.max(1, config.duration_frames || 1),
      props: {...props, clipId, ratio},
      defaultCodec: 'h264',
    };
  }}
/>;

registerRoot(Root);
