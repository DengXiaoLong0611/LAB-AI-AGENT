import tempfile
import unittest
from pathlib import Path

from test_lab_components import example_spec

from labagent.runner import ExperimentRunner
from labagent.store import LabStore, UnknownOperation


class RuntimeTests(unittest.TestCase):
    def test_operation_dedup_and_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task = store.create_task(example_spec())
            calls = []
            result = store.operate(
                task["id"],
                "op1",
                "device",
                {"x": 1},
                lambda: calls.append(1) or {"ok": True},
            )
            self.assertEqual(result, {"ok": True})
            store.operate(
                task["id"], "op1", "device", {"x": 1}, lambda: calls.append(2)
            )
            self.assertEqual(calls, [1])
            with self.assertRaises(ValueError):
                store.operate(task["id"], "op1", "device", {"x": 2}, lambda: None)
            with self.assertRaises(UnknownOperation):
                store.operate(
                    task["id"],
                    "op2",
                    "device",
                    {},
                    lambda: (_ for _ in ()).throw(TimeoutError("ACK lost")),
                )
            with self.assertRaises(UnknownOperation):
                store.operate(task["id"], "op2", "device", {}, lambda: calls.append(3))
            self.assertEqual(calls, [1])

    def test_simulation_converges_and_memories_are_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            config = example_spec()
            task = store.create_task(config)
            runner = ExperimentRunner(store)
            runner.run(task["id"])
            final = store.get_task(task["id"])
            self.assertEqual(final["status"], "succeeded", final)
            self.assertTrue(final["state"]["analysis"]["converged"])
            self.assertGreater(final["state"]["iteration"], 1)
            self.assertTrue(store.memories(config))
            self.assertFalse(store.memories(example_spec(project_id="other")))
            self.assertTrue(store.events(task["id"]))
            self.assertTrue(
                list((Path(directory) / "artifacts" / task["id"]).glob("*.svg"))
            )
            runner.close()

    def test_cancel_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task = store.create_task(example_spec())
            store.request_control(task["id"], "cancel")
            runner = ExperimentRunner(store)
            runner.run(task["id"])
            self.assertEqual(store.get_task(task["id"])["status"], "cancelled")
            runner.close()


class RecoveryTests(unittest.TestCase):
    def test_pause_restart_reuses_completed_measurements(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            config = example_spec()
            task_id = store.create_task(config)["id"]
            reads = []

            class PauseOnce(SimulatedDevices):
                def measure_hotwire(self, point, operation_id):
                    reads.append(operation_id)
                    result = super().measure_hotwire(point, operation_id)
                    if len(reads) == 1:
                        store.request_control(task_id, "pause")
                    return result

            runner = ExperimentRunner(store, PauseOnce)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "paused")
            runner.close()
            store.request_control(task_id, "resume")
            runner = ExperimentRunner(store, PauseOnce)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "succeeded")
            self.assertEqual(len(reads), len(set(reads)))
            runner.close()

    def test_unconfirmed_stop_needs_attention(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec())["id"]

            class StopFails(SimulatedDevices):
                def stop(self):
                    return {"completed": False}

            store.request_control(task_id, "cancel")
            runner = ExperimentRunner(store, StopFails)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "needs_attention")
            runner.close()

    def test_model_policy_uses_actual_tool_calls(self):
        from langchain_core.messages import AIMessage

        from labagent.agent import ModelPolicy

        class FakeModel:
            def bind_tools(self, definitions, **kwargs):
                self.names = [x["function"]["name"] for x in definitions]
                return self

            def invoke(self, messages):
                return AIMessage(
                    content="先设置均匀风场，再测量。",
                    tool_calls=[
                        {
                            "name": "set_fan_field",
                            "args": {"spec": [{"type": "uniform", "value": 4}]},
                            "id": "call-1",
                        }
                    ],
                )

        model = FakeModel()
        policy = ModelPolicy(model)
        decision = policy(
            {"phase": "initialize", "spec": example_spec().model_dump(), "iteration": 0}
        )
        self.assertEqual(decision.tool, "set_fan_field")
        self.assertEqual(model.names, ["set_fan_field"])


class CompletionRaceTests(unittest.TestCase):
    def test_cancel_during_final_analysis_is_honored(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec(tolerance_mps=2))["id"]
            stops = []

            class Device(SimulatedDevices):
                def stop(self):
                    stops.append(True)
                    return super().stop()

            class Runner(ExperimentRunner):
                def analyze(self, task_id, state, config):
                    result = super().analyze(task_id, state, config)
                    store.request_control(task_id, "cancel")
                    return result

            runner = Runner(store, Device)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "cancelled")
            self.assertEqual(stops, [True])
            runner.close()

    def test_crash_between_device_journal_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec(tolerance_mps=2))["id"]

            class CrashOnce(ExperimentRunner):
                def device_call(self, *args, **kwargs):
                    result = super().device_call(*args, **kwargs)
                    if args[2] == "set_fan_field":
                        raise KeyboardInterrupt("crash after device completion")
                    return result

            runner = CrashOnce(store)
            with self.assertRaises(KeyboardInterrupt):
                runner.run(task_id)
            runner.close()
            runner = ExperimentRunner(store)
            runner.run(task_id)
            final = store.get_task(task_id)
            self.assertEqual(final["status"], "succeeded")
            first_analysis = next(
                e["payload"] for e in store.events(task_id) if e["kind"] == "analysis"
            )
            self.assertGreater(first_analysis["points"][0]["mean_mps"], 4)
            runner.close()


class StopRecoveryTests(unittest.TestCase):
    def test_finished_experiment_stops_devices(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec(tolerance_mps=2))["id"]
            stops = []

            class Device(SimulatedDevices):
                def stop(self):
                    stops.append(1)
                    return super().stop()

            runner = ExperimentRunner(store, Device)
            runner.run(task_id)
            self.assertEqual(stops, [1])
            runner.close()

    def test_unconfirmed_cancel_resumes_stop_not_experiment(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec())["id"]

            class Broken(SimulatedDevices):
                def stop(self):
                    return {"completed": False}

            runner = ExperimentRunner(store, Broken)
            store.request_control(task_id, "cancel")
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "needs_attention")
            runner.close()
            store.request_control(task_id, "resume")
            runner = ExperimentRunner(store)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "cancelled")
            self.assertFalse(store.operations(task_id))
            runner.close()


class StopRaceTests(unittest.TestCase):
    def test_cancellation_during_recovered_stop_wins(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec())["id"]
            store.update_task(
                task_id, status="needs_attention", pending_stop="succeeded"
            )
            store.request_control(task_id, "resume")

            class Device(SimulatedDevices):
                def stop(self):
                    store.request_control(task_id, "cancel")
                    return {"completed": True}

            runner = ExperimentRunner(store, Device)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "cancelled")
            runner.close()

    def test_cancellation_survives_failed_recovered_stop(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec())["id"]
            store.update_task(
                task_id, status="needs_attention", pending_stop="succeeded"
            )
            store.request_control(task_id, "resume")

            class Device(SimulatedDevices):
                def stop(self):
                    store.request_control(task_id, "cancel")
                    return {"completed": False}

            runner = ExperimentRunner(store, Device)
            runner.run(task_id)
            runner.close()
            store.request_control(task_id, "resume")
            runner = ExperimentRunner(store)
            runner.run(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "cancelled")
            runner.close()

    def test_terminal_analysis_saved_before_stop(self):
        from labagent.tools.devices import SimulatedDevices

        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec(tolerance_mps=2))["id"]

            class Device(SimulatedDevices):
                def stop(self):
                    self.saved = store.get_task(task_id)["state"].get("analysis")
                    return super().stop()

            devices = []

            def factory(config):
                device = Device(config)
                devices.append(device)
                return device

            runner = ExperimentRunner(store, factory)
            runner.run(task_id)
            self.assertTrue(devices[0].saved and devices[0].saved["converged"])
            runner.close()


class ControlTransitionTests(unittest.TestCase):
    def test_worker_transition_does_not_overwrite_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec())["id"]
            store.request_control(task_id, "pause")
            store.request_control(task_id, "cancel")
            self.assertFalse(store.pause_at_boundary(task_id))
            store.mark_running(task_id)
            self.assertEqual(store.get_task(task_id)["status"], "cancel_requested")


class ReconnectTests(unittest.TestCase):
    def test_reconnection_failure_keeps_unconfirmed_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task_id = store.create_task(example_spec())["id"]
            store.update_task(
                task_id, status="needs_attention", pending_stop="cancelled"
            )
            store.request_control(task_id, "resume")

            def unavailable(config):
                raise ConnectionError("gateway unavailable")

            runner = ExperimentRunner(store, unavailable)
            runner.run(task_id)
            task = store.get_task(task_id)
            self.assertEqual(task["status"], "needs_attention")
            self.assertEqual(task["pending_stop"], "cancelled")
            runner.close()
