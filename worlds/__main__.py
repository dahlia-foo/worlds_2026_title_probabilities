"""CLI: python -m worlds <step> [--fixed] [--snapshot LABEL]"""
import argparse
import importlib
import logging

# `all` runs the pipeline steps in order; `history` only draws a chart from saved snapshots.
PIPELINE = {"data": "worlds.load", "champs": "worlds.champions",
            "model": "worlds.model", "swiss": "worlds.swiss", "knockout": "worlds.knockout"}
COMMANDS = PIPELINE | {"history": "worlds.history"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m worlds")
    parser.add_argument("step", choices=[*COMMANDS, "all"])
    parser.add_argument("--fixed", action="store_true",
                        help="knockout: play the real bracket from config knockout.bracket")
    parser.add_argument("--snapshot", metavar="LABEL",
                        help="knockout/all: save this run's odds to outputs/odds_history.csv")
    args = parser.parse_args()
    if args.fixed and args.step != "knockout":
        parser.error("--fixed only applies to the knockout step")
    if args.snapshot is not None and args.step not in ("knockout", "all"):
        parser.error("--snapshot only applies to knockout or all")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for step in PIPELINE if args.step == "all" else [args.step]:
        module = importlib.import_module(COMMANDS[step])
        if step == "knockout":
            module.run(fixed=args.fixed, snapshot=args.snapshot)
        else:
            module.run()


if __name__ == "__main__":
    main()
