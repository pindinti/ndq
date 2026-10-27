"""Run with python -B -m tools.persistence --help from the repository root."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contract import ContractError, load_pilots, summary
from .db import ConfigurationError, MigrationError, connect, load_database_url, migrate
from .repository import IdentityConflict


def main(argv=None):
    parser = argparse.ArgumentParser(description='NDQ private persistence; never fetches or publishes')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate', help='Apply forward migrations to explicitly configured DATABASE_URL')
    imp = commands.add_parser('import-pilots', help='Validate and import the existing v0.3 JSON records')
    imp.add_argument('--dry-run', action='store_true', help='Validate offline; no connection or DB comparison')
    imp.add_argument('--report', type=Path, help='Write a JSON import report (no connection credentials)')
    args = parser.parse_args(argv)
    try:
        if args.command == 'migrate':
            with connect(load_database_url()) as conn:
                result = {'applied_migrations': migrate(conn)}
        else:
            pilots = load_pilots()
            if args.dry_run:
                result = summary(pilots)
            else:
                from .repository import import_pilots
                with connect(load_database_url()) as conn:
                    result = import_pilots(conn, pilots)
    except Exception as exc:
        # libpq errors may contain credentials/DSNs; do not print their messages.
        result = {'success': False, 'error_type': type(exc).__name__,
                  'message': 'Operation failed; no partial batch committed. Check configuration, contract and migration state.'}
        if isinstance(exc, (ContractError, ConfigurationError, MigrationError, IdentityConflict)):
            result['detail'] = str(exc)
        status = 1
    else:
        result['success'] = True
        status = 0
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if getattr(args, 'report', None):
        try:
            args.report.write_text(output + '\n', encoding='utf-8')
        except OSError as exc:
            # Database work may already have committed; never label a file error
            # as a rolled-back import. Preserve the result on stdout.
            result['report_file_error'] = type(exc).__name__
            output = json.dumps(result, ensure_ascii=False, indent=2)
            status = 1
    print(output)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
