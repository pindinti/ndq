"""python -B -m tools.collectors.camara --proposition-id 2498778 --dry-run"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.collectors.http import JsonClient
from .service import collect, previous_observation, save_report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Câmara official observations; private proposals, no ingestion or publication')
    parser.add_argument('--proposition-id', type=int, required=True)
    parser.add_argument('--dry-run', action='store_true', default=True, help='Default and only mode; never writes to a database')
    parser.add_argument('--no-database', action='store_true', help='Compare with local records only; do not load DB configuration')
    parser.add_argument('--previous-proposal', type=Path, help='Compare literal fields with a previous report in .ndq-private/camara')
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--max-pages', type=int, default=30)
    args = parser.parse_args(argv)
    try:
        previous = previous_observation(args.previous_proposal, args.proposition_id) if args.previous_proposal else None
        report = collect(args.proposition_id, client=JsonClient(timeout=args.timeout, retries=args.retries,
                                                               max_pages=args.max_pages),
                         use_database=not args.no_database, previous=previous)
        path = save_report(report)
        output = {'mode': report['mode'], 'execution_status': report['execution_status'],
                  'report': str(path.relative_to(path.parents[2])), 'finding_counts': report['finding_counts'],
                  'database_baseline': report['baselines']['database']['status'], 'database_writes': False}
        status = 0 if report['execution_status'] == 'complete' else 1
    except Exception as exc:
        output = {'execution_status': 'failed', 'error_type': type(exc).__name__,
                  'diagnosis': 'Collector failed; verify arguments and local configuration. No database writes were attempted.'}
        status = 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
