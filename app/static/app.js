let pets = [], activePet = null, selectedIds = new Set(), refsIds = [], negIds = [], immichUrl = 'http://localhost:2283', negCandidateMode = false, borderlineMode = false, scanLowConfMode = false, lastClickedId = null, lastNegTopScore = null, negGeneration = 0, negPollTimer = null, blGeneration = 0, blPollTimer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) { const t = await r.text().catch(() => r.statusText); throw new Error(t); }
  return r.json();
}

function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(el._t); el._t = setTimeout(() => el.className = 'toast', 2500);
}

function initials(name) { return name.slice(0, 2).toUpperCase(); }

async function refreshState() {
  try {
    const cfg = await api('/api/config');
    immichUrl = cfg.immich_external_url.replace(/\/$/, '');
  } catch(e) {}
  loadPets();
  loadNegatives();
}

// ---------------------------------------------------------------------------
// Pets
// ---------------------------------------------------------------------------

async function loadPets(keepActive = false) {
  try {
    const d = await api('/api/pets');
    pets = d.pets;
    if (activePet) {
      activePet = pets.find(p => p.name === activePet.name) || activePet;
    }
    renderSidebar();
    updateNegStatus();
    if (!keepActive && !activePet && pets.length > 0) await selectPet(pets[0].name);
  } catch(e) { toast('Could not load pets: ' + e.message, 'error'); }
}

function renderSidebar() {
  const el = document.getElementById('petsList');
  if (!pets.length) {
    el.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--text3);text-align:center;line-height:1.6;">No pets yet.<br>Add one to get started.</div>';
    document.getElementById('photoGrid').innerHTML = '<div class="empty" style="grid-column:1/-1;height:300px;"><div class="empty-icon">🐾</div><div class="empty-title">No pets yet</div><div class="empty-sub">Add a pet using the sidebar to get started</div></div>';
    document.getElementById('refsTitle').textContent = 'No pet selected';
    document.getElementById('findRefsBtn').style.display = 'none';
    document.getElementById('clearRefsBtn').style.display = 'none';
    document.getElementById('refsGrid').innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-sub">Add a pet first</div></div>';
    return;
  }
  el.innerHTML = pets.map(p => `
    <div class="pet-item ${activePet?.name === p.name ? 'active' : ''}" onclick="selectPet('${p.name}')">
      <div class="pet-avatar">${p.person_id ? `<img src="/api/person-thumb/${p.person_id}" onerror="this.parentElement.textContent='${initials(p.name)}'" alt="">` : initials(p.name)}</div>
      <div class="pet-info">
        <div class="pet-name">${p.name}</div>
        <div class="pet-count">${p.ref_count} ref${p.ref_count !== 1 ? 's' : ''}</div>
      </div>
      <button class="pet-edit" onclick="event.stopPropagation(); openEditPet('${p.name}')" title="Edit">✎</button>
      <button class="pet-delete" onclick="event.stopPropagation(); openDeletePet('${p.name}')" title="Delete">✕</button>
    </div>`).join('');
}

function clearSearch() {
  document.getElementById('resultsLabel').textContent = '';
  document.getElementById('photoGrid').innerHTML = '<div class="empty" style="grid-column:1/-1; height:300px;"><div class="empty-icon">🐾</div><div class="empty-title">Find photos</div><div class="empty-sub">Click "Find references" to get started</div></div>';
  selectedIds.clear(); lastClickedId = null; updateSelUI();
}

async function selectPet(name) {
  if (activePet?.name === name) return;
  if (selectedIds.size > 0) {
    const ok = confirm(`You have ${selectedIds.size} selected photo${selectedIds.size !== 1 ? 's' : ''} not yet assigned. Switch anyway?`);
    if (!ok) return;
  }
  negCandidateMode = false; borderlineMode = false; scanLowConfMode = false;
  activePet = pets.find(p => p.name === name);
  clearSearch(); renderSidebar();
  document.getElementById('refsTitle').textContent = name;
  document.getElementById('findRefsBtn').style.display = '';
  document.getElementById('clearRefsBtn').style.display = '';
  await loadRefs(name);
  await loadNegatives();
}

async function loadRefs(name) {
  const grid = document.getElementById('refsGrid');
  grid.innerHTML = '<div class="loading">Loading…</div>';
  try {
    const d = await api(`/api/pets/${encodeURIComponent(name)}/assets`);
    refsIds = d.assets.map(a => a.id); renderRefs(d.assets);
  } catch(e) { grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><div class="empty-sub">Error loading refs</div></div>'; }
}

function renderRefs(assets) {
  const grid = document.getElementById('refsGrid');
  if (!assets.length) { grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:160px;"><div class="empty-sub">No references yet.<br>Click "Find references" to add some.</div></div>'; return; }
  grid.innerHTML = assets.map(a => `
    <div class="ref-thumb">
      <a href="${immichUrl}/photos/${a.id}" target="_blank" rel="noopener" title="Open in Immich">
        <img src="${a.thumb}" loading="lazy" onerror="this.style.opacity=0.2">
      </a>
      <button class="ref-remove" onclick="removeRef('${a.id}')" title="Remove">✕</button>
    </div>`).join('');
}

async function removeRef(id) {
  if (!activePet) return;
  try {
    await api(`/api/pets/${encodeURIComponent(activePet.name)}/assets/${id}`, { method: 'DELETE' });
    refsIds = refsIds.filter(i => i !== id);
    await loadRefs(activePet.name);
    await refreshState();
    toast('Removed');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function assignSelected() {
  if (!activePet || !selectedIds.size) return;
  const newIds = [...new Set([...refsIds, ...selectedIds])];
  try {
    await api(`/api/pets/${encodeURIComponent(activePet.name)}/assets`, { method: 'POST', body: { asset_ids: newIds } });
    refsIds = newIds; selectedIds.clear(); updateSelUI();
    document.querySelectorAll('.photo-thumb.selected').forEach(el => { el.classList.remove('selected'); el.classList.add('is-ref'); });
    await loadRefs(activePet.name);
    await refreshState();
    toast(`Added to ${activePet.name}`, 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Ref suggestions
// ---------------------------------------------------------------------------

function viewFindRefs() {
  if (!activePet) return;
  if (activePet.ref_count > 0) viewBorderline();
  else viewSuggestions();
}

async function viewSuggestions() {
  if (!activePet) return;
  if (!activePet.description) { toast('Edit this pet and add a description to use this feature', 'error'); return; }
  selectedIds.clear(); lastClickedId = null; updateSelUI();
  const grid = document.getElementById('photoGrid');
  const label = document.getElementById('resultsLabel');
  grid.innerHTML = '<div class="loading" style="grid-column:1/-1">Finding similar photos… this may take a moment</div>';
  label.textContent = 'Finding references…';
  try {
    const d = await api(`/api/pets/${encodeURIComponent(activePet.name)}/suggestions`);
    label.textContent = `${d.assets.length} photo${d.assets.length !== 1 ? 's' : ''} similar to ${activePet.name}'s refs`;
    if (!d.assets.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-icon">🐾</div><div class="empty-title">No suggestions found</div><div class="empty-sub">Add more refs or broaden the date range</div></div>';
      return;
    }
    grid.innerHTML = d.assets.map(a => `
      <div class="photo-thumb" id="th-${a.id}" onclick="toggleSelect(event, '${a.id}')" title="${a.filename} · ${fmtDate(a.date)}">
        <img src="${a.thumb}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg/>'">
        <a class="photo-open" href="${immichUrl}/photos/${a.id}" target="_blank" rel="noopener" onclick="event.stopPropagation()">⤢</a>
        <div class="photo-check">✓</div>
      </div>`).join('');
    const refSet = new Set(refsIds), negSet = new Set(negIds);
    d.assets.forEach(a => {
      if (refSet.has(a.id)) document.getElementById('th-' + a.id)?.classList.add('is-ref');
      if (negSet.has(a.id)) document.getElementById('th-' + a.id)?.classList.add('is-neg');
    });
  } catch(e) {
    label.textContent = 'Failed to load suggestions';
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-sub">${e.message}</div></div>`;
    toast('Suggestions error: ' + e.message, 'error');
  }
}

async function viewBorderline() {
  if (!activePet || !activePet.ref_count) return;
  const myGen = ++blGeneration;
  if (blPollTimer) { clearInterval(blPollTimer); blPollTimer = null; }
  negCandidateMode = false; borderlineMode = true;
  selectedIds.clear(); lastClickedId = null; updateSelUI();
  const grid = document.getElementById('photoGrid');
  const label = document.getElementById('resultsLabel');
  const petName = activePet.name;
  grid.innerHTML = '<div class="loading" id="blLoadMsg" style="grid-column:1/-1">Loading…</div>';
  label.textContent = 'Finding references…';

  blPollTimer = setInterval(async () => {
    if (blGeneration !== myGen) { clearInterval(blPollTimer); blPollTimer = null; return; }
    try {
      const p = await api(`/api/pets/${encodeURIComponent(petName)}/borderline/progress`);
      const el = document.getElementById('blLoadMsg');
      if (!el) return;
      if (p.total > 0) el.textContent = `Loading ${Math.round(p.current / p.total * 100)}%…`;
      else if (p.running) el.textContent = 'Loading…';
    } catch(_) {}
  }, 1000);

  try {
    const d = await api(`/api/pets/${encodeURIComponent(petName)}/borderline`);
    clearInterval(blPollTimer); blPollTimer = null;
    if (blGeneration !== myGen) return;
    label.textContent = `${d.assets.length} photo${d.assets.length !== 1 ? 's' : ''} ${petName} might be missing. Add good ones as refs to improve accuracy.`;
    if (!d.assets.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-icon">🐾</div><div class="empty-title">No missed photos found</div><div class="empty-sub">The classifier is either very confident or not finding this pet at all</div></div>';
      return;
    }
    const thr = d.threshold ?? 0.8;
    grid.innerHTML = d.assets.map(a => {
      const cls = a.score < thr ? 'score-low' : 'score-ok';
      return `<div class="photo-thumb" id="th-${a.id}" onclick="toggleSelect(event, '${a.id}')" title="${a.filename} · ${fmtDate(a.date)}">
        <img src="${a.thumb}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg/>'">
        <a class="photo-open" href="${immichUrl}/photos/${a.id}" target="_blank" rel="noopener" onclick="event.stopPropagation()">⤢</a>
        <div class="photo-check">✓</div>
        <div class="score-badge ${cls}">${Math.round(a.score * 100)}%</div>
      </div>`;
    }).join('');
    const refSet = new Set(refsIds), negSet = new Set(negIds);
    d.assets.forEach(a => {
      if (refSet.has(a.id)) document.getElementById('th-' + a.id)?.classList.add('is-ref');
      if (negSet.has(a.id)) document.getElementById('th-' + a.id)?.classList.add('is-neg');
    });
  } catch(e) {
    clearInterval(blPollTimer); blPollTimer = null;
    if (blGeneration !== myGen) return;
    label.textContent = 'Failed to load missed photos';
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-sub">${e.message}</div></div>`;
    toast('Error: ' + e.message, 'error');
  }
}

// ---------------------------------------------------------------------------

function toggleSelect(e, id) {
  const el = document.getElementById('th-' + id); if (!el) return;
  if (el.classList.contains('is-ref')) return;
  if (el.classList.contains('is-neg')) return;
  if (e.shiftKey && lastClickedId && lastClickedId !== id) {
    const thumbs = [...document.querySelectorAll('#photoGrid .photo-thumb')];
    const fromEl = document.getElementById('th-' + lastClickedId);
    const fromIdx = thumbs.indexOf(fromEl), toIdx = thumbs.indexOf(el);
    if (fromIdx !== -1 && toIdx !== -1) {
      const lo = Math.min(fromIdx, toIdx), hi = Math.max(fromIdx, toIdx);
      for (let i = lo; i <= hi; i++) {
        if (thumbs[i].classList.contains('is-ref') || thumbs[i].classList.contains('is-neg')) continue;
        const tid = thumbs[i].id.slice(3);
        selectedIds.add(tid); thumbs[i].classList.add('selected');
      }
    }
  } else {
    if (selectedIds.has(id)) { selectedIds.delete(id); el.classList.remove('selected'); }
    else { selectedIds.add(id); el.classList.add('selected'); }
    lastClickedId = id;
  }
  updateSelUI();
}

function updateSelUI() {
  const n = selectedIds.size;
  document.getElementById('selCount').textContent = n ? `${n} selected` : '';
  document.getElementById('assignBtn').style.display = (n && activePet && !negCandidateMode && !scanLowConfMode) ? '' : 'none';
  document.getElementById('skipBtn').style.display = n ? '' : 'none';
  document.getElementById('addNegBtn').style.display = n ? '' : 'none';
  document.getElementById('scanPetBtns').style.display = (n && scanLowConfMode) ? 'flex' : 'none';
}

async function skipSelected() {
  if (!selectedIds.size) return;
  const ids = [...selectedIds];
  try {
    await api('/api/skipped', { method: 'POST', body: { asset_ids: ids } });
    ids.forEach(id => document.getElementById('th-' + id)?.remove());
    selectedIds.clear(); updateSelUI();
    toast(`Skipped ${ids.length} photo${ids.length !== 1 ? 's' : ''}`, 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Negatives
// ---------------------------------------------------------------------------

function updateNegStatus() {
  const el = document.getElementById('negCount');
  const count = negIds.length;
  if (lastNegTopScore === null) {
    el.textContent = count;
    el.style.color = '';
    return;
  }
  const pct = Math.round(lastNegTopScore * 100);
  if (lastNegTopScore >= 0.1) {
    el.textContent = `${count} · top ${pct}%, add more`;
    el.style.color = 'var(--danger)';
  } else if (lastNegTopScore >= 0.05) {
    el.textContent = `${count} · top ${pct}%`;
    el.style.color = 'var(--text2)';
  } else {
    el.textContent = `${count} · calibrated`;
    el.style.color = 'var(--success)';
  }
}

async function loadNegatives() {
  try {
    const d = await api('/api/negatives');
    negIds = d.assets.map(a => a.id);
    updateNegStatus();
    document.getElementById('clearNegsBtn').style.display = negIds.length ? '' : 'none';
    const grid = document.getElementById('negGrid');
    if (!negIds.length) { grid.innerHTML = ''; return; }
    grid.innerHTML = d.assets.map(a => `
      <div class="ref-thumb">
        <a href="${immichUrl}/photos/${a.id}" target="_blank" rel="noopener" title="Open in Immich">
          <img src="${a.thumb}" loading="lazy" onerror="this.style.opacity=0.2">
        </a>
        <button class="ref-remove" onclick="removeNegative('${a.id}')" title="Remove">✕</button>
      </div>`).join('');
  } catch(e) { console.warn('loadNegatives:', e); }
}

async function addSelectedAsNegatives() {
  if (!selectedIds.size) return;
  try {
    await api('/api/negatives', { method: 'POST', body: { asset_ids: [...selectedIds] } });
    negIds = [...new Set([...negIds, ...selectedIds])];
    document.querySelectorAll('.photo-thumb.selected').forEach(el => { el.classList.remove('selected'); el.classList.add('is-neg'); });
    selectedIds.clear(); lastNegTopScore = null; updateSelUI();
    await loadNegatives();
    toast('Added to "not my pets"', 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function viewNegCandidates() {
  const myGen = ++negGeneration;
  if (negPollTimer) { clearInterval(negPollTimer); negPollTimer = null; }
  negCandidateMode = true; taggedMode = false;
  selectedIds.clear(); lastClickedId = null; updateSelUI();
  const grid = document.getElementById('photoGrid');
  const label = document.getElementById('resultsLabel');
  grid.innerHTML = '<div class="loading" id="negLoadMsg" style="grid-column:1/-1">Loading…</div>';
  label.textContent = 'Finding candidates…';

  negPollTimer = setInterval(async () => {
    if (negGeneration !== myGen) { clearInterval(negPollTimer); negPollTimer = null; return; }
    try {
      const p = await api('/api/suggestions/negatives/progress');
      const el = document.getElementById('negLoadMsg');
      if (!el) return;
      if (p.total > 0) el.textContent = `Loading ${Math.round(p.current / p.total * 100)}%…`;
      else if (p.running) el.textContent = 'Loading…';
    } catch(_) {}
  }, 1000);

  try {
    const d = await api('/api/suggestions/negatives');
    clearInterval(negPollTimer); negPollTimer = null;
    if (negGeneration !== myGen) return;
    lastNegTopScore = d.assets.length > 0 ? (d.assets[0].score ?? null) : 0;
    updateNegStatus();
    label.textContent = `${d.assets.length} candidate${d.assets.length !== 1 ? 's' : ''} for "not my pets"`;
    if (!d.assets.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-icon">🐾</div><div class="empty-title">No candidates found</div><div class="empty-sub">Classifier is well calibrated</div></div>';
      return;
    }
    const thr = d.threshold || 0.8;
    grid.innerHTML = d.assets.map(a => {
      const cls = a.score != null ? (a.score < thr ? 'score-low' : 'score-ok') : '';
      const badge = a.score != null ? `<div class="score-badge ${cls}">${Math.round(a.score * 100)}%</div>` : '';
      return `<div class="photo-thumb" id="th-${a.id}" onclick="toggleSelect(event, '${a.id}')" title="${a.filename} · ${fmtDate(a.date)}">
        <img src="${a.thumb}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg/>'">
        <a class="photo-open" href="${immichUrl}/photos/${a.id}" target="_blank" rel="noopener" onclick="event.stopPropagation()">⤢</a>
        <div class="photo-check">✓</div>
        ${badge}
      </div>`;
    }).join('');
    const negSet = new Set(negIds);
    d.assets.forEach(a => {
      if (negSet.has(a.id)) document.getElementById('th-' + a.id)?.classList.add('is-neg');
    });
  } catch(e) {
    clearInterval(negPollTimer); negPollTimer = null;
    if (negGeneration !== myGen) return;
    label.textContent = 'Failed to load candidates';
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-sub">${e.message}</div></div>`;
    toast('Error: ' + e.message, 'error');
  }
}

async function clearAllRefs() {
  if (!activePet) return;
  if (!confirm(`Remove all reference photos for ${activePet.name} from Pet Tagger? This will not affect Immich.`)) return;
  try {
    await api(`/api/pets/${encodeURIComponent(activePet.name)}/refs`, { method: 'DELETE' });
    refsIds = [];
    await loadRefs(activePet.name);
    await refreshState();
    toast('All refs cleared', 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function clearAllNegatives() {
  if (!confirm(`Remove all "not my pets" photos from Pet Tagger? This will not affect Immich.`)) return;
  try {
    await api('/api/negatives/all', { method: 'DELETE' });
    negIds = []; lastNegTopScore = null;
    await loadNegatives();
    toast('All "not my pets" cleared', 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function removeNegative(id) {
  try {
    await api(`/api/negatives/${id}`, { method: 'DELETE' });
    negIds = negIds.filter(i => i !== id);
    await loadNegatives();
    document.getElementById('th-' + id)?.classList.remove('is-neg');
    toast('Removed from "not my pets"');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Poll status
// ---------------------------------------------------------------------------

function fmtDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-');
  return new Date(+y, m - 1, +d).toLocaleDateString();
}


function relativeTime(iso) {
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}


// ---------------------------------------------------------------------------
// Scan timestamp
// ---------------------------------------------------------------------------

async function loadTimestamp() {
  try {
    const d = await api('/api/timestamp');
    if (d.timestamp) document.getElementById('scanDate').value = d.timestamp.slice(0, 10);
  } catch(e) {}
}

async function loadScanResult() {
  try { showScanResult(await api('/api/scan/result')); } catch(_) {}
}

function showScanResult(r) {
  const el = document.getElementById('scanResult');
  if (!r || r.status === 'none') { el.style.display = 'none'; return; }
  el.className = 'scan-result';
  el.style.display = '';
  const stat = (label, val, cls) => `<div class="poll-stat"><span class="poll-stat-label">${label}</span><span class="poll-stat-val ${val > 0 ? cls : ''}">${val}</span></div>`;
  if (r.status === 'running') {
    const dateStr = r.current_date ? new Date(r.current_date + 'T00:00:00').toLocaleDateString() : '';
    const c = r.counts || {};
    el.innerHTML = '<div class="scan-result-header">Scanning…</div>' +
      (dateStr ? `<div style="font-size:11px;color:var(--text3);margin-top:4px;">${dateStr}</div>` : '') +
      '<div class="poll-stats" style="margin-top:6px;">' +
      stat('Tagged', c.added || 0, 'nonzero-good') +
      stat('Low conf.', c.low_confidence || 0, 'nonzero-warn') +
      stat('Other', c.unknown || 0, '') +
      stat('Already tagged', c.already_tagged || 0, '') +
      (c.failed > 0 ? stat('Failed', c.failed, 'nonzero-bad') : '') +
      '</div>';
    return;
  }
  if (r.status === 'error') {
    el.innerHTML = `<div class="scan-result-header">Scan failed</div><div style="font-size:11px;color:var(--danger);margin-top:4px;">${r.error || ''}</div>`;
    return;
  }
  if (r.counts) {
    const c = r.counts;
    el.innerHTML = '<div class="scan-result-header">Scan result</div>' +
      '<div class="poll-stats" style="margin-top:6px;">' +
      stat('Tagged', c.added, 'nonzero-good') +
      stat('Low conf.', c.low_confidence, 'nonzero-warn') +
      stat('Other', c.unknown, '') +
      stat('Out of range', c.out_of_range, '') +
      stat('Already tagged', c.already_tagged, '') +
      (c.failed > 0 ? stat('Failed', c.failed, 'nonzero-bad') : '') +
      (c.no_thumb > 0 ? stat('No thumb', c.no_thumb, 'nonzero-warn') : '') +
      '</div>' +
      (c.low_confidence > 0 ? `<button class="btn" style="font-size:11px;margin-top:8px;width:100%;" onclick="viewScanLowConf()">Review ${c.low_confidence} low confidence</button>` : '');
  }
}

async function viewScanLowConf() {
  scanLowConfMode = true;
  taggedMode = false; negCandidateMode = false; borderlineMode = false;
  selectedIds.clear(); lastClickedId = null;
  const grid = document.getElementById('photoGrid');
  const label = document.getElementById('resultsLabel');
  grid.innerHTML = '<div class="loading" style="grid-column:1/-1">Loading low confidence results…</div>';
  label.textContent = 'Loading…';
  const scanPetBtns = document.getElementById('scanPetBtns');
  scanPetBtns.innerHTML = pets.map(p => `<button class="btn btn-primary">${p.name}</button>`).join('');
  [...scanPetBtns.children].forEach((btn, i) => { btn.onclick = () => scanAssignSelected(pets[i].name); });
  updateSelUI();
  try {
    const d = await api('/api/scan/low-confidence');
    if (!d.assets.length) {
      label.textContent = 'No low confidence results';
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1; height:200px;"><div class="empty-sub">All results were confident or unknown</div></div>';
      return;
    }
    label.textContent = `${d.assets.length} low confidence result${d.assets.length !== 1 ? 's' : ''}`;
    const thr = d.threshold ?? 0.8;
    const negSet = new Set(negIds);
    grid.innerHTML = d.assets.map(a => {
      const cls = a.score < thr ? 'score-low' : 'score-ok';
      return `<div class="photo-thumb" id="th-${a.id}" onclick="toggleSelect(event, '${a.id}')" title="${fmtDate(a.date)} · ${Math.round(a.score * 100)}% ${a.pet_name}">
        <img src="${a.thumb}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg/>'">
        <a class="photo-open" href="${immichUrl}/photos/${a.id}" target="_blank" rel="noopener" onclick="event.stopPropagation()">⤢</a>
        <div class="photo-check">✓</div>
        <div class="score-badge ${cls}">${Math.round(a.score * 100)}%</div>
      </div>`;
    }).join('');
    d.assets.forEach(a => { if (negSet.has(a.id)) document.getElementById('th-' + a.id)?.classList.add('is-neg'); });
  } catch(e) {
    label.textContent = 'Failed to load';
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1; height:200px;"><div class="empty-sub">${e.message}</div></div>`;
  }
}

async function scanAssignSelected(petName) {
  if (!selectedIds.size) return;
  const ids = [...selectedIds];
  try {
    const existing = await api(`/api/pets/${encodeURIComponent(petName)}/assets`);
    const merged = [...new Set([...existing.assets.map(a => a.id), ...ids])];
    await api(`/api/pets/${encodeURIComponent(petName)}/assets`, { method: 'POST', body: { asset_ids: merged } });
    ids.forEach(id => { const el = document.getElementById('th-' + id); if (el) { el.classList.remove('selected'); el.classList.add('is-ref'); } });
    selectedIds.clear(); updateSelUI();
    await refreshState();
    toast(`Added ${ids.length} to ${petName}`, 'success');
  } catch(e) { toast(e.message, 'error'); }
}

async function applyTimestamp() {
  const val = document.getElementById('scanDate').value;
  if (!val) { toast('Pick a date first', 'error'); return; }
  try {
    await api('/api/timestamp', { method: 'POST', body: { date: val } });
    await api('/api/scan', { method: 'POST' });
    showScanResult({ status: 'running' });
    const iv = setInterval(async () => {
      try {
        const r = await api('/api/scan/result');
        showScanResult(r);
        if (r.status !== 'running') { clearInterval(iv); }
      } catch(_) {}
    }, 2000);
  } catch(e) {
    toast(e.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

function modalError(id, msg) { document.getElementById(id).textContent = msg; }
function clearModalError(id) { document.getElementById(id).textContent = ''; }

function openAddPet() {
  document.getElementById('petName').value = ''; document.getElementById('petDescription').value = ''; document.getElementById('petSince').value = ''; document.getElementById('petUntil').value = '';
  document.getElementById('addPetModal').classList.add('open');
  setTimeout(() => document.getElementById('petName').focus(), 100);
}
function closeModal() { document.getElementById('addPetModal').classList.remove('open'); clearModalError('addPetError'); }

async function submitAddPet() {
  clearModalError('addPetError');
  const name = document.getElementById('petName').value.trim();
  if (!name) { modalError('addPetError', 'Name cannot be empty'); return; }
  if (/[\/\\.]/.test(name)) { modalError('addPetError', 'Name cannot contain / \\ or .'); return; }
  const description = document.getElementById('petDescription').value.trim();
  if (!description) { modalError('addPetError', 'Description is required'); return; }
  const sinceRaw = document.getElementById('petSince').value;
  const untilRaw = document.getElementById('petUntil').value;
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  if (sinceRaw && !dateRe.test(sinceRaw)) { modalError('addPetError', 'Invalid "since" date'); return; }
  if (untilRaw && !dateRe.test(untilRaw)) { modalError('addPetError', 'Invalid "until" date'); return; }
  if (sinceRaw && untilRaw && sinceRaw > untilRaw) { modalError('addPetError', '"Since" must be before "until"'); return; }
  try {
    await api('/api/pets', { method: 'POST', body: { name, description, since: sinceRaw || null, until: untilRaw || null } });
    closeModal();
    await loadPets(true);
    await selectPet(name);
    toast(`Created ${name}`, 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

let _petToEdit = null;

function openEditPet(name) {
  _petToEdit = name;
  const p = pets.find(p => p.name === name);
  document.getElementById('editPetName').value = p.name;
  document.getElementById('editPetDescription').value = p.description || '';
  document.getElementById('editPetSince').value = p.since || '';
  document.getElementById('editPetUntil').value = p.until || '';
  document.getElementById('editPetModal').classList.add('open');
  setTimeout(() => document.getElementById('editPetName').focus(), 100);
}
function closeEditModal() { document.getElementById('editPetModal').classList.remove('open'); _petToEdit = null; }

async function submitEditPet() {
  if (!_petToEdit) return;
  clearModalError('editPetError');
  const name = document.getElementById('editPetName').value.trim();
  if (!name) { modalError('editPetError', 'Name cannot be empty'); return; }
  if (/[\/\\.]/.test(name)) { modalError('editPetError', 'Name cannot contain / \\ or .'); return; }
  const description = document.getElementById('editPetDescription').value.trim();
  if (!description) { modalError('editPetError', 'Description is required'); return; }
  const sinceRaw = document.getElementById('editPetSince').value;
  const untilRaw = document.getElementById('editPetUntil').value;
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  if (sinceRaw && !dateRe.test(sinceRaw)) { modalError('editPetError', 'Invalid "since" date'); return; }
  if (untilRaw && !dateRe.test(untilRaw)) { modalError('editPetError', 'Invalid "until" date'); return; }
  if (sinceRaw && untilRaw && sinceRaw > untilRaw) { modalError('editPetError', '"Since" must be before "until"'); return; }
  try {
    await api(`/api/pets/${encodeURIComponent(_petToEdit)}`, { method: 'PATCH', body: { name, description, since: sinceRaw || null, until: untilRaw || null } });
    closeEditModal();
    const prevName = activePet?.name;
    activePet = null; clearSearch();
    await loadPets(true);
    const selectName = prevName === _petToEdit ? name : (prevName || pets[0]?.name);
    if (selectName) await selectPet(selectName);
    toast('Saved', 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

let _petToDelete = null;

function openDeletePet(name) {
  _petToDelete = name;
  document.getElementById('deleteWarningText').textContent =
    `"Delete from Immich too" removes the person and untags all tagged photos in Immich permanently. Your photos are not deleted.`;
  document.getElementById('deleteLocalOnlyText').textContent =
    `"Remove from Pet Tagger only" keeps ${name} in Immich with all tagged photos intact, but stops auto-tagging new photos. Your photos are not deleted. You can re-import it later.`;
  document.getElementById('deletePetModal').classList.add('open');
}
function closeDeleteModal() { document.getElementById('deletePetModal').classList.remove('open'); _petToDelete = null; }

async function confirmDeletePet(localOnly) {
  if (!_petToDelete) return;
  const name = _petToDelete;
  closeDeleteModal();
  try {
    const url = `/api/pets/${encodeURIComponent(name)}` + (localOnly ? '?local_only=true' : '');
    await api(url, { method: 'DELETE' });
    if (activePet?.name === name) {
      activePet = null;
      document.getElementById('refsTitle').textContent = 'No pet selected';
      document.getElementById('refsGrid').innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-sub">Select a pet</div></div>';
    }
    await refreshState();
    toast(localOnly ? `Removed ${name} from Pet Tagger` : `Deleted ${name}. Immich will clean up faces in the background.`, 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Import from Immich
// ---------------------------------------------------------------------------

let _allImportPeople = [], _importSelectedPerson = null;

async function openImportPet() {
  _importSelectedPerson = null;
  _allImportPeople = [];
  document.getElementById('importSearch').value = '';
  document.getElementById('importPeopleGrid').innerHTML = '<div class="loading">Loading…</div>';
  clearModalError('importPickerError');
  document.getElementById('importPickerModal').classList.add('open');
  try {
    const d = await api('/api/immich-people');
    _allImportPeople = d.people || [];
    renderImportPeople(_allImportPeople);
  } catch(e) {
    document.getElementById('importPeopleGrid').innerHTML = `<div class="empty" style="grid-column:1/-1;padding:24px;"><div class="empty-sub">${e.message}</div></div>`;
  }
}

function renderImportPeople(people) {
  const grid = document.getElementById('importPeopleGrid');
  if (!people.length) {
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:24px;"><div class="empty-sub">No people found in Immich</div></div>';
    return;
  }
  const petPersonIds = new Set(pets.map(p => p.person_id).filter(Boolean));
  grid.innerHTML = people.map(p => `
    <div class="person-card${petPersonIds.has(p.id) ? ' already-added' : ''}" data-pid="${p.id}" onclick="handlePersonCardClick(this)">
      <img class="person-thumb" src="/api/person-thumb/${p.id}" onerror="this.style.opacity=0.2" loading="lazy" alt="">
      <span class="person-name-label">${p.name || '—'}</span>
    </div>`).join('');
}

function filterImportPeople() {
  const q = document.getElementById('importSearch').value.toLowerCase();
  renderImportPeople(q ? _allImportPeople.filter(p => (p.name || '').toLowerCase().includes(q)) : _allImportPeople);
}

function handlePersonCardClick(el) {
  const id = el.dataset.pid;
  const person = _allImportPeople.find(p => p.id === id);
  if (!person) return;
  _importSelectedPerson = person;
  document.getElementById('importPickerModal').classList.remove('open');
  document.getElementById('importPetName').value = person.name || '';
  document.getElementById('importPetDescription').value = '';
  document.getElementById('importPetSince').value = '';
  document.getElementById('importPetUntil').value = '';
  clearModalError('importDetailError');
  document.getElementById('importDetailModal').classList.add('open');
  setTimeout(() => document.getElementById('importPetDescription').focus(), 100);
}

function closeImportPicker() { document.getElementById('importPickerModal').classList.remove('open'); }
function closeImportDetail() { document.getElementById('importDetailModal').classList.remove('open'); _importSelectedPerson = null; }
function backToImportPicker() { document.getElementById('importDetailModal').classList.remove('open'); document.getElementById('importPickerModal').classList.add('open'); }

async function submitImportPet() {
  if (!_importSelectedPerson) return;
  clearModalError('importDetailError');
  const description = document.getElementById('importPetDescription').value.trim();
  if (!description) { modalError('importDetailError', 'Description is required'); return; }
  const sinceRaw = document.getElementById('importPetSince').value;
  const untilRaw = document.getElementById('importPetUntil').value;
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  if (sinceRaw && !dateRe.test(sinceRaw)) { modalError('importDetailError', 'Invalid "since" date'); return; }
  if (untilRaw && !dateRe.test(untilRaw)) { modalError('importDetailError', 'Invalid "until" date'); return; }
  if (sinceRaw && untilRaw && sinceRaw > untilRaw) { modalError('importDetailError', '"Since" must be before "until"'); return; }
  try {
    const result = await api('/api/pets/import', { method: 'POST', body: {
      person_id: _importSelectedPerson.id,
      name: _importSelectedPerson.name,
      description,
      since: sinceRaw || null,
      until: untilRaw || null,
    }});
    closeImportDetail();
    await refreshState();
    await selectPet(result.name);
    toast(`Imported ${result.name} with ${result.ref_count} refs`, 'success');
  } catch(e) { modalError('importDetailError', e.message); }
}

// ---------------------------------------------------------------------------
// Modal backdrop dismissal
// ---------------------------------------------------------------------------

document.getElementById('addPetModal').addEventListener('click', function(e) { if (e.target === this) closeModal(); });
document.getElementById('editPetModal').addEventListener('click', function(e) { if (e.target === this) closeEditModal(); });
document.getElementById('deletePetModal').addEventListener('click', function(e) { if (e.target === this) closeDeleteModal(); });
document.getElementById('importPickerModal').addEventListener('click', function(e) { if (e.target === this) closeImportPicker(); });
document.getElementById('importDetailModal').addEventListener('click', function(e) { if (e.target === this) closeImportDetail(); });

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async () => {
  await refreshState();
  loadTimestamp();
  loadScanResult();
})();
