"""CLI: python -m worlds <step> [--fixed]"""
import argparse
import importlib
import logging

STEPS = {"data": "worlds.load", "champs": "worlds.champions",
         "model": "worlds.model", "swiss": "worlds.swiss", "knockout": "worlds.knockout"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m worlds")
    parser.add_argument("step", choices=[*STEPS, "all"])
    parser.add_argument("--fixed", action="store_true",
                        help="knockout: play the real bracket from config knockout.bracket")
    args = parser.parse_args()
    if args.fixed and args.step != "knockout":
        parser.error("--fixed only applies to the knockout step")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for step in STEPS if args.step == "all" else [args.step]:
        module = importlib.import_module(STEPS[step])
        module.run(fixed=args.fixed) if step == "knockout" else module.run()


if __name__ == "__main__":
    main()
