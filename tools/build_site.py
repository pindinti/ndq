#!/usr/bin/env python3
"""Rebuild the private NDQ MVP static pages from editorial fixture JSONs.
Uses Python standard library only; publish only after human review of generated files.
Rich HTML blocks in JSON are trusted editorial fragments from approved prototypes,
NOT arbitrary user input and NOT a complete normalized data contract.
"""
from pathlib import Path
import json, html
ROOT=Path(__file__).resolve().parents[1]
OB=ROOT/'observatorio'
DATA=OB/'conteudo'/'dossies'
OUT=OB/'dossies'
LABEL={'legislacao':'Projetos de lei','regulacao':'Regulação','compras':'Compras públicas'}

def escape(x):return html.escape(str(x),quote=True)

def fixture(path):
    d=json.loads(path.read_text('utf-8'))
    required=['id','group','title','description','status','source','card_html','detail_html','verified_at']
    if any(not d.get(k) for k in required):raise ValueError(f'Missing fixture field: {path}')
    if d['group'] not in LABEL:raise ValueError(f'Unknown group: {path}')
    if d['id']!=path.stem or not all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in d['id']):raise ValueError(f'Invalid slug: {path}')
    if not d['source'].startswith('https://'):raise ValueError(f'Official source not HTTPS: {path}')
    return d

def detail_page(d):
    title=escape(d['title']); short=escape(d['type']); code=escape(d['eyebrow']); group=escape(LABEL[d['group']])
    styles={'legislacao':'','regulacao':'<link rel="stylesheet" href="../../assets/anvisa.css">','compras':'<link rel="stylesheet" href="../../assets/fundepar.css">'}[d['group']]
    return f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{escape(d['description'])}"><meta name="robots" content="noindex, nofollow">
<title>{title} · Observatório NDQ</title>
<link rel="stylesheet" href="../../assets/dossie.css">{styles}<script defer src="../../assets/dossie.js"></script></head>
<body id="inicio" class="show-notes">
<div class="topbar"><div class="container"><strong>PRÉVIA PRIVADA · não publicado</strong><span>Dados observados em 27/10/2026 · requer nova conferência antes de publicação</span></div></div>
<header class="masthead"><div class="container mast-inner">
<a class="brand" href="../../../" aria-label="Não de Queijo, página inicial"><span class="brand-icon" aria-hidden="true"><span>n.</span></span><span class="brand-lines"><strong>Observatório NDQ</strong><small>Informação para inclusão alimentar</small></span></a>
<nav class="mastnav" aria-label="Navegação"><a href="../../../">Não de Queijo</a><a class="current" href="../../">Observatório</a><a href="#transparencia" data-switch="detail">Fontes</a></nav>
</div></header>
<div class="proto-strip"><div class="container proto-row">
<div class="proto-name"><strong>Prévia de dossiê · {short}</strong><small>Conteúdo original aprovado conceitualmente, com metadados da consulta de 27/10/2026. Não é uma publicação vigente.</small></div>
<div class="proto-controls"><div class="segmented" role="tablist" aria-label="Formato de visualização">
<button id="tab-card" role="tab" aria-selected="true" aria-controls="view-card" tabindex="0" data-view="card">Card de listagem</button>
<button id="tab-detail" role="tab" aria-selected="false" aria-controls="view-detail" tabindex="-1" data-view="detail">Dossiê completo</button>
</div><label class="toggle"><input type="checkbox" id="notes" checked><span>Notas internas</span></label></div>
</div></div>
<main class="container" id="conteudo"><a class="portal-back" href="../../">← Voltar aos dossiês</a>
<div class="breadcrumbs" aria-label="Caminho"><a href="../../../">Não de Queijo</a><span class="slash">/</span><a href="../../">Observatório</a><span class="slash">/</span><span>{group}</span><span class="slash">/</span><strong>{code}</strong></div>
<section id="view-card" role="tabpanel" aria-labelledby="tab-card">{d['card_html']}</section>
<section id="view-detail" role="tabpanel" aria-labelledby="tab-detail" hidden class="hide">{d['detail_html']}</section>
</main><footer><div class="container footer-content"><span><strong>Observatório NDQ</strong> · Informação verificável a serviço da inclusão alimentar.</span><span><a href="../../">Todos os dossiês</a> · Protótipo privado · 27/10/2026</span></div></footer>
</body></html>'''

def card(d):
    vals={k:escape(d[k]) for k in ['id','title','description','eyebrow','status','group','type','relevance','verified_at']}
    terms=' '.join(str(d.get(k,'')) for k in ['title','description','eyebrow','status','type','relevance'])+' '.join(d.get('theme',[]))
    url=f'dossies/{vals["id"]}/#dossie'
    return f'''<article class="dossier-card" data-card data-group="{vals['group']}" data-search="{escape(terms)}">
        <div class="card-top"><span class="card-group">{vals['type']}</span><span class="card-topic">{vals['relevance']}</span></div>
        <div class="card-main"><span class="card-code">{vals['eyebrow']}</span><h2>{vals['title']}</h2><p class="summary">{vals['description']}</p></div>
        <div class="card-bottom"><span class="status-pill">{vals['status']}</span><div class="card-footer"><span class="card-date">Consulta: 27/10/2026</span><a class="card-link" href="{url}" aria-label="Abrir dossiê: {vals['title']}">Abrir dossiê <span aria-hidden="true">→</span></a></div></div>
    </article>'''

def home_page(dossiers):
    cards='\n'.join(card(d) for d in dossiers)
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Observatório NDQ — Informação pública para inclusão alimentar, com fontes oficiais e dossiês verificáveis.">
<meta name="robots" content="noindex, nofollow"><title>Observatório NDQ · Informação para inclusão alimentar</title>
<link rel="stylesheet" href="assets/observatorio.css"><script defer src="assets/observatorio.js"></script></head>
<body>
<div class="topbar"><div class="container"><strong>PRÉVIA PRIVADA · não publicado</strong><span>Conteúdo revisado em 27/10/2026 · sem atualizações automáticas</span></div></div>
<header class="masthead"><div class="container mast-inner"><a class="brand" href="../"><span class="brand-symbol" aria-hidden="true"><span>n.</span></span><span class="brand-name">Observatório NDQ<small>Informação para inclusão alimentar</small></span></a>
<nav class="mastnav" aria-label="Navegação"><a href="../">Não de Queijo</a><a class="active" href="./" aria-current="page">Dossiês</a><a href="#metodologia">Metodologia</a></nav></div></header>
<main class="container">
<section class="hero"><div class="eyebrow">Informação pública · Inclusão alimentar</div><h1>Decisões públicas também estão no seu prato.</h1>
<p>Acompanhe propostas, regulamentações e compras públicas que podem afetar a inclusão alimentar no Brasil. Os documentos oficiais estão sempre a um clique de distância.</p>
<div class="observation">Estes três dossiês são exemplos de validação editorial. A situação de cada registro corresponde à última consulta documentada em 27/10/2026, não ao acompanhamento em tempo real.</div></section>
<section aria-label="Dossiês disponíveis" id="dossies">
<div class="controls"><div class="filters" role="group" aria-label="Filtrar por tipo de dossiê">
<button class="filter" type="button" data-filter="todos" aria-pressed="true">Todos</button>
<button class="filter" type="button" data-filter="legislacao" aria-pressed="false">Projetos de lei</button>
<button class="filter" type="button" data-filter="regulacao" aria-pressed="false">Regulação</button>
<button class="filter" type="button" data-filter="compras" aria-pressed="false">Compras públicas</button></div>
<div class="search"><label for="search">Buscar</label><input id="search" type="search" placeholder="Tema, órgão ou palavra-chave" autocomplete="off"></div></div>
<p class="count" id="result-count">3 dossiês encontrados</p><div class="card-grid" id="card-grid">{cards}</div>
<p class="empty" id="empty" hidden>Nenhum dossiê corresponde aos filtros. Experimente outra palavra-chave.</p></section>
<section class="editorial-note" id="metodologia"><span class="eyebrow">Nossa abordagem</span><h2>Fidelidade à documentação. Confiança no leitor.</h2>
<p>O Observatório NDQ é um serviço informativo da Não de Queijo dedicado a tornar mais acessíveis decisões públicas relacionadas à inclusão alimentar, incluindo necessidades médicas e escolhas éticas ou culturais. Apresentamos o que os documentos comprovam e identificamos aquilo que ainda não foi verificado.</p>
<p><strong>Projeto de lei não é lei vigente; agenda regulatória não é norma aprovada; edital não é produto entregue.</strong> O conteúdo aqui é experimental, para revisão privada, sem atualizações automáticas ou chamadas à participação não verificadas.</p></section>
</main><footer class="foot"><div class="container"><span><strong>Observatório NDQ</strong> · A inclusão não pode ter final.</span><span><a href="../">Home da marca</a> · Protótipo privado v0.1</span></div></footer>
</body></html>'''

def main():
    ds=sorted((fixture(path) for path in DATA.glob('*.json')),key=lambda d:d.get('sort',10))
    if len(ds)!=3:raise ValueError('Expected exactly three approved pilot dossiers')
    for d in ds:
        dest=OUT/d['id'];dest.mkdir(parents=True,exist_ok=True)
        (dest/'index.html').write_text(detail_page(d),encoding='utf-8')
    (OB/'index.html').write_text(home_page(ds),encoding='utf-8')
    print('Generated Observatory index and',len(ds),'dossiers')
if __name__=='__main__':main()
