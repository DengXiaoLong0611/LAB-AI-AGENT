"""后台实验执行与 LangGraph checkpoint。所有设备调用经过操作日志。"""

import sqlite3
import time
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agent import TOOL_MODELS, allowed_tools, baseline_policy, make_deepseek_policy
from .models import ExperimentSpec, ToolDecision
from .specs import expand_field, normalize_field
from .store import TERMINAL, LabStore, UnknownOperation
from .tools.analysis import analyze_samples, comparison_svg, evaluate_field
from .tools.control import FeedbackStrategy
from .tools.devices import SimulatedDevices


class CancelledExperiment(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class ExperimentState(TypedDict, total=False):
    spec: dict
    task_id: str
    phase: str
    iteration: int
    step: int
    current_spec: list[dict]
    proposed_spec: list[dict]
    flap_setting: dict
    measurements: list[dict]
    analysis: dict
    history: list[dict]
    memories: list[dict]
    decision: dict
    status: str
    stop_reason: str
    artifacts: list[str]


class ExperimentRunner:
    def __init__(
        self, store: LabStore, device_factory=SimulatedDevices, policy_factory=None
    ):
        self.store = store
        self.device_factory = device_factory
        self.policy_factory = policy_factory
        self.connection = sqlite3.connect(
            store.directory / "checkpoints.sqlite3", check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self.connection)
        self.strategy = FeedbackStrategy()
        self.active_task_id = None
        self.device = None
        self.started_at = None

    def close(self):
        self.connection.close()

    def run(self, task_id):
        task = self.store.get_task(task_id)
        if task["status"] in TERMINAL:
            return
        config = ExperimentSpec.model_validate(task["spec"])
        self.active_task_id = task_id
        started = time.monotonic()
        self.started_at = started
        elapsed_before = task["elapsed_seconds"]
        deadline = started + max(0, config.max_seconds - elapsed_before)
        graph = None
        invocation = {"configurable": {"thread_id": task_id}, "recursion_limit": 3000}
        try:
            self.device = self.device_factory(config)
            if config.flap and not self.device.supports_flap:
                raise ValueError("实验要求翼板，但当前设备适配器不支持")
            if not self.device.simulated and (
                config.device_id == "simulator"
                or config.calibration_version == "simulation-v1"
            ):
                raise ValueError(
                    "真实设备必须明确声明 device_id 和 calibration_version"
                )
            if task.get("pending_stop"):
                pending = (
                    "cancelled"
                    if task["status"] == "cancel_requested"
                    else task["pending_stop"]
                )
                self.finish_with_stop(task_id, pending)
                return
            policy = (
                self.policy_factory(config)
                if self.policy_factory
                else (
                    make_deepseek_policy()
                    if config.mode == "deepseek"
                    else baseline_policy
                )
            )
            graph = self.build_graph(task_id, config, policy, deadline)
            snapshot = graph.get_state(invocation)
            if snapshot.values and isinstance(self.device, SimulatedDevices):
                self.device.restore(snapshot.values)
            self.store.mark_running(task_id)
            if snapshot.values:
                input_value = Command(resume=True) if snapshot.interrupts else None
            else:
                input_value = {
                    "spec": config.model_dump(),
                    "task_id": task_id,
                    "phase": "initialize",
                    "iteration": 0,
                    "step": 0,
                    "history": [],
                    "artifacts": [],
                    "memories": self.store.memories(config, limit=5),
                    "status": "running",
                }
            result = graph.invoke(input_value, invocation)
            if "__interrupt__" in result:
                return
            status = result.get("status", "failed")
            self.store.prepare_stop(task_id, status, state=dict(result))
            if not self.confirm_stop(task_id):
                return
            status = self.store.finish_stop(task_id)
            self.store.event(
                task_id,
                "experiment_finished",
                {"status": status, "reason": result.get("stop_reason")},
            )
        except CancelledExperiment:
            self.finish_with_stop(task_id, "cancelled")
        except BudgetExceeded:
            self.finish_with_stop(task_id, "exhausted", "实验时间预算已用完")
        except UnknownOperation as error:
            self.store.update_task(task_id, status="needs_attention", error=str(error))
            self.store.event(task_id, "needs_attention", {"error": str(error)})
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if self.device is None and (
                task.get("pending_stop") or self.store.operations(task_id)
            ):
                self.store.prepare_stop(
                    task_id, task.get("pending_stop") or "failed", error=message
                )
                self.store.stop_unknown(
                    task_id, f"设备重连失败，停止仍待确认，{message}"
                )
            else:
                self.finish_with_stop(task_id, "failed", message)
            self.store.event(
                task_id,
                "experiment_error",
                {"error": str(error), "type": type(error).__name__},
            )
        finally:
            if graph:
                snapshot = graph.get_state(invocation)
                if (
                    snapshot.values
                    and self.store.get_task(task_id)["status"] not in TERMINAL
                ):
                    self.store.update_task(task_id, state=dict(snapshot.values))
            self.store.update_task(
                task_id, elapsed_seconds=elapsed_before + time.monotonic() - started
            )
            final = self.store.get_task(task_id)
            if final["status"] in TERMINAL and self.device is not None:
                final_state = {
                    **final["state"],
                    "status": final["status"],
                    "stop_reason": final["error"] or final["state"].get("stop_reason"),
                }
                self.remember(task_id, final_state, config)
            self.active_task_id, self.device, self.started_at = None, None, None

    def confirm_stop(self, task_id):
        try:
            if self.device is not None:
                result = self.device.stop()
                self.store.event(task_id, "device_stop", result)
                if not isinstance(result, dict) or result.get("completed") is not True:
                    raise UnknownOperation("设备停止尚未确认")
        except Exception as stop_error:
            self.store.stop_unknown(task_id, f"停止未确认，{stop_error}")
            self.store.event(task_id, "needs_attention", {"error": str(stop_error)})
            return False
        return True

    def finish_with_stop(self, task_id, status, error=None):
        self.store.prepare_stop(task_id, status, error=error)
        if not self.confirm_stop(task_id):
            return
        status = self.store.finish_stop(task_id)
        self.store.event(
            task_id, "experiment_finished", {"status": status, "error": error}
        )

    def gate(self, task_id, deadline):
        status = self.store.get_task(task_id)["status"]
        if status == "cancel_requested":
            raise CancelledExperiment()
        if status == "pause_requested" and self.store.pause_at_boundary(task_id):
            self.store.event(
                task_id, "paused", {"message": "已在工具边界暂停，设备保持当前设置"}
            )
            interrupt({"reason": "用户请求暂停"})
        if self.store.get_task(task_id)["status"] == "cancel_requested":
            raise CancelledExperiment()
        if time.monotonic() >= deadline:
            raise BudgetExceeded()

    def build_graph(self, task_id, config, policy, deadline):
        def reason(state):
            self.gate(task_id, deadline)
            self.store.update_task(task_id, state=dict(state))
            decision = ToolDecision.model_validate(policy(state))
            if decision.tool not in allowed_tools(state):
                raise ValueError("当前实验阶段不允许该工具")
            TOOL_MODELS[decision.tool].model_validate(decision.arguments)
            self.store.event(task_id, "decision", decision.model_dump())
            return {"decision": decision.model_dump()}

        def act(state):
            self.gate(task_id, deadline)
            decision = ToolDecision.model_validate(state["decision"])
            args = (
                TOOL_MODELS[decision.tool]
                .model_validate(decision.arguments)
                .model_dump(by_alias=True)
            )
            tool = decision.tool
            operation_id = f"{task_id}:{state['step']}:{tool}"
            updates = {}
            if tool == "set_fan_field":
                field = expand_field(args["spec"], config)
                if state.get("current_spec"):
                    old = expand_field(state["current_spec"], config)
                    if any(
                        abs(a - b) > config.max_change_mps + 1e-9
                        for arow, brow in zip(field, old)
                        for a, b in zip(arow, brow)
                    ):
                        raise ValueError("风场单轮变化超过 max_change_mps")
                spec = normalize_field(args["spec"])
                result = self.device_call(
                    task_id,
                    operation_id,
                    tool,
                    {"spec": spec},
                    lambda: self.device.set_fan_field(spec, operation_id),
                )
                if isinstance(self.device, SimulatedDevices):
                    self.device.field = field
                updates = {
                    "current_spec": spec,
                    "phase": "scan",
                    "iteration": state["iteration"] + 1,
                    "measurements": [],
                    "proposed_spec": [],
                }
            elif tool == "set_flap":
                if not self.device.supports_flap:
                    raise ValueError("翼板工具不可用")
                result = self.device_call(
                    task_id,
                    operation_id,
                    tool,
                    args,
                    lambda: self.device.set_flap(args, operation_id),
                )
                if isinstance(self.device, SimulatedDevices):
                    self.device.flap = args
                updates = {
                    "flap_setting": args,
                    "phase": "initialize" if state["iteration"] == 0 else "scan",
                }
                if state["iteration"]:
                    updates["iteration"] = state["iteration"] + 1
                    updates["measurements"] = []
            elif tool == "scan_field":
                result = self.scan(task_id, operation_id, config, deadline)
                updates = {"measurements": result["measurements"], "phase": "analyze"}
            elif tool == "analyze_field":
                result = self.analyze(task_id, state, config)
                updates = {
                    "analysis": result,
                    "phase": "adjust",
                    "artifacts": list(
                        dict.fromkeys(
                            state.get("artifacts", [])
                            + [result["json_ref"], result["plot_ref"]]
                        )
                    ),
                }
                if result["converged"]:
                    updates.update(
                        status="succeeded", stop_reason="所有测点满足目标误差"
                    )
                elif state["iteration"] >= config.max_iterations:
                    updates.update(status="exhausted", stop_reason="达到最大实验轮数")
            elif tool == "propose_adjustment":
                result = {
                    "spec": self.strategy.propose(
                        config,
                        state["current_spec"],
                        state["analysis"]["points"],
                        gain=args["gain"],
                    ),
                    "strategy": "bounded-proportional-v1",
                }
                updates = {"proposed_spec": result["spec"], "phase": "apply"}
            else:
                result = {"reason": args["reason"]}
                updates = {"status": "exhausted", "stop_reason": args["reason"]}
            if tool in {"analyze_field", "propose_adjustment", "finish"}:
                self.store.event(
                    task_id,
                    "tool_completed",
                    {"operation_id": operation_id, "tool": tool, "result": result},
                )
            history = state.get("history", []) + [
                {
                    "id": operation_id,
                    "tool": tool,
                    "arguments": args,
                    "summary": decision.summary,
                    "result": result,
                }
            ]
            updates.update(history=history[-6:], step=state["step"] + 1)
            # 额外上限覆盖模型反复调用翼板或分析的情况。
            if (
                updates["step"] >= config.max_iterations * 8 + 8
                and "status" not in updates
            ):
                updates.update(status="exhausted", stop_reason="达到工具调用预算")
            return updates

        graph = StateGraph(ExperimentState)
        graph.add_node("reason", reason)
        graph.add_node("act", act)
        graph.add_edge(START, "reason")
        graph.add_edge("reason", "act")
        graph.add_conditional_edges(
            "act",
            lambda s: "end" if s.get("status") in TERMINAL else "next",
            {"end": END, "next": "reason"},
        )
        return graph.compile(checkpointer=self.checkpointer)

    def device_call(self, task_id, operation_id, tool, params, call):
        def checked():
            result = call()
            if not isinstance(result, dict) or result.get("completed") is not True:
                raise ValueError("设备必须确认实际完成 completed=true")
            return result

        def recovered(op_id):
            result = self.device.lookup_operation(op_id)
            if result is not None and result.get("completed") is not True:
                raise UnknownOperation(f"{op_id} 设备尚未确认完成")
            return result

        return self.store.operate(
            task_id, operation_id, tool, params, checked, recovered
        )

    def scan(self, task_id, operation_id, config, deadline):
        # 每次恢复都重新等待稳定，已完成的移动和采样由 operation_id 去重。
        end = time.monotonic() + config.settle_seconds
        while time.monotonic() < end:
            self.gate(task_id, deadline)
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        measurements = []
        for index, point in enumerate(config.points):
            self.gate(task_id, deadline)
            move_id = f"{operation_id}:{point.id}:move"
            self.device_call(
                task_id,
                move_id,
                "move_probe",
                point.model_dump(),
                lambda: self.device.move_probe(point, move_id),
            )
            self.gate(task_id, deadline)
            read_id = f"{operation_id}:{point.id}:measure"

            def read():
                raw = self.device.measure_hotwire(point, read_id)
                return self.save_measurement(task_id, read_id, config, point, raw)

            def recover_measurement(op_id):
                raw = self.device.lookup_operation(op_id)
                return (
                    None
                    if raw is None
                    else self.save_measurement(task_id, read_id, config, point, raw)
                )

            measurement = self.store.operate(
                task_id,
                read_id,
                "measure_hotwire",
                {
                    "point": point.model_dump(),
                    "sample_count": config.sample_count,
                    "sample_rate_hz": config.sample_rate_hz,
                },
                read,
                recover_measurement,
            )
            measurements.append(measurement)
            self.store.event(
                task_id,
                "scan_progress",
                {
                    "completed": index + 1,
                    "total": len(config.points),
                    "point_id": point.id,
                },
            )
        return {"measurements": measurements}

    def save_measurement(self, task_id, read_id, config, point, raw):
        if (
            raw.get("quality") != "good"
            or raw.get("point_id") != point.id
            or raw.get("calibration_version") != config.calibration_version
            or raw.get("x_mm") != point.x_mm
            or raw.get("y_mm") != point.y_mm
            or not raw.get("timestamp")
        ):
            raise ValueError("热线质量、位置、时间或标定信息不匹配")
        samples = raw["samples_mps"]
        if (
            len(samples) != config.sample_count
            or raw["sample_rate_hz"] != config.sample_rate_hz
        ):
            raise ValueError("热线实际采样配置与请求不匹配")
        statistics = analyze_samples(samples, sample_rate_hz=raw["sample_rate_hz"])
        filename = read_id.replace(":", "_") + ".json"
        ref = self.store.save_artifact(task_id, filename, raw)
        return {
            **statistics,
            "point_id": point.id,
            "x_mm": point.x_mm,
            "y_mm": point.y_mm,
            "raw_ref": ref,
            "timestamp": raw["timestamp"],
            "simulated": self.device.simulated,
            "calibration_version": config.calibration_version,
        }

    def analyze(self, task_id, state, config):
        result = evaluate_field(config, state["measurements"])
        stem = f"iteration-{state['iteration']:03d}"
        result["plot_ref"] = self.store.save_artifact(
            task_id, stem + ".svg", comparison_svg(result)
        )
        result["json_ref"] = stem + "-analysis.json"
        self.store.save_artifact(task_id, result["json_ref"], result)
        self.store.event(
            task_id, "analysis", {"iteration": state["iteration"], **result}
        )
        return result

    def remember(self, task_id, state, config):
        content = {
            "target": config.model_dump(),
            "current_spec": state.get("current_spec"),
            "analysis": state.get("analysis"),
            "artifacts": state.get("artifacts", []),
            "status": state.get("status"),
            "reason": state.get("stop_reason"),
            "simulated": self.device.simulated,
            "source_task_id": task_id,
        }
        self.store.remember(task_id, "experiment", content, status="recorded")
        self.store.remember(
            task_id,
            "experience",
            {
                "claim": "这组控制参数的效果见关联实测记录，适用性待复核。",
                "conditions": {
                    "device_id": config.device_id,
                    "calibration_version": config.calibration_version,
                    "simulated": self.device.simulated,
                },
                "source_task_id": task_id,
                "analysis_ref": (state.get("analysis") or {}).get("json_ref"),
                "control_spec": state.get("current_spec"),
                "outcome": state.get("status"),
            },
        )
