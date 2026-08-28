import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index];
  const value = process.argv[index + 1];
  if (key?.startsWith('--') && value !== undefined) args.set(key.slice(2), value);
}

const required = (name) => {
  const value = args.get(name);
  if (!value) throw new Error(`Missing --${name}`);
  return value;
};

const planPath = path.resolve(required('plan'));
const clipId = required('clip');
const ratio = args.get('ratio') || '9:16';
const requestedOutput = path.resolve(required('output'));
const editorRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const publicRoot = path.join(editorRoot, 'public');
const sessionId = crypto.randomBytes(8).toString('hex');
const runtimeRoot = path.join(publicRoot, '__clipper_runtime', sessionId);

const emit = (payload) => process.stdout.write(`CLIPPER_PROGRESS ${JSON.stringify(payload)}\n`);
const safeName = (value) => value.replace(/[^a-zA-Z0-9._-]+/g, '-').slice(-120) || 'asset.bin';

const materialize = async (sourceValue, assetId) => {
  if (!sourceValue || /^(https?:|data:|blob:)/.test(sourceValue)) return null;
  const source = path.isAbsolute(sourceValue) ? sourceValue : path.resolve(path.dirname(planPath), sourceValue);
  let stat;
  try { stat = await fs.stat(source); } catch { return null; }
  if (!stat.isFile()) return null;
  const folder = crypto.createHash('sha1').update(String(assetId)).digest('hex').slice(0, 10);
  const destinationDir = path.join(runtimeRoot, folder);
  await fs.mkdir(destinationDir, {recursive: true});
  const destination = path.join(destinationDir, safeName(path.basename(source)));
  try { await fs.link(source, destination); }
  catch { await fs.copyFile(source, destination); }
  return `/__clipper_runtime/${sessionId}/${folder}/${path.basename(destination)}`;
};

const raw = JSON.parse(await fs.readFile(planPath, 'utf8'));
if (!raw?.scene_graph?.compositions?.[clipId]) throw new Error(`Clip ${clipId} is missing from scene graph`);
await fs.mkdir(runtimeRoot, {recursive: true});

for (const [assetId, asset] of Object.entries(raw.scene_graph.assets || {})) {
  if (!asset || typeof asset !== 'object') continue;
  const value = asset.path || null;
  const publicPath = await materialize(value, assetId);
  if (publicPath) asset.public_path = publicPath;
}

const inputProps = {plan: raw, clipId, ratio, showGuides: false, selectedItemIds: []};
const actualOutput = /\.(mp4|mov|mkv|webm)$/i.test(requestedOutput) ? requestedOutput : `${requestedOutput}.mp4`;
await fs.mkdir(path.dirname(actualOutput), {recursive: true});

let serveUrl;
try {
  emit({stage: 'preparing', progress: 0.02, detail: 'Bundling Remotion composition'});
  serveUrl = await bundle({
    entryPoint: path.join(editorRoot, 'src', 'remotion-root.tsx'),
    onProgress: (progress) => emit({stage: 'preparing', progress: Math.min(0.18, 0.02 + progress / 100 * 0.16), detail: 'Bundling'}),
  });
  const composition = await selectComposition({serveUrl, id: 'ClipperPlan', inputProps});
  emit({stage: 'rendering', progress: 0.2, frameCount: composition.durationInFrames, detail: 'Rendering frames'});
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    audioCodec: 'aac',
    outputLocation: actualOutput,
    inputProps,
    concurrency: null,
    overwrite: true,
    onProgress: ({progress, renderedFrames, encodedFrames, stitchStage}) => {
      emit({
        stage: stitchStage === 'muxing' ? 'muxing' : 'encoding',
        progress: 0.2 + Math.max(0, Math.min(1, progress)) * 0.78,
        renderedFrames,
        encodedFrames,
        frameCount: composition.durationInFrames,
        detail: stitchStage === 'muxing' ? 'Muxing audio and video' : 'Rendering and encoding',
      });
    },
  });
  if (actualOutput !== requestedOutput) {
    await fs.rm(requestedOutput, {force: true});
    await fs.rename(actualOutput, requestedOutput);
  }
  emit({stage: 'done', progress: 1, frameCount: composition.durationInFrames, detail: 'Render complete'});
} finally {
  await fs.rm(runtimeRoot, {recursive: true, force: true}).catch(() => undefined);
  if (actualOutput !== requestedOutput) await fs.rm(actualOutput, {force: true}).catch(() => undefined);
}
