"""Offline append-only ingestion decision; never writes to a database.

The future SQL adapter must enforce these keys with a unique constraint in one
transaction. A dry-run decision alone is not a concurrency guarantee.
"""
from __future__ import annotations

import re

from .compare import compare_snapshots
from .normalize import PROJECTION_VERSION, digest

SECTIONS = ('proposition', 'events', 'authors', 'relationships')


def source_content_key(observation: dict) -> str:
    """Hash selected official values and endpoint completeness, excluding poll time."""
    if observation.get('projection_version') != PROJECTION_VERSION:
        raise ValueError('Unsupported source projection')
    if (isinstance(observation.get('proposition_id'), bool)
            or not isinstance(observation.get('proposition_id'), int) or observation['proposition_id'] < 1):
        raise ValueError('Invalid proposition identity')
    sections = {}
    for name in SECTIONS:
        coverage = observation['coverage'][name]
        if coverage.get('complete') is not True:
            raise ValueError('Incomplete source retrieval cannot be a factual baseline')
        value = observation[name]
        rows = [value] if name == 'proposition' and value else value or []
        if name == 'proposition' and len(rows) != 1:
            raise ValueError('Proposition identity unavailable')
        sections[name] = sorted((row['original_fields'] for row in rows), key=digest)
    return digest({'projection_version': PROJECTION_VERSION,
                   'proposition_id': observation['proposition_id'], 'sections': sections})


def plan_source_ingestion(current: dict, *, known_content_keys=(), previous: dict | None = None) -> dict:
    """Return proposed actions without creating a source, editorial or site record."""
    try:
        key = source_content_key(current)
    except (KeyError, TypeError, ValueError):
        return {'mode': 'simulation_only', 'status': 'incomplete_or_invalid', 'source_content_key': None,
                'append_source_snapshot': False, 'source_change_candidates': [],
                'create_factual_revision': False, 'create_editorial_proposal': False,
                'approve_or_publish': False, 'database_writes': False}
    known = set(known_content_keys)
    if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in known):
        raise ValueError('Invalid known content key')
    if previous is not None and previous['proposition_id'] != current['proposition_id']:
        raise ValueError('Previous source identity mismatch')
    previous_key = None
    if previous is not None:
        try:
            previous_key = source_content_key(previous)
        except (KeyError, TypeError, ValueError):
            pass
    if key in known or key == previous_key:
        status, candidates = 'already_observed', []
    elif previous is None:
        status, candidates = 'initial_source_snapshot', []
    elif previous_key is None:
        status, candidates = 'previous_source_incomplete', []
    else:
        status = 'new_source_snapshot'
        candidates = [item for item in compare_snapshots(current, previous)
                      if item['classification'] != 'unchanged']
    return {'mode': 'simulation_only', 'status': status, 'source_content_key': key,
            'append_source_snapshot': status in ('initial_source_snapshot', 'new_source_snapshot'),
            'source_change_candidates': candidates,
            'create_factual_revision': False, 'create_editorial_proposal': False,
            'approve_or_publish': False, 'database_writes': False,
            'enforcement': 'future_database_unique_constraint_required'}
