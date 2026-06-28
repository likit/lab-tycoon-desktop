"""Application entrypoint."""

from __future__ import annotations

import argparse
import sys

from app.system.simulation import run_and_print_workflow_simulation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LabTycoon desktop application")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run the currently loaded workflow as a headless SimPy simulation.",
    )
    parser.add_argument(
        "--specimens-number",
        type=int,
        default=None,
        help="Number of specimens to simulate when --simulate is enabled.",
    )
    parser.add_argument(
        "--workflow-id",
        type=str,
        default=None,
        help="Workflow document id to simulate. Defaults to the active workflow when omitted.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.simulate:
        if args.specimens_number is None or args.specimens_number <= 0:
            parser.error("--specimens-number must be a positive integer when --simulate is used.")
        try:
            run_and_print_workflow_simulation(
                specimens_number=args.specimens_number,
                workflow_id=args.workflow_id,
            )
        except Exception as exc:
            print(f"Simulation failed: {exc}", file=sys.stderr)
            return 1
        return 0

    from app.main import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
