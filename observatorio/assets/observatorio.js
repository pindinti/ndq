"use strict";
(() => {
  const filters=[...document.querySelectorAll('[data-filter]')];
  const cards=[...document.querySelectorAll('[data-card]')];
  const search=document.getElementById('search');
  const count=document.getElementById('result-count');
  const empty=document.getElementById('empty');
  let category='todos';
  function normalize(v){return String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR');}
  function refresh(){
    const query=normalize(search?.value).trim();
    let shown=0;
    cards.forEach(card=>{
      const matchCategory=category==='todos'||card.dataset.group===category;
      const matchQuery=!query||normalize(card.dataset.search).includes(query);
      const show=matchCategory&&matchQuery;
      card.hidden=!show;
      if(show)shown++;
    });
    if(count)count.textContent=`${shown} ${shown===1?'dossiê encontrado':'dossiês encontrados'}`;
    if(empty)empty.hidden=shown!==0;
  }
  filters.forEach(btn=>btn.addEventListener('click',()=>{
    category=btn.dataset.filter;
    filters.forEach(item=>item.setAttribute('aria-pressed',String(item===btn)));
    refresh();
  }));
  search?.addEventListener('input',refresh);
  refresh();
})();
