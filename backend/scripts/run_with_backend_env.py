#!/usr/bin/env python3
"""Run one operator child with literal dotenv values, without evaluating shell code."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 2:
        print('ERROR: protected environment path and child command are required.', file=sys.stderr)
        return 64
    try:
        from dotenv.parser import parse_stream
        profile = Path(arguments[0])
        if not profile.is_file():
            raise ValueError('invalid environment')
        with profile.open(encoding='utf-8-sig') as handle:
            bindings = list(parse_stream(handle))
        if any(binding.error for binding in bindings):
            raise ValueError('invalid environment')
        values = {binding.key: binding.value for binding in bindings if binding.key is not None}
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or value is None
               for key, value in values.items()):
            raise ValueError('invalid environment')
        environment = dict(os.environ)
        environment.update(values)
        # The selected profile is authoritative; do not discover another .env.
        environment['PYTHON_DOTENV_DISABLED'] = '1'
        os.execvpe(arguments[1], arguments[1:], environment)
    except Exception:
        # Never stringify parser, driver or exec errors containing private values.
        print('ERROR: unable to load the selected backend environment or start the child.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
