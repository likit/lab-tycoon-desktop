"""Application entrypoint."""

from __future__ import annotations

import argparse
import sys

from app.system.order_sources import UploadedOrderValidationError, load_uploaded_order_source, use_uploaded_order_source
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
    parser.add_argument(
        "--order-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Import order data from an Excel or CSV file before launching the app.",
    )
    parser.add_argument(
        "--factor",
        type=float,
        default=None,
        help="SimPy realtime factor to use when --simulate is enabled.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible simulations.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.simulate:
        order_source = None
        orders = None
        specimens_number = args.specimens_number
        if args.order_file:
            try:
                order_source = load_uploaded_order_source(args.order_file)
                use_uploaded_order_source()
            except UploadedOrderValidationError as exc:
                print(f"Order import failed: {exc}", file=sys.stderr)
                return 1
            orders = list(order_source.list_orders())
            specimens_number = len(orders)
        elif specimens_number is None or specimens_number <= 0:
            parser.error("--specimens-number must be a positive integer when --simulate is used.")
        try:
            run_and_print_workflow_simulation(
                specimens_number=specimens_number,
                workflow_id=args.workflow_id,
                realtime_factor=args.factor,
                random_seed=args.seed,
                orders=orders,
            )
        except ValueError as exc:
            message = str(exc)
            if "multiple root nodes" in message or "no root node" in message:
                print(f"Simulation cannot start: {message}", file=sys.stderr)
                print(
                    "Tip: merge the workflow into a single connected flow before simulating.",
                    file=sys.stderr,
                )
            else:
                print(f"Simulation failed: {message}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Simulation failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.order_file:
        try:
            load_uploaded_order_source(args.order_file)
            use_uploaded_order_source()
        except UploadedOrderValidationError as exc:
            print(f"Order import failed: {exc}", file=sys.stderr)
            return 1

    from app.main import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
