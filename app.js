// SVG 是用坐标画图：每款装饰共用画布、挂绳和挂环。
const drawings = {
  candy: {name:'拐杖糖', shape:'<path d="M23 64V34a11 11 0 0 1 22 0v7" fill="none" stroke="#fff4e3" stroke-width="10" stroke-linecap="round"/><path d="m19 55 8 5m-8-18 8 5m-7-17 8 5m2-11 5 7m6-3-1 9" fill="none" stroke="#c64951" stroke-width="5"/><path d="m21 46-10-5 1 12 10-4 10 7 1-12Z" fill="#487859" stroke="#9ab589"/>'},
  stocking: {name:'圣诞袜', shape:'<path d="M24 28h23l-4 24q-1 8-9 12L17 70Q5 71 6 61q0-6 9-9l7-3Z" fill="#b83e4b" stroke="#e99a92"/><path d="M21 24h29v12H21Z" fill="#fff0db" stroke="#dfd4bd"/><path d="M7 60q6 0 11 8M38 49l6 2" fill="none" stroke="#f4d6b9" stroke-width="5"/><path d="m31 41 2 4 5 1-4 3 1 5-4-3-4 3 1-5-4-3 5-1Z" fill="#f0cb82"/>'},
  moon: {name:'16nights · 月夜', shape:'<path d="M37 23A23 23 0 1 0 49 59A21 21 0 0 1 37 23Z" fill="#bddde9" stroke="#e7f6ff" stroke-width="1.5"/><path d="m45 24 3 7 8 1-6 5 2 8-7-4-6 4 1-8-6-5 8-1Z" fill="#f0cb82"/><path d="M19 39q-8 14 5 22" fill="none" stroke="#7faac5" stroke-width="2"/>'},
  star: {name:'金色星星', shape:'<path d="M30 20 37 34 53 36 41 48 44 64 30 56 16 64 19 48 7 36 23 34Z" fill="#f0cb82" stroke="#fff0bd" stroke-width="1.5"/><path d="m30 26 0 24-13 7 4-12Z" fill="#d99b48"/>'},
  ball: {name:'红色玻璃球', shape:'<path d="M24 22h12v7H24z" fill="#f0cb82"/><circle cx="30" cy="47" r="21" fill="#b9404d" stroke="#f0cb82"/><path d="M18 38q-7 11 0 17" fill="none" stroke="#ffb5ad" stroke-width="4" stroke-linecap="round"/><path d="M13 55q17 9 34 0" fill="none" stroke="#f0cb82"/>'},
  bell: {name:'金色铃铛', shape:'<circle cx="30" cy="61" r="5" fill="#be7e36"/><path d="M12 57q7-7 7-20a11 11 0 0 1 22 0q0 13 7 20Z" fill="#e5b55e" stroke="#ffe5a4" stroke-width="1.5"/><path d="M24 36v12" stroke="#fff0bd" stroke-width="3" stroke-linecap="round"/><path d="M12 57h36" stroke="#fff0bd" stroke-width="3"/>'}
};
// 页面只负责显示和收集输入，真正的保存与规则检查交给服务器。
const $ = id => document.getElementById(id);
const hint = text => { document.querySelector('.hint').lastChild.textContent = text; };
const picker = $('ornament-dialog');
const messageDialog = $('message-dialog');
const createDialog = $('create-dialog');
const successDialog = $('success-dialog');
const treeId = location.pathname.match(/^\/tree\/([A-Za-z0-9_-]{16})$/)?.[1];
let ready = false, hasGifted = false, sending = false;
let selectedOrnament = null, pendingMessage = null;
let isOwner = false, viewingPoint = null;
const messages = new Map();
const pointButtons = [];
const rows = [[23,47,53,2],[30,44,56,3],[39,40,60,4],[48,35,65,5],[58,31,69,5],[68,26,74,6],[78,25,75,6],[88,18,82,7]];
// 挂点编号不变，只调整坐标，已有留言继续留在原挂点。
const points = rows.flatMap(([y,left,right,count],row) => Array.from({length:count},(_,i) => {
  const base=left+(right-left)*i/(count-1);
  return [base + Math.sin(row*2.4+i*1.9)*1.6, y+Math.sin(i*2.3+row*1.7)*2.6];
}));

async function api(path, data) {
  let response;
  try {
    response = await fetch(path, data === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
    });
  } catch { throw new Error('连接失败，请检查网络后重试；填写的内容仍然保留着'); }
  let result;
  try { result = await response.json(); }
  catch {
    throw new Error(response.status === 429 ? '来访有点频繁，请稍等一会儿再试' : response.status === 413 ? '留言太长了，请缩短后再试' : '网站暂时繁忙，请稍后再试；填写的内容仍保留着');
  }
  if (!response.ok) {
    const error = new Error(result.message || '请求失败，请稍后再试');
    error.code = result.code;
    throw error;
  }
  return result;
}
function ornamentSVG(key) {
  // 只把自己绘制的固定图形作为 HTML；访客留言始终用 textContent。
  return `<svg viewBox="0 0 60 76" aria-hidden="true"><path d="M30 0v14" stroke="#e3c589"/><circle cx="30" cy="18" r="4" fill="none" stroke="#e3c589"/>${drawings[key].shape}</svg>`;
}
// 赠送者文字单独创建，避免把访客输入当成 HTML。
function paintGift(button, entry) {
  button.innerHTML = ornamentSVG(entry.ornament);
  const name = document.createElement('span');
  name.className = 'gift-sender';
  name.textContent = entry.name;
  name.setAttribute('aria-hidden','true');
  button.append(name);
  button.title = entry.name;
}
function updateAction() {
  $('decorate-button').hidden = !treeId;
  $('create-tree').className = treeId ? 'dialog-close' : 'decorate-button';
  $('create-tree').textContent = treeId ? '我也种一棵' : '种下我的树';
  $('decorate-button').disabled = !ready || sending;
  $('decorate-button').textContent = !treeId ? '种下我的圣诞树' : hasGifted ? '心意已送达' : '送一枚装饰';
}
function cancelPlacement() {
  stage.classList.remove('choosing-point');
  $('cancel-placement').hidden = true;
  selectedOrnament = null; pendingMessage = null;
}
function renderTree(data) {
  messages.clear();
  for (const entry of data.ornaments) messages.set(entry.point,entry);
  hasGifted = data.hasGifted;
  isOwner = data.isOwner;
  document.querySelector('[data-exclusive]').hidden = !data.isDeveloper;
  const owner = document.createElement('em');
  owner.textContent = data.name;
  $('tree-title').replaceChildren(owner, document.createTextNode(' 的圣诞树'));
  document.querySelector('.lede').textContent = '每一枚装饰，都有一句想对你说的话。';
  document.title = `${data.name}的圣诞树`;
  document.querySelector('.tree').setAttribute('aria-label',`${data.name}的圣诞树，已有 ${messages.size} 枚装饰`);
  pointButtons.forEach((button,index) => {
    const entry = messages.get(index);
    button.classList.toggle('occupied', Boolean(entry));
    if (entry) paintGift(button,entry);
    else { button.replaceChildren(); button.removeAttribute('title'); }
    button.setAttribute('aria-label',entry ? `查看${entry.name}的留言` : `选择第 ${index+1} 个挂点`);
  });
  ready = true; updateAction();
}
async function loadTree() {
  const data = await api(`/api/trees/${treeId}`);
  renderTree(data);
}
for (const [key,item] of Object.entries(drawings)) {
  const button = document.createElement('button');
  button.className = 'ornament-option';
  if (key === 'moon') { button.dataset.exclusive = 'true'; button.hidden = true; }
  button.innerHTML = ornamentSVG(key);
  const label = document.createElement('span'); label.textContent = item.name; button.append(label);
  button.addEventListener('click', () => {
    selectedOrnament = key; picker.close(); messageDialog.showModal();
  });
  $('ornament-options').append(button);
}
points.forEach(([x,y],index) => {
  const button = document.createElement('button');
  button.className = 'hang-point';
  button.style.left = `${50+(x-50)*1.5}%`; button.style.top = `${y}%`;
  button.setAttribute('aria-label',`选择第 ${index+1} 个挂点`);
  button.addEventListener('click', async () => {
    if (sending) return;
    if (messages.has(index)) {
      const entry = messages.get(index);
      viewingPoint = index;
      $('remove-message').hidden = !isOwner;
      $('remove-confirm').hidden = true;
      $('remove-error').textContent = '';
      $('read-title').textContent = `来自 ${entry.name}`;
      $('message-content').textContent = entry.text;
      $('read-dialog').showModal(); return;
    }
    if (!pendingMessage || !selectedOrnament || hasGifted) return;
    sending = true; updateAction(); hint('正在挂上你的心意…');
    let committed = false;
    try {
      const entry = {...pendingMessage, ornament:selectedOrnament, point:index};
      await api(`/api/trees/${treeId}/ornaments`,entry);
      committed = true;
      messages.set(index,entry); hasGifted = true;
      button.classList.add('occupied'); paintGift(button,entry);
      button.setAttribute('aria-label',`查看${entry.name}的留言`);
      cancelPlacement(); $('message-form').reset();
      hint('已经挂好了，点开装饰就能读到留言');
      successDialog.showModal();
      try { await loadTree(); } catch { /* 保存已成功，保留当前显示，稍后同步。 */ }
    } catch (error) {
      if (error.code === 'POINT_TAKEN' || error.code === 'ALREADY_GIFTED') {
        try { await loadTree(); } catch { /* 保留原输入供重试。 */ }
      }
      if (error.code === 'ALREADY_GIFTED') { hasGifted = true; cancelPlacement(); }
      hint(error.message);
    } finally {
      sending = false; updateAction();
      if (committed) hasGifted = true;
    }
  });
  pointButtons.push(button); stage.append(button);
});
$('decorate-button').addEventListener('click', () => {
  if (!treeId) { createDialog.showModal(); return; }
  if (hasGifted) { hint('你的心意已在树上，点开装饰看看吧'); return; }
  if (messages.size >= points.length) { hint('这棵树已经挂满了心意'); return; }
  cancelPlacement(); picker.showModal();
});
$('message-form').addEventListener('submit', event => {
  event.preventDefault();
  const name = $('sender-name').value.trim(), text = $('message-text').value.trim();
  if (!name || !text) { hint('称呼和留言不能只有空格'); return; }
  pendingMessage = {name,text};
  messageDialog.close(); stage.classList.add('choosing-point');
  $('cancel-placement').hidden = false;
  hint('轻点树上的圆点，为心意选个位置');
  pointButtons.find(button => !button.classList.contains('occupied'))?.focus();
});
$('cancel-placement').addEventListener('click', () => { if (!sending) {cancelPlacement(); hint('已暂时不挂，填写的留言仍保留着');} });
$('close-ornaments').addEventListener('click', () => picker.close());
$('cancel-message').addEventListener('click', () => messageDialog.close());
$('close-message').addEventListener('click', () => $('read-dialog').close());
$('create-tree').addEventListener('click', () => {
  if (location.protocol === 'file:') { location.assign('http://127.0.0.1:8000'); return; }
  createDialog.showModal();
});
$('cancel-create').addEventListener('click', () => createDialog.close());
$('success-close').addEventListener('click', () => successDialog.close());
$('success-create').addEventListener('click', () => { successDialog.close(); createDialog.showModal(); });
$('create-form').addEventListener('submit', async event => {
  event.preventDefault(); const name = $('owner-name').value.trim();
  if (!name) { $('create-error').textContent = '请填写称呼'; return; }
  const button = event.submitter; button.disabled = true; $('create-error').textContent = '';
  try {
    const result = await api('/api/trees',{name});
    location.assign(`/tree/${result.id}`);
  } catch(error) { $('create-error').textContent = error.message; button.disabled = false; }
});
$('share-tree').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(location.href); hint('链接已复制，邀朋友来送一份祝福吧'); }
  catch { hint('请复制浏览器地址栏中的完整网址，再发给朋友'); }
});
async function init() {
  updateAction();
  if (location.protocol === 'file:') {
    hint('写下你的名字，种一棵属于自己的圣诞树');
    $('create-tree').textContent = '种下我的树';
    return;
  }
  if (!treeId) { ready = true; updateAction(); hint('写下名字，让祝福有个落脚的地方'); return; }
  hint('正在寻找这棵树…');
  try {
    await loadTree(); $('share-tree').hidden = false;
    hint(hasGifted ? '你的心意已在树上，点开装饰看看吧' : '送一枚装饰，留一句想说的话');
  } catch(error) { hint(error.message); }
}
// 返回页面时重新读取，让朋友刚挂上的装饰也能出现。
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && treeId && !sending) loadTree().catch(() => {});
});
init();

// 删除权限依靠服务器保存的创建者标识，不依靠页面上的名字。
$('remove-message').addEventListener('click', () => { $('remove-confirm').hidden=false; $('remove-message').hidden=true; });
$('cancel-remove').addEventListener('click', () => { $('remove-confirm').hidden=true; $('remove-message').hidden=false; });
$('confirm-remove').addEventListener('click', async () => {
  if (!isOwner || viewingPoint === null) return;
  $('confirm-remove').disabled = true;
  try {
    await api(`/api/trees/${treeId}/ornaments/${viewingPoint}/remove`,{});
    const button = pointButtons[viewingPoint];
    messages.delete(viewingPoint); button.replaceChildren(); button.removeAttribute('title'); button.classList.remove('occupied');
    button.setAttribute('aria-label',`选择第 ${viewingPoint+1} 个挂点`);
    $('read-dialog').close(); hint('这份留言已移除');
    try { await loadTree(); } catch { /* 删除成功，保留当前显示。 */ }
  } catch(error) { $('remove-error').textContent=error.message; }
  finally { $('confirm-remove').disabled=false; }
});
// 不自动播放，也不预下载；音符按钮由真实播放状态更新。
const bgm=$('winter-bgm'), musicToggle=$('music-toggle');
bgm.volume=.3;
function syncMusic(){
  const playing=!bgm.paused;
  musicToggle.setAttribute('aria-pressed',String(playing));
  musicToggle.setAttribute('aria-label',playing?'暂停冬夜音乐':'播放冬夜音乐');
  musicToggle.title=playing?'暂停冬夜音乐':'播放冬夜音乐';
}
musicToggle.addEventListener('click',async()=>{
  if (!bgm.paused) { bgm.pause(); return; }
  musicToggle.disabled=true;
  try { await bgm.play(); }
  catch { hint('音乐暂时无法播放，请稍后再试'); }
  finally { musicToggle.disabled=false;syncMusic(); }
});
bgm.addEventListener('play',syncMusic);bgm.addEventListener('pause',syncMusic);
bgm.addEventListener('error',()=>{syncMusic();hint('音乐暂时无法播放，装饰和留言仍可正常使用');});
