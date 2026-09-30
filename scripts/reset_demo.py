"""
Reset the live demo
===================

    docker compose exec api python -m scripts.reset_demo

Same as ``scripts.reset_dhanu_demo``.
"""

import sys

from scripts.reset_dhanu_demo import main

if __name__ == "__main__":
    sys.exit(main())
