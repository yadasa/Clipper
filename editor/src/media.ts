import {parseMedia} from '@remotion/media-parser';
import type {MediaAnalysis} from './types';

const boundedSamples = (values: Float32Array, buckets = 160): number[] => {
  if (!values.length) return [];
  const size = Math.max(1, Math.floor(values.length / buckets));
  const result: number[] = [];
  let peak = 0.0001;
  for (let bucket = 0; bucket < buckets; bucket++) {
    const start = bucket * size;
    const end = Math.min(values.length, start + size);
    let sum = 0;
    let count = 0;
    for (let index = start; index < end; index++) {
      sum += Math.abs(values[index]);
      count++;
    }
    const value = count ? sum / count : 0;
    peak = Math.max(peak, value);
    result.push(value);
  }
  return result.map((value) => Math.min(1, value / peak));
};

const analyzeAudio = async (url: string): Promise<number[]> => {
  const response = await fetch(url);
  if (!response.ok) return [];
  const data = await response.arrayBuffer();
  const AudioContextClass = window.AudioContext || (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext;
  if (!AudioContextClass) return [];
  const context = new AudioContextClass();
  try {
    const buffer = await context.decodeAudioData(data.slice(0));
    const channels = Math.max(1, buffer.numberOfChannels);
    const mono = new Float32Array(buffer.length);
    for (let channel = 0; channel < channels; channel++) {
      const source = buffer.getChannelData(channel);
      for (let index = 0; index < source.length; index++) mono[index] += source[index] / channels;
    }
    return boundedSamples(mono);
  } catch {
    return [];
  } finally {
    void context.close();
  }
};

const seekVideo = (video: HTMLVideoElement, time: number) => new Promise<void>((resolve, reject) => {
  const timeout = window.setTimeout(() => reject(new Error('Video seek timed out')), 5000);
  const done = () => { window.clearTimeout(timeout); video.removeEventListener('seeked', done); resolve(); };
  video.addEventListener('seeked', done, {once: true});
  video.currentTime = Math.max(0, Math.min(video.duration || time, time));
});

const filmstrip = async (url: string, duration: number, amount = 9): Promise<string[]> => {
  if (!duration || amount <= 0) return [];
  const video = document.createElement('video');
  video.crossOrigin = 'anonymous';
  video.muted = true;
  video.preload = 'auto';
  video.playsInline = true;
  video.src = url;
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error('Video metadata timed out')), 8000);
    video.onloadedmetadata = () => { window.clearTimeout(timeout); resolve(); };
    video.onerror = () => { window.clearTimeout(timeout); reject(new Error('Could not load source preview')); };
  });
  const canvas = document.createElement('canvas');
  canvas.width = 160;
  canvas.height = 90;
  const context = canvas.getContext('2d');
  if (!context) return [];
  const frames: string[] = [];
  for (let index = 0; index < amount; index++) {
    const timestamp = Math.min(Math.max(0, duration - 0.05), duration * ((index + 0.5) / amount));
    try {
      await seekVideo(video, timestamp);
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      frames.push(canvas.toDataURL('image/jpeg', 0.55));
    } catch {
      break;
    }
  }
  video.removeAttribute('src');
  video.load();
  return frames;
};

export const analyzeMedia = async (url: string): Promise<MediaAnalysis> => {
  const result = await parseMedia({
    src: url,
    fields: {durationInSeconds: true, dimensions: true, tracks: true},
    acknowledgeRemotionLicense: true,
    logLevel: 'error',
  });
  const duration = Number(result.durationInSeconds || 0);
  const dimensions = result.dimensions || {width: 0, height: 0};
  const tracks = result.tracks || [];
  const videoTrack = tracks.find((track) => track.type === 'video') as {fps?: number} | undefined;
  const [waveform, frames] = await Promise.all([
    analyzeAudio(url).catch(() => []),
    filmstrip(url, duration).catch(() => []),
  ]);
  return {duration, width: Number(dimensions.width || 0), height: Number(dimensions.height || 0), fps: Number(videoTrack?.fps || 0) || null, waveform, filmstrip: frames};
};
