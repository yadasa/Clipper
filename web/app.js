import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-app.js';
import { getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signInWithRedirect, getRedirectResult, signOut } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-auth.js';
import { getFirestore, collection, doc, getDocs, query, setDoc, serverTimestamp, where } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js';
import { getStorage, ref, uploadBytesResumable, getDownloadURL } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-storage.js';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const configResponse = await fetch('/__/firebase/init.json');
if (!configResponse.ok) throw new Error('Firebase Hosting configuration is unavailable. Run through Firebase Hosting or firebase serve.');
const firebaseConfig = await configResponse.json();
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const storage = getStorage(app);
const provider = new GoogleAuthProvider();

let currentUser = null;
let sourceMode = 'device';
const urlCache = new Map();

function escapeHtml(value='') {
  return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function timeValue(value) {
  try { return value?.toMillis?.() || new Date(value || 0).getTime() || 0; } catch { return 0; }
}
function selectedRatios() { return $$('input[name="ratio"]:checked').map(el => el.value); }
function setMessage(text, error=false) { const el=$('#uploadMessage'); el.textContent=text; el.style.color=error?'#824d3d':''; }
function setProgress(value) { const box=$('#uploadProgress'); box.classList.toggle('hidden', value == null); if(value!=null) box.firstElementChild.style.width=`${Math.max(0,Math.min(100,value))}%`; }
function updateQueueButton() {
  const ready = !!currentUser && selectedRatios().length > 0 && (sourceMode==='device' ? !!$('#fileInput').files[0] : !!$('#sourceUrl').value.trim());
  $('#queueButton').disabled = !ready;
}

$$('.tab').forEach(button => button.addEventListener('click', () => {
  sourceMode = button.dataset.sourceTab;
  $$('.tab').forEach(x => x.classList.toggle('active', x===button));
  $('#devicePane').classList.toggle('active', sourceMode==='device');
  $('#linkPane').classList.toggle('active', sourceMode==='link');
  updateQueueButton();
}));
$('#fileInput').addEventListener('change', () => { $('#fileName').textContent=$('#fileInput').files[0]?.name || 'Nothing selected'; updateQueueButton(); });
$('#sourceUrl').addEventListener('input', updateQueueButton);
$$('input[name="ratio"]').forEach(input => input.addEventListener('change', () => { input.closest('.ratio').classList.toggle('selected', input.checked); updateQueueButton(); }));

async function authenticate() {
  if (currentUser) { await signOut(auth); return; }
  if (matchMedia('(max-width: 700px)').matches) await signInWithRedirect(auth, provider);
  else await signInWithPopup(auth, provider);
}
$('#authButton').addEventListener('click', authenticate);
await getRedirectResult(auth).catch(()=>null);

async function resolveOutput(output) {
  if (urlCache.has(output.storagePath)) return urlCache.get(output.storagePath);
  const url = await getDownloadURL(ref(storage, output.storagePath));
  urlCache.set(output.storagePath, url);
  return url;
}

function variantClass(ratio) {
  if (ratio === '16:9') return 'landscape';
  if (ratio === '1:1') return 'square';
  return '';
}

async function renderDoneProject(project) {
  const groups = {};
  for (const output of project.outputs || []) (groups[output.clipId] ||= []).push(output);
  const blocks = [];
  for (const [clipId, outputs] of Object.entries(groups)) {
    const variants = await Promise.all(outputs.map(async output => {
      let url=''; try { url=await resolveOutput(output); } catch {}
      return `<div class="variant ${variantClass(output.aspectRatio)}">
        ${url ? `<video controls playsinline preload="metadata" src="${escapeHtml(url)}"></video>` : '<div class="fine">Video URL unavailable</div>'}
        <div class="variant-footer"><span><i class="color-dot"></i>${escapeHtml(output.aspectRatio)}</span>${url ? `<a href="${escapeHtml(url)}" download="${escapeHtml(output.filename)}">Download</a>`:''}</div>
      </div>`;
    }));
    blocks.push(`<div class="clip-block"><div class="clip-title"><strong>${escapeHtml(outputs[0]?.title || clipId)}</strong><span class="meta">${outputs.length} version${outputs.length===1?'':'s'}</span></div><div class="variants">${variants.join('')}</div></div>`);
  }
  return `<article class="project card"><div class="project-top"><div><h3>${escapeHtml(project.sourceName || 'Untitled source')}</h3><div class="meta">${project.clipCount || Object.keys(groups).length} clips · ${(project.ratios||[]).join(' · ')}</div></div><span class="job-status done">done</span></div><div class="clips">${blocks.join('') || '<div class="fine">No output files were returned.</div>'}</div></article>`;
}

function renderJob(job) {
  const message = job.lastError ? `<div class="fine" style="margin-top:10px">${escapeHtml(job.lastError)}</div>` : '';
  return `<article class="project card"><div class="project-top"><div><h3>${escapeHtml(job.sourceName || job.sourceUrl || 'Queued source')}</h3><div class="meta">${(job.ratios||[]).join(' · ')}</div></div><span class="job-status ${escapeHtml(job.status)}">${escapeHtml(job.status || 'queued')}</span></div>${message}</article>`;
}

async function loadProjects() {
  if (!currentUser) { $('#projects').innerHTML='<div class="empty card">Sign in to see queued and finished videos.</div>'; return; }
  $('#projects').innerHTML='<div class="empty card">Loading your edits…</div>';
  try {
    const [jobSnap, projectSnap] = await Promise.all([
      getDocs(query(collection(db,'clipperJobs'), where('userId','==',currentUser.uid))),
      getDocs(query(collection(db,'clipperProjects'), where('userId','==',currentUser.uid))),
    ]);
    const projects = projectSnap.docs.map(s => ({id:s.id,...s.data()}));
    const completedIds = new Set(projects.map(p=>p.id));
    const jobs = jobSnap.docs.map(s => ({id:s.id,...s.data()})).filter(j=>!completedIds.has(j.id));
    const rows = [
      ...projects.map(p=>({kind:'project',data:p,sort:timeValue(p.completedAt)||timeValue(p.createdAt)})),
      ...jobs.map(j=>({kind:'job',data:j,sort:timeValue(j.createdAt)})),
    ].sort((a,b)=>b.sort-a.sort);
    if (!rows.length) { $('#projects').innerHTML='<div class="empty card">No projects yet. Queue your first video above.</div>'; return; }
    const html=[];
    for (const row of rows) html.push(row.kind==='project' ? await renderDoneProject(row.data) : renderJob(row.data));
    $('#projects').innerHTML=html.join('');
    const active = jobs.find(j=>['claimed','processing','uploading'].includes(j.status));
    $('#workerState').textContent = active ? `Desktop ${active.status}` : 'Firebase queue ready';
    $('#workerState').className='status-pill live';
  } catch (error) {
    $('#projects').innerHTML=`<div class="empty card">Couldn’t load projects: ${escapeHtml(error.message)}</div>`;
  }
}
$('#refreshButton').addEventListener('click', loadProjects);

async function queueJob() {
  if (!currentUser) return;
  const ratios=selectedRatios();
  if (!ratios.length) return setMessage('Pick at least one aspect ratio.',true);
  const jobRef=doc(collection(db,'clipperJobs'));
  const base={userId:currentUser.uid,status:'queued',ratios,alternateVisualLayouts:$('#alternateLayouts').checked,createdAt:serverTimestamp(),updatedAt:serverTimestamp()};
  $('#queueButton').disabled=true; setMessage('Preparing job…'); setProgress(0);
  try {
    if (sourceMode==='device') {
      const file=$('#fileInput').files[0]; if(!file) throw new Error('Choose a file first.');
      const safe=file.name.replace(/[^a-zA-Z0-9._-]+/g,'-');
      const storagePath=`users/${currentUser.uid}/sources/${jobRef.id}/${safe}`;
      const task=uploadBytesResumable(ref(storage,storagePath),file,{contentType:file.type||'application/octet-stream'});
      await new Promise((resolve,reject)=>task.on('state_changed',snap=>setProgress((snap.bytesTransferred/snap.totalBytes)*100),reject,resolve));
      await setDoc(jobRef,{...base,sourceStoragePath:storagePath,sourceName:file.name,ownContentAck:true});
    } else {
      const sourceUrl=$('#sourceUrl').value.trim();
      if(!sourceUrl) throw new Error('Paste a link first.');
      if(!$('#ownContentAck').checked) throw new Error('Confirm that you own or are authorized to reuse this social post.');
      await setDoc(jobRef,{...base,sourceUrl,sourceName:sourceUrl,ownContentAck:true});
    }
    setProgress(100); setMessage('Queued. Your home computer will claim it when the worker is online.');
    setTimeout(()=>setProgress(null),700);
    await loadProjects();
  } catch(error) { setProgress(null); setMessage(error.message||String(error),true); }
  finally { updateQueueButton(); }
}
$('#queueButton').addEventListener('click',queueJob);

onAuthStateChanged(auth, async user => {
  currentUser=user;
  $('#userLabel').textContent=user?.displayName || user?.email || 'Signed out';
  $('#authButton').textContent=user?'Sign out':'Sign in';
  $('#workerState').textContent=user?'Firebase queue ready':'Waiting for sign-in';
  $('#workerState').className=`status-pill ${user?'live':'muted'}`;
  updateQueueButton();
  await loadProjects();
});
