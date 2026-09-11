"""Application entrypoint."""

from __future__ import annotations

import argparse
import sys

from app.system.order_sources import UploadedOrderValidationError, load_uploaded_order_source, use_uploaded_order_source
from app.system.governance import (
    export_governance_consultations_to_csv,
    export_governance_consultations_to_json,
    export_simulation_result_to_json,
)
from app.system.simulation import load_workflow_document_for_simulation, run_and_print_workflow_simulation


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
    parser.add_argument(
        "--export-governance-log",
        type=str,
        default=None,
        metavar="PATH",
        help="Export governance consultation records to CSV or JSON after simulation.",
    )
    parser.add_argument(
        "--export-simulation-result",
        type=str,
        default=None,
        metavar="PATH",
        help="Export the structured simulation result as JSON after simulation.",
    )
    parser.add_argument(
        "--debug-governance",
        action="store_true",
        help="Print governance diagnostics before and after simulation.",
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
            if args.debug_governance:
                workflow_document = load_workflow_document_for_simulation(
                    workflow_id=args.workflow_id,
                )
                _print_governance_debug_info(workflow_document.workflow)
            result = run_and_print_workflow_simulation(
                specimens_number=specimens_number,
                workflow_id=args.workflow_id,
                realtime_factor=args.factor,
                random_seed=args.seed,
                orders=orders,
                debug_governance=args.debug_governance,
            )
            if args.debug_governance:
                _print_governance_debug_result(result.governance_consultations)
            if args.export_governance_log:
                _export_governance_consultations(
                    result.governance_consultations,
                    args.export_governance_log,
                )
            if args.export_simulation_result:
                export_simulation_result_to_json(result, args.export_simulation_result)
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


def _export_governance_consultations(consultations: list[object], path: str) -> None:
    """Export governance consultations using the format implied by the file suffix."""

    if path.lower().endswith(".csv"):
        export_governance_consultations_to_csv(consultations, path)
        return

    export_governance_consultations_to_json(consultations, path)


def _print_governance_debug_info(workflow: object) -> None:
    """Print governance-related workflow metadata before simulation."""

    print("Governance debug: workflow inspection")
    for node in workflow.graph.iter_nodes():
        governance_events = node.metadata.get("governance_events")
        can_emit = _node_can_emit_governance_events(node)
        print(
            "- "
            f"node_id={node.node_id} "
            f"widget.name={node.widget.name} "
            f"widget.node_type={node.widget.node_type} "
            f"metadata.governance_events={governance_events} "
            f"can_emit_governance_events={can_emit}"
        )


def _print_governance_debug_result(consultations: list[object]) -> None:
    """Print a short governance summary after simulation."""

    print(f"Governance consultations total: {len(consultations)}")
    if not consultations:
        return

    print("Governance consultations preview:")
    for consultation in consultations[:5]:
        if hasattr(consultation, "to_dict") and callable(getattr(consultation, "to_dict")):
            payload = consultation.to_dict()
        elif isinstance(consultation, dict):
            payload = consultation
        else:
            payload = {"value": consultation}
        print(f"- {payload}")


def _node_can_emit_governance_events(node: object) -> bool:
    """Mirror the current simulation rule for governance event emission."""

    metadata = getattr(node, "metadata", {})
    governance_events = metadata.get("governance_events")
    if isinstance(governance_events, list) and governance_events:
        return True
    node_type = getattr(getattr(node, "widget", None), "node_type", None)
    if node_type in {"specimen registration", "Evaluation", "AI agent"}:
        return True
    return metadata.get("plugin") == "duplicate_order_detector"


if __name__ == "__main__":
    raise SystemExit(main())
