# ``python -m halo_systems`` runs the same command line as the installed ``halo`` command.
import sys

from .cli import main

sys.exit(main())
