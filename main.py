"""Wavemaker System Control -- entry point.

    python main.py                 connect to the PLC, fall back to simulation
    python main.py --simulate      never touch the PLC (training, development)
    python main.py --ip 10.0.0.5   use a different PLC address

Written for the UNC Fluids Lab.

GUI developed by Jasper Christie, Marc Lewis, Chelsea Rowe and Ezri White.
Original GUI by Raphael Provosty and Schuyler Moss.
Later work by Sicheng Wang and the COMP 523 teams.

Uses the pylogix library by Burt Peterson, maintained by Dustin Roeder --
https://github.com/dmroeder/pylogix -- vendored under modules/.
"""

from __future__ import annotations

import argparse
import sys

from app import paths
from modules.logging.log_utils import setup_file_logging


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py", description="Wavemaker System Control"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="run without the PLC; nothing will move",
    )
    parser.add_argument(
        "--ip",
        default=None,
        metavar="ADDRESS",
        help="PLC address (default 192.168.1.1)",
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=None,
        metavar="N",
        help="processor slot (default 1)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    paths.ensure_directories()
    setup_file_logging()

    # Imported after logging is configured so the connection attempt is recorded.
    from Model import Model
    from View import View

    model = Model(
        ip_address=args.ip,
        processor_slot=args.slot,
        simulate=args.simulate,
    )

    # The window is built and shown first; connecting to the machine and
    # clearing it happens on a worker thread from View.run().  Doing that work
    # before the window existed is why launching used to show nothing at all
    # for the first fifteen seconds or so.
    view = View(model)
    view.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
