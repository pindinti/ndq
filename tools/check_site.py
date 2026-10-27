#!/usr/bin/env python3
"""Basic static quality checks for NDQ site (no dependencies)."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from html.parser import HTMLParser
from collections import Counter
import json
ROOT=Path(__file__).resolve().parents[1]
class Walker(HTMLParser):
    def __init__(self):
        super().__init__();self.links=[];self.ids=[];self.alt_missing=[];self.stylesheets=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if d.get('id'):self.ids.append(d['id'])
        for k in ['href','src']:
            if d.get(k):self.links.append((tag,k,d[k]))
        if tag=='img' and 'alt' not in d:self.alt_missing.append(d.get('src'))

issues=[]
htmls=list(ROOT.rglob('*.html'))
for p in htmls:
    v=Walker();v.feed(p.read_text('utf8'))
    for id,count in Counter(v.ids).items():
        if count>1:issues.append(f'{p.relative_to(ROOT)}: duplicated id={id}')
    for src in v.alt_missing:issues.append(f'{p.relative_to(ROOT)}: missing image alt for {src}')
    for tag,k,link in v.links:
        if link.startswith(('https://','http://','mailto:','tel:','#','data:')):continue
        if link.startswith('javascript:'):issues.append(f'{p}: unsafe javascript url');continue
        url=urlsplit(link)
        loc=(p.parent/unquote(url.path)).resolve()
        if not loc.is_relative_to(ROOT.resolve()):issues.append(f'{p}: link outside root {link}');continue
        if loc.is_dir():loc=loc/'index.html'
        if not loc.exists():issues.append(f'{p.relative_to(ROOT)}: local link broken {link}')
        if url.fragment and loc.suffix=='.html':
            target=Walker();target.feed(loc.read_text('utf8'))
            if url.fragment not in target.ids and url.fragment not in ['dossie','card']:
                issues.append(f'{p.relative_to(ROOT)}: unknown fragment {link}')

fixtures=list((ROOT/'observatorio/conteudo/dossies').glob('*.json'))
for f in fixtures:
    d=json.loads(f.read_text('utf8'))
    if not d.get('source','').startswith('https://'):issues.append(f'{f}: source not HTTPS')
    if d.get('publication_status')!='private_preview_only':issues.append(f'{f}: wrong publication status')
print(f'Checked {len(htmls)} HTML pages and {len(fixtures)} dossier fixtures.')
if issues:
    for issue in issues:print('FAIL',issue)
    raise SystemExit(1)
print('PASS: local links, IDs, assets, source schemes and draft status')
