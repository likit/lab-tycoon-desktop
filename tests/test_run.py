"""Tests for the application entrypoint."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import run


class RunEntryPointTests(unittest.TestCase):
    def test_order_file_imports_uploaded_source_before_launching_app(self) -> None:
        with patch("run.load_uploaded_order_source") as load_source, patch(
            "run.use_uploaded_order_source"
        ) as use_source, patch("app.main.run_app") as run_app:
            exit_code = run.main(["--order-file", "/tmp/orders.xlsx"])

        self.assertEqual(exit_code, 0)
        load_source.assert_called_once_with("/tmp/orders.xlsx")
        use_source.assert_called_once_with()
        run_app.assert_called_once_with()

    def test_simulation_with_order_file_uses_uploaded_orders(self) -> None:
        class FakeOrderSource:
            def list_orders(self) -> list[object]:
                return [object(), object()]

        with patch("run.load_uploaded_order_source", return_value=FakeOrderSource()) as load_source, patch(
            "run.use_uploaded_order_source"
        ) as use_source, patch("run.run_and_print_workflow_simulation") as run_sim:
            exit_code = run.main(["--simulate", "--order-file", "/tmp/orders.xlsx"])

        self.assertEqual(exit_code, 0)
        load_source.assert_called_once_with("/tmp/orders.xlsx")
        use_source.assert_called_once_with()
        run_sim.assert_called_once()
        kwargs = run_sim.call_args.kwargs
        self.assertEqual(kwargs["specimens_number"], 2)
        self.assertEqual(len(kwargs["orders"]), 2)


if __name__ == "__main__":
    unittest.main()
