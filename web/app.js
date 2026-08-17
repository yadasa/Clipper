import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-app.js';
import { getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signInWithRedirect, getRedirectResult, signOut } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-auth.js';
import { getFirestore, collection, doc, getDoc, getDocs, query, setDoc, serverTimestamp, where } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js';
import { getStorage, ref, uploadBytesResumable, getDownloadURL, deleteObject } from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-storage.js';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const configResponse = await fetch('/__/firebase/init.json');
if (!configResponse.ok) throw new Error('Firebase Hosting configuration is unavailable. Run through Firebase Hosting or firebase serve.');
const app = initializeApp(await configResponse.json());
const auth = getAuth(app);
const db = getFirestore(app);
const storage = getStorage(app);
const provider = new GoogleAuthProvider();

let currentUser = null;
let sourceMode = 'device';
const urlCache = new Map();
const SOCIAL_HOSTS = new Set([
  'instagram.com','www.instagram.com','tiktok.com','www.tiktok.com','vm.tiktok.com',
  'youtube.com','www.youtube.com','youtu.be','facebook.com','www.facebook.com',
  'x.com','www.x.com','twitter.com','www.twitter.com'
]);

function escapeHtml(value='') { return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function timeValue(value) { try { return value?.toMillis?.() || new Date(value || 0).getTime() || 0; } catch { return 0; } }
function selectedRatios() { return $$('input[name="ratio"]:checked').map(el => el.value); }
function selectedPlatforms() { return $$('input[name="platform"]:checked').map(el => el.value); }
function setMessage(text, error=false) { const el=$('#uploadMessage'); el.textContent=text; el.style.color=error?'#824d3d':''; }
function setProgress(value) { const box=$('#uploadProgress'); box.classList.toggle('hidden', value == null); if(value!=null) box.firstElementChild.style.width=`${Math.max(0,Math.min(100,value))}%`; }
function updateQueueButton() {
  const ready = !!currentUser && selectedRatios().length > 0 && (sourceMode==='device' ? !!$('#fileInput').files[0] : !!$('#sourceUrl').value.trim());
  $('#queueButton').disabled = !ready;
}
function safeFileName(file) {
  const cleaned=String(file?.name||'upload').replace(/[^a-zA-Z0-9._-]+/g,'-').replace(/^-+|-+$/g,'');
  return cleaned.slice(0,180) || 'upload';
}
function isSupportedSocialUrl(value) {
  try {
    const url=new URL(value);
    if (!['http:','https:'].includes(url.protocol)) return false;
    const host=url.hostname.toLowerCase().replace(/\.$/,'');
    return SOCIAL_HOSTS.has(host) || [...SOCIAL_HOSTS].some(base=>host.endsWith(`.${base}`));
  } catch { return false; }
}

$$('.tab').forEach(button => button.addEventListener('click', () => {
  sourceMode = button.dataset.sourceTab;
  $$('.tab').forEach(x => x.classList.toggle('active', x===button));
  $('#devicePane').classList.toggle('active', sourceMode==='device');
  $('#linkPane').classList.toggle('active', sourceMode==='link');
  updateQueueButton();
}));
$('#fileInput').addEventListener('change', () => { $('#fileName').textContent=$('#fileInput').files[0]?.name || 'Nothing selected'; updateQueueButton(); });
$('#secondaryInput').addEventListener('change', () => { const n=$('#secondaryInput').files.length; $('#secondaryName').textContent=n ? `${n} camera file${n===1?'':'s'} selected` : 'Optional · multicam'; });
$('#externalAudioInput').addEventListener('change', () => { $('#externalAudioName').textContent=$('#externalAudioInput').files[0]?.name || 'Optional · audio sync'; });
$('#logoInput').addEventListener('change', () => { $('#logoName').textContent=$('#logoInput').files[0]?.name || 'Optional · transparent PNG works best'; });
$('#musicInput').addEventListener('change', () => { $('#musicName').textContent=$('#musicInput').files[0]?.name || 'Optional · auto-ducked under speech'; });
$('#sourceUrl').addEventListener('input', updateQueueButton);
$$('input[name="ratio"]').forEach(input => input.addEventListener('change', () => { input.closest('.ratio').classList.toggle('selected', input.checked); updateQueueButton(); }));

async function authenticate() {
  if (currentUser) { await signOut(auth); return; }
  if (matchMedia('(max-width: 700px)').matches) await signInWithRedirect(auth, provider);
  else await signInWithPopup(auth, provider);
}
$('#authButton').addEventListener('click', authenticate);
await getRedirectResult(auth).catch(()=>null);

async function resolveStoragePath(storagePath) {
  if (!storagePath) return '';
  if (urlCache.has(storagePath)) return urlCache.get(storagePath);
  const url = await getDownloadURL(ref(storage, storagePath));
  urlCache.set(storagePath, url);
  return url;
}
function variantClass(ratio) { if (ratio === '16:9') return 'landscape'; if (ratio === '1:1') return 'square'; return ''; }
function scoreLabel(value) { const score=Number(value); return Number.isFinite(score) ? `${Math.round(score)}/100` : ''; }

async function renderDoneProject(project) {
  const groups = {};
  for (const output of project.outputs || []) (groups[output.clipId] ||= []).push(output);
  const blocks = [];
  for (const [clipId, outputs] of Object.entries(groups)) {
    const clipMeta=project.clipMetadata?.[clipId] || {};
    const candidate=clipMeta.candidate || {};
    const variants = await Promise.all(outputs.map(async output => {
      let url=''; let poster='';
      try { url=await resolveStoragePath(output.storagePath); } catch {}
      try { poster=await resolveStoragePath(output.thumbnailStoragePath); } catch {}
      const layout=output.layoutMode && output.layoutMode!=='auto' ? ` · ${output.layoutMode}` : '';
      return `<div class="variant ${variantClass(output.aspectRatio)}">
        ${url ? `<video controls playsinline preload="metadata" ${poster?`poster="${escapeHtml(poster)}"`:''} src="${escapeHtml(url)}"></video>` : '<div class="fine">Video URL unavailable</div>'}
        <div class="variant-footer"><span><i class="color-dot"></i>${escapeHtml(output.aspectRatio)}${escapeHtml(layout)}</span>${url ? `<a href="${escapeHtml(url)}" download="${escapeHtml(output.filename)}">Download</a>`:''}</div>
      </div>`;
    }));
    const metrics=candidate.metrics || outputs[0]?.metrics || {};
    const metricBits=['hook','clarity','payoff'].filter(k=>metrics[k]!=null).map(k=>`${k} ${Math.round(metrics[k])}`).join(' · ');
    const hook=clipMeta.hookText ? `<div class="fine">Hook: ${escapeHtml(clipMeta.hookText)}</div>` : '';
    const brollProviders=[...new Set((clipMeta.visualCues||[]).map(cue=>cue.provider).filter(Boolean))];
    const broll=brollProviders.length ? `<div class="fine">B-roll: ${escapeHtml(brollProviders.join(' · '))}</div>` : '';
    const score=scoreLabel(candidate.score ?? outputs[0]?.score);
    blocks.push(`<div class="clip-block"><div class="clip-title"><strong>${escapeHtml(outputs[0]?.title || candidate.title || clipId)}</strong><span class="meta">${score ? `${score} · `:''}${outputs.length} version${outputs.length===1?'':'s'}</span></div>${metricBits?`<div class="fine">${escapeHtml(metricBits)}</div>`:''}${hook}${broll}<div class="variants">${variants.join('')}</div></div>`);
  }
  const published=(project.publishPlatforms||[]).length ? `<div class="fine" style="margin-top:12px">Publish targets: ${escapeHtml((project.publishPlatforms||[]).join(' · '))}${(project.publishErrors||[]).length ? ` · ${project.publishErrors.length} publish issue${project.publishErrors.length===1?'':'s'}` : ' · submitted'}</div>` : '';
  const errors=(project.publishErrors||[]).length ? `<details class="extras"><summary>Publishing issues</summary><p class="fine">${(project.publishErrors||[]).map(escapeHtml).join('<br>')}</p></details>` : '';
  let plan='';
  if(project.editPlanStoragePath){ try { const planUrl=await resolveStoragePath(project.editPlanStoragePath); plan=`<a class="plan-link" href="${escapeHtml(planUrl)}" download="edit_plan.json">Download editable plan</a>`; } catch {} }
  const hardware=project.hardwareProfile?.name ? ` · ${escapeHtml(project.hardwareProfile.name)}` : '';
  return `<article class="project card"><div class="project-top"><div><h3>${escapeHtml(project.sourceName || 'Untitled source')}</h3><div class="meta">${project.clipCount || Object.keys(groups).length} clips · ${(project.ratios||[]).join(' · ')}${hardware}</div></div><div class="project-actions">${plan}<span class="job-status done">done</span></div></div>${published}${errors}<div class="clips">${blocks.join('') || '<div class="fine">No output files were returned.</div>'}</div></article>`;
}
function renderJob(job) {
  const message = job.lastError ? `<div class="fine" style="margin-top:10px">${escapeHtml(job.lastError)}</div>` : '';
  const publish = job.publishPlatforms?.length ? ` · publish ${job.publishPlatforms.join(', ')}` : '';
  const intelligence=`${job.smartCut===false?' · raw pauses':' · smart cuts'}${job.punchIns===false?'':' · punch-ins'}${job.musicStoragePath?' · music':''}`;
  return `<article class="project card"><div class="project-top"><div><h3>${escapeHtml(job.sourceName || job.sourceUrl || 'Queued source')}</h3><div class="meta">${(job.ratios||[]).join(' · ')}${job.secondaryStoragePaths?.length ? ` · ${job.secondaryStoragePaths.length+1} cameras` : ''}${job.externalAudioStoragePath ? ' · separate mic' : ''}${escapeHtml(intelligence)}${escapeHtml(publish)}</div></div><span class="job-status ${escapeHtml(job.status)}">${escapeHtml(job.status || 'queued')}</span></div>${message}</article>`;
}

async function loadProjects() {
  if (!currentUser) { $('#projects').innerHTML='<div class="empty card">Sign in to see queued and finished videos.</div>'; return; }
  $('#projects').innerHTML='<div class="empty card">Loading your edits…</div>';
  try {
    const [jobSnap, projectSnap, workerSnap] = await Promise.all([
      getDocs(query(collection(db,'clipperJobs'), where('userId','==',currentUser.uid))),
      getDocs(query(collection(db,'clipperProjects'), where('userId','==',currentUser.uid))),
      getDocs(collection(db,'clipperWorkers')),
    ]);
    const projects = projectSnap.docs.map(s => ({id:s.id,...s.data()}));
    const completedIds = new Set(projects.map(p=>p.id));
    const jobs = jobSnap.docs.map(s => ({id:s.id,...s.data()})).filter(j=>!completedIds.has(j.id));
    const rows = [
      ...projects.map(p=>({kind:'project',data:p,sort:timeValue(p.completedAt)||timeValue(p.createdAt)})),
      ...jobs.map(j=>({kind:'job',data:j,sort:timeValue(j.createdAt)})),
    ].sort((a,b)=>b.sort-a.sort);
    if (!rows.length) $('#projects').innerHTML='<div class="empty card">No projects yet. Queue your first video above.</div>';
    else {
      const html=[];
      for (const row of rows) html.push(row.kind==='project' ? await renderDoneProject(row.data) : renderJob(row.data));
      $('#projects').innerHTML=html.join('');
    }
    const now=Date.now();
    const workers=workerSnap.docs.map(s=>s.data()).filter(w=>now-timeValue(w.lastSeenAt)<150000 && w.state!=='offline');
    const active=workers.find(w=>['processing','uploading','publishing'].includes(w.state)) || workers[0];
    $('#workerState').textContent = active ? `Home desktop ${active.state}` : 'Home desktop offline';
    $('#workerState').className=`status-pill ${active?'live':'muted'}`;
  } catch (error) {
    $('#projects').innerHTML=`<div class="empty card">Couldn’t load projects: ${escapeHtml(error.message)}</div>`;
  }
}
$('#refreshButton').addEventListener('click', loadProjects);

async function uploadOne(file, storagePath, baseBytes, totalBytes) {
  const task=uploadBytesResumable(ref(storage,storagePath),file,{contentType:file.type||'application/octet-stream'});
  await new Promise((resolve,reject)=>task.on('state_changed',snap=>setProgress(totalBytes ? ((baseBytes+snap.bytesTransferred)/totalBytes)*100 : 0),reject,resolve));
  return storagePath;
}

async function cleanupUploadedPaths(paths) {
  await Promise.allSettled(paths.map(path=>deleteObject(ref(storage,path))));
}

async function queueJob() {
  if (!currentUser) return;
  const ratios=selectedRatios();
  if (!ratios.length) return setMessage('Pick at least one aspect ratio.',true);
  const jobRef=doc(collection(db,'clipperJobs'));
  const primaryFile=sourceMode==='device' ? $('#fileInput').files[0] : null;
  const secondaryFiles=[...$('#secondaryInput').files];
  const externalAudio=$('#externalAudioInput').files[0] || null;
  const musicFile=$('#musicInput').files[0] || null;
  const logoFile=$('#logoInput').files[0] || null;
  const allUploads=[...(primaryFile?[primaryFile]:[]),...secondaryFiles,...(externalAudio?[externalAudio]:[]),...(musicFile?[musicFile]:[]),...(logoFile?[logoFile]:[])];
  const totalBytes=allUploads.reduce((sum,file)=>sum+file.size,0);
  const uploadedPaths=[];
  let sent=0;
  let jobCreated=false;
  const base={
    userId:currentUser.uid,
    status:'queued',
    ratios,
    alternateVisualLayouts:$('#alternateLayouts').checked,
    smartCut:$('#smartCut').checked,
    removeFillers:$('#removeFillers').checked,
    punchIns:$('#punchIns').checked,
    hookOverlay:$('#hookOverlay').checked,
    captionPreset:$('#captionPreset').value,
    brand:{
      name:'web',
      accent:$('#brandAccent').value,
      primary_text:$('#brandText').value,
      font:$('#brandFont').value.trim() || 'Arial',
      caption_preset:$('#captionPreset').value,
    },
    publishPlatforms:selectedPlatforms(),
    publishDescription:$('#publishDescription').value.trim(),
    createdAt:serverTimestamp(),
    updatedAt:serverTimestamp()
  };
  $('#queueButton').disabled=true; setMessage('Preparing job…'); setProgress(totalBytes?0:null);
  try {
    const extras={secondaryStoragePaths:[]};
    if (primaryFile) {
      const path=`users/${currentUser.uid}/sources/${jobRef.id}/${safeFileName(primaryFile)}`;
      await uploadOne(primaryFile,path,sent,totalBytes); uploadedPaths.push(path); sent+=primaryFile.size;
      base.sourceStoragePath=path; base.sourceName=primaryFile.name; base.ownContentAck=true;
    } else {
      const sourceUrl=$('#sourceUrl').value.trim();
      if(!sourceUrl) throw new Error('Paste a link first.');
      if(!isSupportedSocialUrl(sourceUrl)) throw new Error('Use a supported Instagram, TikTok, YouTube, Facebook, or X link.');
      if(!$('#ownContentAck').checked) throw new Error('Confirm that you own or are authorized to reuse this social post.');
      base.sourceUrl=sourceUrl; base.sourceName=sourceUrl; base.ownContentAck=true;
    }
    for (let i=0;i<secondaryFiles.length;i++) {
      const file=secondaryFiles[i];
      const path=`users/${currentUser.uid}/sources/${jobRef.id}/camera-${i+2}-${safeFileName(file)}`;
      extras.secondaryStoragePaths.push(await uploadOne(file,path,sent,totalBytes)); uploadedPaths.push(path); sent+=file.size;
    }
    if (externalAudio) {
      const path=`users/${currentUser.uid}/sources/${jobRef.id}/mic-${safeFileName(externalAudio)}`;
      extras.externalAudioStoragePath=await uploadOne(externalAudio,path,sent,totalBytes); uploadedPaths.push(path); sent+=externalAudio.size;
    }
    if (musicFile) {
      const path=`users/${currentUser.uid}/sources/${jobRef.id}/music-${safeFileName(musicFile)}`;
      extras.musicStoragePath=await uploadOne(musicFile,path,sent,totalBytes); uploadedPaths.push(path); sent+=musicFile.size;
    }
    if (logoFile) {
      const path=`users/${currentUser.uid}/sources/${jobRef.id}/logo-${safeFileName(logoFile)}`;
      extras.logoStoragePath=await uploadOne(logoFile,path,sent,totalBytes); uploadedPaths.push(path); sent+=logoFile.size;
    }
    await setDoc(jobRef,{...base,...extras});
    jobCreated=true;
    setProgress(totalBytes?100:null); setMessage('Queued. Your home computer will claim it when the worker is online.');
    if(totalBytes) setTimeout(()=>setProgress(null),700);
    await loadProjects();
  } catch(error) {
    setProgress(null);
    // A network timeout can make setDoc's result ambiguous: the server may have
    // accepted the job even though the client saw an error. Verify before deleting
    // uploaded sources; if verification itself fails, preserve them rather than
    // breaking a potentially live queue item.
    if (!jobCreated && uploadedPaths.length) {
      let definitelyNotQueued=false;
      try { definitelyNotQueued=!(await getDoc(jobRef)).exists(); } catch {}
      if (definitelyNotQueued) await cleanupUploadedPaths(uploadedPaths);
    }
    setMessage(error.message||String(error),true);
  } finally { updateQueueButton(); }
}
$('#queueButton').addEventListener('click',queueJob);

onAuthStateChanged(auth, async user => {
  currentUser=user;
  $('#userLabel').textContent=user?.displayName || user?.email || 'Signed out';
  $('#authButton').textContent=user?'Sign out':'Sign in';
  $('#workerState').textContent=user?'Checking home desktop…':'Waiting for sign-in';
  $('#workerState').className=`status-pill ${user?'live':'muted'}`;
  updateQueueButton();
  await loadProjects();
});
