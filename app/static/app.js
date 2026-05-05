let pets = [], activePet = null, selectedIds = new Set(), refsIds = [], negIds = [], immichUrl = 'http://localhost:2283', taggedMode = false, lastClickedId = null;

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
    document.getElementById('suggestSection').style.display = 'none';
    document.getElementById('taggedBtn').style.display = 'none';
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
  document.getElementById('photoGrid').innerHTML = '<div class="empty" style="grid-column:1/-1; height:300px;"><div class="empty-icon">🐾</div><div class="empty-title">Find photos</div><div class="empty-sub">Click Similar to find photos matching this pet</div></div>';
  selectedIds.clear(); lastClickedId = null; updateSelUI();
}

async function selectPet(name) {
  if (activePet?.name === name) return;
  if (selectedIds.size > 0) {
    const ok = confirm(`You have ${selectedIds.size} selected photo${selectedIds.size !== 1 ? 's' : ''} not yet assigned. Switch anyway?`);
    if (!ok) return;
  }
  taggedMode = false;
  activePet = pets.find(p => p.name === name);
  clearSearch(); renderSidebar();
  document.getElementById('refsTitle').textContent = name;
  document.getElementById('suggestSection').style.display = '';
  document.getElementById('taggedBtn').style.display = '';
  document.getElementById('taggedBtn').textContent = 'Tagged';
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
  if (!assets.length) { grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:160px;"><div class="empty-sub">No references yet.<br>Search and add photos.</div></div>'; return; }
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

async function viewSuggestions() {
  if (!activePet) return;
  if (!activePet.description) { toast('Edit this pet and add a description to use this feature', 'error'); return; }
  taggedMode = false;
  selectedIds.clear(); lastClickedId = null; updateSelUI();
  const grid = document.getElementById('photoGrid');
  const label = document.getElementById('resultsLabel');
  grid.innerHTML = '<div class="loading" style="grid-column:1/-1">Finding similar photos… this may take a moment</div>';
  label.textContent = 'Finding similar photos…';
  try {
    const d = await api(`/api/pets/${encodeURIComponent(activePet.name)}/suggestions`);
    label.textContent = `${d.assets.length} photo${d.assets.length !== 1 ? 's' : ''} similar to ${activePet.name}'s refs`;
    if (!d.assets.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-icon">🐾</div><div class="empty-title">No suggestions found</div><div class="empty-sub">Add more refs or broaden the date range</div></div>';
      return;
    }
    grid.innerHTML = d.assets.map(a => `
      <div class="photo-thumb" id="th-${a.id}" onclick="toggleSelect(event, '${a.id}')" title="${a.filename} · ${a.date}">
        <img src="${a.thumb}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg/>'">
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

// ---------------------------------------------------------------------------
// Tagged photos
// ---------------------------------------------------------------------------

async function viewTagged() {
  if (!activePet) return;
  taggedMode = true;
  selectedIds.clear(); lastClickedId = null;
  const grid = document.getElementById('photoGrid');
  const label = document.getElementById('resultsLabel');
  grid.innerHTML = '<div class="loading" style="grid-column:1/-1">Loading tagged photos...</div>';
  label.textContent = 'Loading...';
  updateSelUI();
  try {
    const d = await api(`/api/pets/${encodeURIComponent(activePet.name)}/tagged`);
    label.textContent = `${d.count} photo${d.count !== 1 ? 's' : ''} tagged as ${activePet.name} in Immich`;
    document.getElementById('taggedBtn').textContent = `Tagged (${d.count})`;
    if (!d.assets.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-icon">🐾</div><div class="empty-title">No tagged photos yet</div></div>';
      return;
    }
    grid.innerHTML = d.assets.map(a => `
      <div class="photo-thumb" id="th-${a.id}" onclick="toggleSelect(event, '${a.id}')" title="${a.filename || a.id} · ${a.date}">
        <img src="${a.thumb}" loading="lazy" onerror="this.src='data:image/svg+xml,<svg/>'">
        <div class="photo-check">✓</div>
      </div>`).join('');
  } catch(e) {
    label.textContent = 'Failed to load';
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="empty-sub">${e.message}</div></div>`;
  }
}

function exitTaggedMode() {
  taggedMode = false;
  selectedIds.clear();
  updateSelUI();
  document.getElementById('taggedBtn').textContent = 'Tagged';
  clearSearch();
}

async function rejectSelected() {
  if (!activePet || !selectedIds.size) return;
  const ids = [...selectedIds];
  try {
    await api(`/api/pets/${encodeURIComponent(activePet.name)}/reject`, { method: 'POST', body: { asset_ids: ids } });
    ids.forEach(id => document.getElementById('th-' + id)?.remove());
    selectedIds.clear(); updateSelUI();
    await loadNegatives();
    const remaining = document.querySelectorAll('#photoGrid .photo-thumb').length;
    document.getElementById('resultsLabel').textContent = `${remaining} photo${remaining !== 1 ? 's' : ''} tagged as ${activePet.name} in Immich`;
    document.getElementById('taggedBtn').textContent = `Tagged (${remaining})`;
    toast(`Removed ${ids.length} tag${ids.length !== 1 ? 's' : ''} and added to "not my pets"`, 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

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
  document.getElementById('assignBtn').style.display = (n && activePet && !taggedMode) ? '' : 'none';
  document.getElementById('addNegBtn').style.display = (n && !taggedMode) ? '' : 'none';
  document.getElementById('rejectBtn').style.display = (n && taggedMode) ? '' : 'none';
}

// ---------------------------------------------------------------------------
// Negatives
// ---------------------------------------------------------------------------

function updateNegStatus() {
  const totalRefs = pets.reduce((s, p) => s + p.ref_count, 0);
  const el = document.getElementById('negCount');
  const count = negIds.length;
  if (totalRefs === 0) { el.textContent = count; el.style.color = ''; return; }
  const lo = totalRefs * 2, hi = totalRefs * 3;
  if (count < lo) {
    el.textContent = `${count} / ${lo}-${hi} needed`;
    el.style.color = 'var(--danger)';
  } else {
    el.textContent = count;
    el.style.color = 'var(--success)';
  }
}

async function loadNegatives() {
  try {
    const d = await api('/api/negatives');
    negIds = d.assets.map(a => a.id);
    updateNegStatus();
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
    selectedIds.clear(); updateSelUI();
    await loadNegatives();
    toast('Added to "not my pets"', 'success');
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

function relativeTime(iso) {
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

async function loadPollStatus() {
  try {
    const d = await api('/api/poll-status');
    const badge = document.getElementById('pollBadge');
    const timeEl = document.getElementById('pollTime');
    const statsEl = document.getElementById('pollStats');
    badge.className = `poll-badge ${d.status}`;
    badge.textContent = d.status === 'running' ? 'Scanning...' : d.status === 'error' ? 'Error' : d.status === 'never' ? 'Never run' : 'Idle';
    if (d.ran_at) timeEl.textContent = relativeTime(d.ran_at);
    else if (d.started_at) timeEl.textContent = `Started ${relativeTime(d.started_at)}`;
    else timeEl.textContent = '';
    if (d.counts) {
      const c = d.counts;
      const stat = (label, val, cls) => `<div class="poll-stat"><span class="poll-stat-label">${label}</span><span class="poll-stat-val ${val > 0 ? cls : ''}">${val}</span></div>`;
      statsEl.innerHTML =
        stat('Tagged', c.added, 'nonzero-good') +
        stat('Low conf.', c.low_confidence, 'nonzero-warn') +
        stat('Unknown', c.unknown, '') +
        stat('Out of range', c.out_of_range, '') +
        stat('Already tagged', c.already_tagged, '') +
        (c.failed > 0 ? stat('Failed', c.failed, 'nonzero-bad') : '') +
        (c.no_thumb > 0 ? stat('No thumb', c.no_thumb, 'nonzero-warn') : '');
    } else {
      statsEl.innerHTML = '';
    }
    if (d.error) timeEl.textContent += (timeEl.textContent ? '. ' : '') + d.error;
  } catch(e) {}
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

async function applyTimestamp() {
  const val = document.getElementById('scanDate').value;
  if (!val) { toast('Pick a date first', 'error'); return; }
  try {
    await api('/api/timestamp', { method: 'POST', body: { date: val } });
    toast('Scan date updated. Takes effect on next poll.', 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
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
  const sinceEl = document.getElementById('petSince');
  const untilEl = document.getElementById('petUntil');
  if (sinceEl.value === '' && sinceEl.validity && !sinceEl.validity.valid && sinceEl.validity.badInput) { modalError('addPetError', 'Incomplete "since" date'); return; }
  if (untilEl.value === '' && untilEl.validity && !untilEl.validity.valid && untilEl.validity.badInput) { modalError('addPetError', 'Incomplete "until" date'); return; }
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  if (sinceRaw && !dateRe.test(sinceRaw)) { modalError('addPetError', 'Invalid "since" date. Use YYYY-MM-DD'); return; }
  if (untilRaw && !dateRe.test(untilRaw)) { modalError('addPetError', 'Invalid "until" date. Use YYYY-MM-DD'); return; }
  if (sinceRaw && untilRaw && sinceRaw > untilRaw) { modalError('addPetError', '"Since" must be before "until"'); return; }
  try {
    await api('/api/pets', { method: 'POST', body: { name, description, since: sinceRaw || null, until: untilRaw || null } });
    closeModal();
    await refreshState();
    toast(`Created ${name}`, 'success');
    selectPet(name);
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
  const sinceEl = document.getElementById('editPetSince');
  const untilEl = document.getElementById('editPetUntil');
  if (sinceEl.value === '' && sinceEl.validity && !sinceEl.validity.valid && sinceEl.validity.badInput) { modalError('editPetError', 'Incomplete "since" date'); return; }
  if (untilEl.value === '' && untilEl.validity && !untilEl.validity.valid && untilEl.validity.badInput) { modalError('editPetError', 'Incomplete "until" date'); return; }
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  if (sinceRaw && !dateRe.test(sinceRaw)) { modalError('editPetError', 'Invalid "since" date. Use YYYY-MM-DD'); return; }
  if (untilRaw && !dateRe.test(untilRaw)) { modalError('editPetError', 'Invalid "until" date. Use YYYY-MM-DD'); return; }
  if (sinceRaw && untilRaw && sinceRaw > untilRaw) { modalError('editPetError', '"Since" must be before "until"'); return; }
  try {
    await api(`/api/pets/${encodeURIComponent(_petToEdit)}`, { method: 'PATCH', body: { name, description, since: sinceRaw || null, until: untilRaw || null } });
    closeEditModal();
    const wasActive = activePet?.name === _petToEdit;
    activePet = null; clearSearch();
    await loadPets();
    if (wasActive) await selectPet(name);
    toast('Saved', 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

let _petToDelete = null;

function openDeletePet(name) {
  _petToDelete = name;
  const p = pets.find(p => p.name === name);
  const refs = p ? p.ref_count : 0;
  document.getElementById('deleteWarningText').textContent =
    `"${name}" will be permanently removed from Immich and all ${refs} reference photo${refs !== 1 ? 's' : ''} will be untagged.`;
  document.getElementById('deletePetModal').classList.add('open');
}
function closeDeleteModal() { document.getElementById('deletePetModal').classList.remove('open'); _petToDelete = null; }

async function confirmDeletePet() {
  if (!_petToDelete) return;
  const name = _petToDelete;
  closeDeleteModal();
  try {
    await api(`/api/pets/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (activePet?.name === name) {
      activePet = null;
      document.getElementById('refsTitle').textContent = 'No pet selected';
      document.getElementById('refsGrid').innerHTML = '<div class="empty" style="grid-column:1/-1;height:200px;"><div class="empty-sub">Select a pet</div></div>';
    }
    await refreshState();
    toast(`Deleted ${name}`, 'success');
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

// ---------------------------------------------------------------------------
// Modal backdrop dismissal
// ---------------------------------------------------------------------------

document.getElementById('addPetModal').addEventListener('click', function(e) { if (e.target === this) closeModal(); });
document.getElementById('editPetModal').addEventListener('click', function(e) { if (e.target === this) closeEditModal(); });
document.getElementById('deletePetModal').addEventListener('click', function(e) { if (e.target === this) closeDeleteModal(); });

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async () => {
  await refreshState();
  loadTimestamp();
  loadPollStatus();
  setInterval(loadPollStatus, 30000);
})();
