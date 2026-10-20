"""
Executable module entry point for Genesis Development Journal.
Allows running: python -m journal <command>
"""

import sys
from journal.cli import main

if __name__ == "__main__":
    sys.exit(main())
