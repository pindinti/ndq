"use strict";
(() => {
  const card = document.getElementById('view-card');
  const detail = document.getElementById('view-detail');
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const notes = document.getElementById('notes');
  if (!card || !detail) return;
  function show(which, scroll = false, updateHash = true) {
    const full = which === 'detail';
    [[card,!full],[detail,full]].forEach(([node,visible])=>{
      node.hidden = !visible;
      node.classList.toggle('hide',!visible);
    });
    tabs.forEach(tab=>{
      const selected=tab.dataset.view===which;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex=selected?0:-1;
    });
    if(updateHash && history.replaceState) history.replaceState(null,'',full?'#dossie':'#card');
    if(scroll) document.getElementById('conteudo').scrollIntoView({behavior:'smooth',block:'start'});
  }
  document.querySelectorAll('[data-view]').forEach(btn=>btn.addEventListener('click',()=>show(btn.dataset.view,btn.getAttribute('role')!=='tab')));
  document.querySelectorAll('[data-switch="detail"]').forEach(link=>link.addEventListener('click',ev=>{
    ev.preventDefault();
    show('detail',false,false);
    document.getElementById('transparencia')?.scrollIntoView({behavior:'smooth',block:'start'});
  }));
  // Table of contents already refers to sections in the expanded panel.
  document.querySelectorAll('.contents-links a').forEach(link=>link.addEventListener('click',()=>show('detail',false,false)));
  notes?.addEventListener('change',()=>document.body.classList.toggle('show-notes',notes.checked));
  if(notes) document.body.classList.toggle('show-notes',notes.checked);
  tabs.forEach((tab,i)=>tab.addEventListener('keydown',ev=>{
    if(ev.key==='ArrowRight' || ev.key==='ArrowLeft'){
      ev.preventDefault();
      const next=tabs[(i+(ev.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
      next.focus(); show(next.dataset.view,false);
    }
  }));
  const cardHashes=new Set(['','#card','#inicio','#conteudo']);
  function handleHash(){
    if(!cardHashes.has(location.hash) && location.hash!== '#') show('detail',false,false);
    else show('card',false,false);
  }
  handleHash();
  window.addEventListener('hashchange',handleHash);
})();
