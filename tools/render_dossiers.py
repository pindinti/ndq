#!/usr/bin/env python3
"""NDQ v0.3: render evidenced dossier slots from structured, validated records.

No network, no publication and no scraping. Approved explanatory HTML lives in
separate curated templates; factual timeline, sources, people, notice versions,
status observations and selected metadata render from ndq-dossier-0.2 records.

Chronological events, contextual milestones and dates merely scheduled by a
notice remain separate types even when they share a human-readable timeline.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'observatorio' / 'conteudo' / 'editorial'
MARKER_RE = re.compile(r'\{\{NDQ_[A-Z_]+\}\}')


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def display_date(s: str) -> str:
    """Date supplied by institution: no timezone conversions or guessed days."""
    raw = s.split('T', 1)[0]
    return date.fromisoformat(raw).strftime('%d/%m/%Y')


def source_map(record: dict) -> dict:
    return {ref['source_id']: ref for ref in record['source_references']}


def official_source(record: dict, source_ids: list[str]) -> dict:
    refs = source_map(record)
    for sid in source_ids:
        src = refs.get(sid)
        if src:
            return src
    raise ValueError(f'No source linked to {record.get("slug")} element: {source_ids}')


def official_link(record: dict, source_ids: list[str], label: str = 'Ver fonte oficial') -> str:
    src = official_source(record, source_ids)
    url = src['url']
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError(f'Untrusted source URL: {url}')
    return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(label)} <span aria-hidden="true">↗</span></a>'


def main_timeline(record: dict) -> str:
    """Timeline uses actual API events and *distinct* contextual entries.

    Do not promote contextual observations or planned dates to official events.
    """
    rows: list[tuple[str, str]] = []
    for ev in record.get('events', []):
        at = ev['occurred_at']
        when = display_date(at)
        html_date = f'<time datetime="{e(at)}">{e(when)}</time>'
        if record['kind'] == 'legislative_proposal':
            extra = f'<span class="micro-sep">·</span><span>{e(ev.get("agency_code") or "Órgão não informado")}</span>'
            if 'T' in at:
                extra += f'<span class="event-time" title="Horário informado pela fonte; fuso não especificado">{e(at.split("T",1)[1][:5])}</span>'
            details = (f'<p>Sequência na API: {e(ev["official_sequence"])}. ID local de controle: '
                       f'<code>{e(ev["event_id"].rsplit(":",1)[-1][:16])}…</code> (não é identificador oficial).</p>')
            title = ev.get('display_title', ev['event_type'])
            desc = ev['description']
        else:
            extra = f'<span class="micro-sep">·</span><span>{e(ev.get("display_badge", "Documento oficial"))}</span>'
            details = ''
            title = ev.get('display_title', ev['event_type'])
            desc = ev['description']
        item = f'''<li class="event-row" data-ndq-event="{e(ev['event_id'])}" data-ndq-kind="official-event">
<div class="event-line"><span class="event-dot" aria-hidden="true"></span></div>
<div class="event-content"><div class="event-meta">{html_date}{extra}</div>
<h4>{e(title)}</h4><p>{e(desc)}</p>
<details class="event-evidence"><summary>Rastreabilidade documental</summary>{details}{official_link(record,ev['source_ids'])}</details></div></li>'''
        rows.append((at[:10], item))

    for item in record.get('contextual_milestones', []):
        period = item['period']
        meta = f'<time datetime="{e(period)}">{e(item["display_date"])}</time><span class="badge-micro">{e(item["display_badge"])}</span>'
        kind = item['context_kind']
        if kind == 'scheduled_date_not_confirmed_occurrence':
            caution = 'Esta é uma data prevista no documento, não um evento cuja realização tenha sido confirmada.'
        elif kind == 'ndq_observation_not_agency_event':
            caution = 'Observação feita pelo NDQ; não representa novo ato da instituição.'
        elif kind == 'institutional_communication_not_procurement_award':
            caution = 'Comunicação institucional; não equivale a contratação ou fornecimento executado.'
        else:
            caution = 'Contexto documental selecionado; não é cronologia processual exaustiva.'
        out = f'''<li class="event-row" data-ndq-milestone="{e(item['milestone_id'])}" data-ndq-kind="{e(kind)}">
<div class="event-line"><span class="event-dot" aria-hidden="true"></span></div>
<div class="event-content"><div class="event-meta">{meta}</div>
<h4>{e(item['title'])}</h4><p>{e(item['description'])}</p>
<details class="event-evidence"><summary>Ver contexto e referência</summary>
<p>{e(caution)}</p>{official_link(record,item['source_ids'])}</details></div></li>'''
        rows.append((period+'-00' if len(period)==7 else period, out))
    return '\n'.join(item for _,item in sorted(rows, key=lambda x:x[0]))


def source_list(record: dict) -> str:
    lines = []
    for src in record['source_references']:
        label = src.get('display_label',src['label'])
        desc = src.get('display_description','Referência institucional indicada no dossiê.')
        note = src.get('display_note') or ('Consultado em '+display_date(src['observed_at']))
        if src['access_state']=='not_checked':
            note += ' · Conteúdo não reanalisado nesta versão'
        lines.append(f'''<li class="source-row" data-ndq-source="{e(src['source_id'])}">
<span class="source-mark" aria-hidden="true">↗</span><div>{official_link(record,[src['source_id']],label)}
<p>{e(desc)}</p><small>{e(note)}</small></div></li>''')
    return '\n'.join(lines)


def official_status(record: dict) -> tuple[str, str]:
    states = record.get('official_status_observations') or []
    if len(states)==0:
        return 'Documento público', 'Estado processual não revalidado neste dossiê'
    obs = states[-1]
    return obs['status_text'], f'Situação oficial observada em {display_date(obs["observed_at"])}'


def status_box(record: dict) -> str:
    if record['kind']=='public_procurement':
        return f'''<span class="overline">Pregão SRP nº 669/2026</span>
<strong>Aviso e retificação documentados</strong><small>Resultados, assinatura contratual e entrega não verificados neste dossiê.</small>'''
    text, meta = official_status(record)
    title = 'Última consulta à Câmara' if record['kind']=='legislative_proposal' else 'Planilha da agenda regulatória'
    return f'<span class="overline">{e(title)}</span><strong>{e(text)}</strong><small>{e(meta)}</small>'


def fact(record:dict, predicate:str) -> object:
    items = [f for f in record.get('facts',[]) if f['predicate']==predicate]
    return items[0]['value'] if items else None


def money_br(value_cents: int) -> str:
    value_cents = int(value_cents)
    digits = f'{abs(value_cents)//100:,}'.replace(',','.')
    return ('-' if value_cents < 0 else '')+f'R$ {digits},{abs(value_cents)%100:02d}'


def ribbon(record: dict) -> str:
    kind = record['kind']
    if kind=='legislative_proposal':
        actors = record['actor_roles']
        author = next((a['actor_name'] for a in actors if a['role_type']=='author'), 'Não informado')
        relator = next((a['actor_name'] for a in actors if a['role_type']=='designated_rapporteur'), 'Não informado')
        presented = fact(record,'presented_at')
        last = max(ev['occurred_at'] for ev in record['events'])
        pairs=[('Autor',author),('Relator designado',relator),('Apresentação',display_date(presented)),('Último registro',display_date(last))]
    elif kind=='regulatory_agenda_theme':
        ext=record['type_data']
        pairs=[('Instituição',record['institution']),('Macrotema',ext['macrotheme']),('Agenda regulatória',ext['agenda_cycle'].replace('-', '–')),('Consulta à fonte',display_date(record['last_observed_at']))]
    else:
        ext=record['type_data']
        versions=ext['notice_versions']
        amount=versions[-1]['maximum_value_brl_cents']
        pairs=[('Órgão','Fundepar · Governo do Paraná'),('Modalidade',fact(record,'procurement_modality')),('Lotes anunciados',fact(record,'notice_announced_lot_count')),('Valor máximo corrigido',money_br(amount))]
    return ''.join(f'<div class="fact-pair"><span>{e(k)}</span><strong>{e(v)}</strong></div>' for k,v in pairs)


def card_meta(record: dict) -> str:
    kind=record['kind']
    if kind=='legislative_proposal':
        people=record['actor_roles']
        author=next(a['actor_name'] for a in people if a['role_type']=='author')
        relator=next(a['actor_name'] for a in people if a['role_type']=='designated_rapporteur')
        entries=[('Autor',author),('Relator designado',relator),('Último movimento conhecido',display_date(max(e['occurred_at'] for e in record['events']))),('Consulta à fonte',display_date(record['last_observed_at']))]
    elif kind=='regulatory_agenda_theme':
        entries=[('Órgão responsável',record['institution']),('Identificador do tema',record['type_data']['theme_id']),('Situação na agenda',official_status(record)[0]),('Dados consultados',display_date(record['last_observed_at']))]
    else:
        versions=record['type_data']['notice_versions']
        entries=[('Órgão comprador',record['institution']),('Modalidade',fact(record,'procurement_modality')),('Aviso original',display_date(versions[0]['document_date'])),('Valor máximo retificado',money_br(versions[-1]['maximum_value_brl_cents']))]
    return ''.join(f'<div class="fact-pair"><span>{e(k)}</span><strong>{e(v)}</strong></div>' for k,v in entries)


def actors(record:dict) -> str:
    blocks=[]
    for actor in record['actor_roles']:
        if actor['role_type']=='author':
            role='Autoria'
            explanation='Nome informado no registro oficial de autoria; filiação partidária à época não verificada neste dossiê.'
        elif actor['role_type']=='designated_rapporteur':
            role='Relatoria na CDC'
            explanation=f'Designação documentada em {display_date(actor["role_date"])}; filiação à época não inferida.'
        else:raise ValueError('Unknown actor role in private pilot')
        blocks.append(f'''<div class="actor"><div class="role">{e(role)}</div><strong>{e(actor['actor_name'])}</strong>
<p>{e(explanation)} {official_link(record,actor['source_ids'],'Conferir registro')}</p></div>''')
    return '\n'.join(blocks)


def changes(record:dict) -> str:
    versions=record['type_data']['notice_versions']
    change=record['change_history'][0]
    old=versions[0]['maximum_value_brl_cents']
    new=versions[-1]['maximum_value_brl_cents']
    delta=new-old
    if delta!=change['delta_brl_cents'] or new!=change['new_observed_value']:
        raise ValueError('Documented rectification does not balance')
    direction='a menos' if delta < 0 else 'a mais' if delta > 0 else 'sem variação'
    evidence=' · '.join(official_link(record,[sid],source_map(record)[sid]['label']) for sid in change['basis_source_ids'])
    return f'''<div class="change-header">Retificação de {e(display_date(versions[-1]['document_date']))} · valor máximo</div>
<div class="change-math"><div class="change-side"><span>Aviso original</span><strong>{e(money_br(old))}</strong></div>
<span class="change-arrow" aria-hidden="true">→</span><div class="change-side"><span>Valor corrigido</span><strong>{e(money_br(new))}</strong></div></div>
<div class="change-foot"><strong>Diferença: {e(money_br(abs(delta)))} {e(direction)}.</strong> A alteração é documental; não comprova mudança nos produtos ou sua entrega.<div class="ndq-evidence-inline">{evidence}</div></div>'''


def items(record:dict) -> str:
    out=[]
    for item in record['type_data']['item_requirements']:
        out.append(f'''<div class="item-block" data-ndq-item="documented"><small>Categoria prevista no aviso</small><strong>{e(item['name'])}</strong><p>{e(item['details'])}</p></div>''')
    return '\n'.join(out)


def render_record(record:dict, template:dict) -> tuple[str,str]:
    if record.get('publication_status')!='private_preview_only' or template.get('status')!='approved_private_pilot_narrative':
        raise ValueError('Private editorial gate not satisfied')
    if template.get('slug')!=record.get('slug'):
        raise ValueError('Editorial template slug mismatch')
    slots={
        '{{NDQ_CARD_TITLE}}':e(record['presentation']['title']),
        '{{NDQ_CARD_META}}':card_meta(record),
        '{{NDQ_TIMELINE}}':main_timeline(record),
        '{{NDQ_SOURCES}}':source_list(record),
        '{{NDQ_RIBBON}}':ribbon(record),
        '{{NDQ_STATUS}}':status_box(record),
        '{{NDQ_ACTORS}}':actors(record) if record['kind']=='legislative_proposal' else None,
        '{{NDQ_CHANGE}}':changes(record) if record['kind']=='public_procurement' else None,
        '{{NDQ_ITEMS}}':items(record) if record['kind']=='public_procurement' else None,
    }
    out=[]
    for field in ('card_html','detail_html'):
        raw=template[field]
        for token in set(MARKER_RE.findall(raw)):
            if token not in slots or slots[token] is None:
                raise ValueError(f'Unexpected marker {token} in {record["slug"]}')
            if raw.count(token)!=1:
                raise ValueError(f'Non-unique marker {token} in {record["slug"]}')
            raw=raw.replace(token,slots[token])
        if MARKER_RE.search(raw):
            raise ValueError(f'Unrendered NDQ marker in {record["slug"]}')
        out.append(raw)
    return tuple(out)
