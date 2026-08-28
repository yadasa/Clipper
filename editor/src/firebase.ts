import {initializeApp} from 'firebase/app';
import {GoogleAuthProvider, getAuth, getRedirectResult, onAuthStateChanged, signInWithPopup, signInWithRedirect, signOut, type User} from 'firebase/auth';
import {collection, doc, getDoc, getDocs, getFirestore, query, serverTimestamp, setDoc, where} from 'firebase/firestore';
import {getDownloadURL, getStorage, ref} from 'firebase/storage';
import type {EditPlan, ProjectRecord, Ratio} from './types';
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

  // Hydrate prior output variants as reusable assets so an editor can use a
  // finished render as a fallback source or B-roll without another upload.
  for (const output of project.outputs || []) {
    const id = `render:${output.clipId}:${output.aspectRatio}`;
    if (graph.assets[id]?.remote_url) continue;
    const url = await resolveStorageUrl(output.storagePath).catch(() => '');
    graph.assets[id] = {id, type: 'video', role: 'render', name: `${output.clipId} ${output.aspectRatio}`, storage_path: output.storagePath, remote_url: url || null, metadata: {clipId: output.clipId, ratio: output.aspectRatio}};
  }
  return {project, plan};
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
