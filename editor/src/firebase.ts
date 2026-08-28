import {initializeApp} from 'firebase/app';
import {GoogleAuthProvider, getAuth, getRedirectResult, onAuthStateChanged, signInWithPopup, signInWithRedirect, signOut, type User} from 'firebase/auth';
import {collection, doc, getDoc, getDocs, getFirestore, query, serverTimestamp, setDoc, where} from 'firebase/firestore';
import {getDownloadURL, getStorage, ref, uploadBytesResumable} from 'firebase/storage';
import type {EditPlan, ProjectRecord, Ratio, SceneAsset} from './types';
import {ensureV3, normalizedPlanForSave} from './plan';

let initialized: Promise<ReturnType<typeof initializeApp>> | null = null;

const firebaseApp = async () => {
  if (!initialized) {
    initialized = (async () => {
      const response = await fetch('/__/firebase/init.json');
      if (!response.ok) throw new Error('Firebase Hosting configuration is unavailable. Open the editor through Firebase Hosting or the emulator.');
      return initializeApp(await response.json());
    })();
  }
  return initialized;
};

export const services = async () => {
  const app = await firebaseApp();
  return {app, auth: getAuth(app), db: getFirestore(app), storage: getStorage(app)};
};

export const observeUser = async (callback: (user: User | null) => void) => {
  const {auth} = await services();
  await getRedirectResult(auth).catch(() => null);
  return onAuthStateChanged(auth, callback);
};

export const toggleAuth = async () => {
  const {auth} = await services();
  if (auth.currentUser) return signOut(auth);
  const provider = new GoogleAuthProvider();
  if (matchMedia('(max-width: 700px)').matches) return signInWithRedirect(auth, provider);
  return signInWithPopup(auth, provider);
};

const getJson = async (url: string) => {
  const response = await fetch(url, {cache: 'no-store'});
  if (!response.ok) throw new Error(`Could not load edit plan (${response.status})`);
  return response.json();
};

export const resolveStorageUrl = async (storagePath?: string | null) => {
  if (!storagePath) return '';
  const {storage} = await services();
  return getDownloadURL(ref(storage, storagePath));
};

export const loadProject = async (projectId: string, user: User): Promise<{project: ProjectRecord; plan: EditPlan}> => {
  const {db} = await services();
  const snapshot = await getDoc(doc(db, 'clipperProjects', projectId));
  if (!snapshot.exists()) throw new Error('Project not found.');
  const project = {id: snapshot.id, ...snapshot.data()} as ProjectRecord;
  if (project.userId !== user.uid) throw new Error('This project belongs to another account.');

  let rawPlan: EditPlan | null = null;
  const editSnapshot = await getDoc(doc(db, 'clipperProjectEdits', projectId));
  if (editSnapshot.exists() && editSnapshot.data().userId === user.uid && editSnapshot.data().plan) rawPlan = editSnapshot.data().plan as EditPlan;
  if (!rawPlan && project.editPlanStoragePath) {
    const planUrl = await resolveStorageUrl(project.editPlanStoragePath);
    rawPlan = await getJson(planUrl) as EditPlan;
  }
  if (!rawPlan) throw new Error('This project does not have an edit plan yet.');
  const plan = ensureV3(rawPlan);

  const graph = plan.scene_graph;
  const sourceAsset = graph.assets['source:primary'];
  if (sourceAsset) {
    let sourceUrl = '';
    if (project.sourceStoragePath) sourceUrl = await resolveStorageUrl(project.sourceStoragePath).catch(() => '');
    if (!sourceUrl) {
      const fallback = project.outputs?.find((output) => output.clipId === plan.clips?.[0]?.id) || project.outputs?.[0];
      if (fallback?.storagePath) sourceUrl = await resolveStorageUrl(fallback.storagePath).catch(() => '');
    }
    sourceAsset.remote_url = sourceUrl || sourceAsset.remote_url || null;
    sourceAsset.storage_path = project.sourceStoragePath || sourceAsset.storage_path || null;
  }

  for (const output of project.outputs || []) {
    const id = `render:${output.clipId}:${output.aspectRatio}`;
    if (graph.assets[id]?.remote_url) continue;
    const url = await resolveStorageUrl(output.storagePath).catch(() => '');
    graph.assets[id] = {id, type: 'video', role: 'render', name: `${output.clipId} ${output.aspectRatio}`, storage_path: output.storagePath, remote_url: url || null, metadata: {clipId: output.clipId, ratio: output.aspectRatio}};
  }

  for (const asset of Object.values(graph.assets)) {
    if (!asset.remote_url && asset.storage_path && asset.storage_path.startsWith(`users/${user.uid}/`)) {
      asset.remote_url = await resolveStorageUrl(asset.storage_path).catch(() => null);
    }
  }
  return {project, plan};
};

export const saveWorkingEdit = async (project: ProjectRecord, user: User, planInput: EditPlan) => {
  const {db} = await services();
  const plan = normalizedPlanForSave(planInput);
  await setDoc(doc(db, 'clipperProjectEdits', project.id), {
    userId: user.uid,
    projectId: project.id,
    revisionId: plan.revision?.id || 'working',
    message: plan.revision?.message || 'Working edit',
    sequence: Number(plan.revision?.sequence || 0),
    plan,
    updatedAt: serverTimestamp(),
  }, {merge: true});
  return plan;
};

export const saveRevision = async (project: ProjectRecord, user: User, planInput: EditPlan, message: string) => {
  const {db} = await services();
  const plan = normalizedPlanForSave(planInput);
  if (!plan.revision?.id) throw new Error('Revision id is missing.');
  const payload = {userId: user.uid, projectId: project.id, revisionId: plan.revision.id, message: message.slice(0, 240), sequence: Number(plan.revision.sequence || 0), plan, createdAt: serverTimestamp()};
  await Promise.all([
    setDoc(doc(db, 'clipperProjectEdits', project.id), {...payload, updatedAt: serverTimestamp()}, {merge: true}),
    setDoc(doc(db, 'clipperProjectRevisions', `${project.id}__${plan.revision.id}`), payload),
  ]);
  return plan;
};

export const listRevisions = async (project: ProjectRecord, user: User) => {
  const {db} = await services();
  const snapshot = await getDocs(query(collection(db, 'clipperProjectRevisions'), where('userId', '==', user.uid)));
  return snapshot.docs
    .map((entry) => ({id: entry.id, ...entry.data()}))
    .filter((entry) => entry.projectId === project.id)
    .sort((a, b) => Number(b.sequence || 0) - Number(a.sequence || 0));
};

const safeFileName = (name: string) => name.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 160) || 'asset';

const assetTypeFor = (file: File): SceneAsset['type'] => {
  if (file.type.startsWith('video/')) return file.type === 'image/gif' ? 'gif' : 'video';
  if (file.type.startsWith('audio/')) return 'audio';
  if (file.type === 'image/gif') return 'gif';
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.includes('json') || file.name.toLowerCase().endsWith('.json')) return 'json';
  return 'video';
};

export const uploadEditorAsset = async (file: File, project: ProjectRecord, user: User): Promise<SceneAsset> => {
  if (!file.size) throw new Error('The selected asset is empty.');
  const maxBytes = 2 * 1024 * 1024 * 1024;
  if (file.size > maxBytes) throw new Error('Editor assets are limited to 2 GB each.');
  const {storage} = await services();
  const id = `asset:${crypto.randomUUID()}`;
  const storagePath = `users/${user.uid}/project-edits/${project.id}/assets/${id.replace(':', '-')}-${safeFileName(file.name)}`;
  const storageRef = ref(storage, storagePath);
  const task = uploadBytesResumable(storageRef, file, {contentType: file.type || 'application/octet-stream'});
  await new Promise<void>((resolve, reject) => task.on('state_changed', undefined, reject, resolve));
  const remoteUrl = await getDownloadURL(storageRef);
  return {
    id,
    type: assetTypeFor(file),
    role: 'editor-asset',
    name: file.name,
    storage_path: storagePath,
    remote_url: remoteUrl,
    metadata: {size: file.size, contentType: file.type || null},
  };
};

export const queueRerender = async (
  project: ProjectRecord,
  user: User,
  planInput: EditPlan,
  options: {ratios: Ratio[]; backend: 'auto' | 'ffmpeg' | 'remotion'},
) => {
  const {db} = await services();
  const jobRef = doc(collection(db, 'clipperJobs'));
  const plan = normalizedPlanForSave(planInput);
  await setDoc(jobRef, {
    userId: user.uid,
    jobType: 'rerender',
    projectId: project.id,
    editPlan: plan,
    ratios: options.ratios,
    renderBackend: options.backend,
    status: 'queued',
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  });
  return jobRef.id;
};