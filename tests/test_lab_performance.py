"""Counts, order-weighted TAT medians, and excluded records."""
from datetime import datetime, timedelta
import unittest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.lab_performance import summarize, lis_performance, simulation_performance, PHASES
from app.demo_project import create_demo_project
from app.system.live_simulation import LiveSimulationSession
from app.system.models import LabOrder


class LabPerformanceTests(unittest.TestCase):
    def test_medians_exclude_incomplete_rejected_and_invalid_timelines(self):
        records = [dict(tests=2, excluded=False, times=[0, 2, 6, 10]),
                   dict(tests=1, excluded=False, times=[0, 4, 10, 16]),
                   dict(tests=3, excluded=True, times=[0, 2, 6, 10]),
                   dict(tests=1, excluded=False, times=[0, 2, None, None]),
                   dict(tests=1, excluded=False, times=[0, 5, 3, 9])]
        data = summarize(records)
        self.assertEqual(data['orders'], 5)
        self.assertEqual(data['tests'], 8)
        self.assertEqual(data['sample_size'], 2)
        self.assertEqual(list(data['medians'].values()), [3, 5, 5, 13])
        self.assertTrue(all(v is None for v in summarize([])['medians'].values()))

    def test_floor_demo_reports_completed_tat_in_minutes(self):
        project = create_demo_project()
        live = LiveSimulationSession(project.workflow, count=1, seed=7,
            equipment_types={e.instance_id:e.equipment_type for e in project.floor.equipment_instances})
        self.assertEqual(simulation_performance(live)['orders'], 0)
        live.engine.run()
        data = simulation_performance(live)
        self.assertEqual((data['orders'], data['tests'], data['sample_size']), (1, 1, 1))
        self.assertTrue(all(data['medians'][p] > 0 for p in PHASES))
        self.assertAlmostEqual(data['medians']['Lab TAT'], sum(data['medians'][p] for p in PHASES[:3]))

    def test_lis_database_timestamps_are_used_without_modification(self):
        from tests.test_simulation_lis import SimulationLISImportTests
        fixture = SimulationLISImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.convert()
        origin = datetime(2026, 1, 1)
        with Session(fixture.engine) as session, session.begin():
            orders = list(session.scalars(select(LabOrder).order_by(LabOrder.id)))
            for index, order in enumerate(orders):
                order.order_datetime = origin
                order.received_at = origin + timedelta(minutes=2)
                for item in order.order_items:
                    item.finished_at = origin + timedelta(minutes=7)
                order.approved_at = origin + timedelta(minutes=10)
            orders[1].rejected_at = origin + timedelta(minutes=1)
        data = lis_performance(fixture.engine)
        self.assertEqual(data['orders'], 2)
        self.assertEqual(data['sample_size'], 1)
        self.assertEqual(data['medians'], dict(zip(PHASES, [2, 5, 3, 10])))
