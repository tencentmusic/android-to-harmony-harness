#!/usr/bin/env python3
"""Generic before-end-turn hook. A host must honor a nonzero exit as a veto."""
import sys
from migration import main

if __name__ == "__main__":
    sys.exit(main(["end-check", *sys.argv[1:]]))
