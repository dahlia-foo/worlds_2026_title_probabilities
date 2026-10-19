"""CLI: python -m worlds <step>"""
import argparse
import importlib
import logging

STEPS = {"data": "worlds.load", "champs": "worlds.champions"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m worlds")
    parser.add_argument("step", choices=[*STEPS, "all"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for step in STEPS if args.step == "all" else [args.step]:
        importlib.import_module(STEPS[step]).run()


if __name__ == "__main__":
    main()
