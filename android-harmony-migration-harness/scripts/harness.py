#!/usr/bin/env python3
"""Launch the single Migration Harness runtime using the package environment."""
import json
import subprocess
import sys

from common import ROOT, configure_runtime_environment, python_path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        configure_runtime_environment()
        return subprocess.call([python_path(), str(ROOT / 'runtime/harness/migration.py'), *(args or ['--help'])])
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
