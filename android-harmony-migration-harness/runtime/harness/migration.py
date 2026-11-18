#!/usr/bin/env python3
"""Migration Harness: joint review, managed execution and state-level acceptance."""
import argparse
import json
import sys
from pathlib import Path

from contract import FORMAT, validate_contract, profile_data
from project import readiness as project_readiness, verify as verify_project, reconcile as reconcile_project
from engine import Harness, read_json, runtime
from report import write_report
from review_html import write_review_packet
from continuation import advice, command_error
from closeout import record as record_closeout
from test_plan import compile_contract, summary as plan_summary
from handoff import write_handoff
from session import record as record_session, end_check, recovery
from requirements import compile_plan
from onboarding import discover
from agent_adapter import inspect_adapter
from agent_runner import drive as drive_agent, reconcile as reconcile_agent, inspect_control


def project_result(experiment, result, status=None, continuation=None):
    status = experiment.status() if status is None else status
    continuation = advice(experiment, status) if continuation is None else continuation
    result["continuation"] = continuation
    result["delivery"] = continuation.get("delivery")
    result["developer_handoff"] = continuation["developer_handoff"]
    lifecycle = recovery(experiment, status, continuation)
    if lifecycle["journal_error"] or lifecycle["agent_control"]["journal_error"] or lifecycle["agent_control"]["pending_turn"]:
        result["continuation"] = {**continuation, "status": "must_continue", "can_end_turn": False,
                                  "next_action": lifecycle["next_action"], "pending_actions": lifecycle["pending_actions"]}
    result["lifecycle"] = {key: lifecycle[key] for key in ("state", "session", "active_attempts", "next_action",
        "last_progress_at", "external_holds", "invalid_holds", "background_worker", "journal_error", "checkpoint_current", "agent_control")}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harness.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    capture = commands.add_parser("capture-screen", help="Capture a real screen and layout without a migration run")
    capture.add_argument("--hdc", required=True)
    capture.add_argument("--target")
    capture.add_argument("--bundle", required=True, help="Required foreground application bundle")
    capture.add_argument("--output-dir", required=True, help="New directory for immutable capture evidence")
    visual = commands.add_parser("compare-visual", help="Compute real SSIM/MAE; image checks do not grant delivery")
    visual.add_argument("--baseline", required=True)
    visual.add_argument("--harmony-capture", required=True)
    visual.add_argument("--state-id", required=True)
    visual.add_argument("--output-dir", required=True)
    visual.add_argument("--regions", help="JSON viewport, masks and required_rois")
    visual.add_argument("--diagnostic", action="store_true")
    visual.add_argument("--resize-to-baseline", action="store_true",
                        help="Explicit diagnostic normalization; requires --diagnostic")
    check = commands.add_parser("agent-check", help="Inspect an Agent integration without executing it")
    check.add_argument("--adapter", required=True)
    check.add_argument("--require-enforcement", action="store_true")
    drive = commands.add_parser("agent-drive", help="Own Agent turns and enforce lifecycle end checks")
    drive.add_argument("--run-dir", required=True)
    drive.add_argument("--adapter", required=True)
    drive.add_argument("--operation-id", required=True)
    drive.add_argument("--max-turns", type=int, default=20)
    drive.add_argument("--max-no-progress", type=int, default=3)
    drive.add_argument("--max-reconnects", type=int, default=2)
    drive.add_argument("--max-seconds", type=int, default=1800)
    drive.add_argument("--resume", action="store_true")
    control = commands.add_parser("agent-status", help="Read the controller and unresolved Agent attempt")
    control.add_argument("--run-dir", required=True)
    reconcile = commands.add_parser("agent-reconcile", help="Reconcile an original Agent attempt without replaying it")
    reconcile.add_argument("--run-dir", required=True)
    reconcile.add_argument("--operation-id", required=True)
    reconcile.add_argument("--input", required=True)
    discovery = commands.add_parser("project-discover", help="Discover modules/toolchain and write a reviewable onboarding draft")
    discovery.add_argument("--root", required=True)
    discovery.add_argument("--project-id", required=True)
    discovery.add_argument("--deveco-home")
    discovery.add_argument("--output", required=True)
    session = commands.add_parser("session", help="Persist the current host request and lifecycle events")
    location = session.add_mutually_exclusive_group(required=True)
    location.add_argument("--run-dir")
    location.add_argument("--journal", help="Preparation journal before a run exists")
    session.add_argument("--operation-id", required=True)
    session.add_argument("--input", required=True)
    for name in ("project-context", "project-verify", "project-reconcile"):
        child = commands.add_parser(name, help="Project onboarding without creating a migration run")
        child.add_argument("--profile", required=True)
        child.add_argument("--roots", required=True, help="JSON object mapping root names to project directories")
        child.add_argument("--demo", action="store_true")
        if name == "project-verify":
            child.add_argument("--capabilities", nargs="+", required=True)
        if name == "project-reconcile":
            child.add_argument("--capability", required=True)
            child.add_argument("--action", choices=("accept", "retry"), required=True)
            child.add_argument("--reason", required=True)
            child.add_argument("--evidence", nargs="+", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("--contract", required=True)
    init = commands.add_parser("start", aliases=["init"], help="Create a joint review migration run")
    init.add_argument("--contract", required=True)
    init.add_argument("--run-dir", required=True)
    plan = commands.add_parser("plan", help="Compile selected scenarios and methods into a new contract")
    plan.add_argument("--contract", required=True)
    plan.add_argument("--input", required=True, help="Change-driven test plan JSON")
    plan.add_argument("--output", required=True, help="New contract file; existing files are never overwritten")
    for name in ("status", "context", "review-packet", "coverage", "metrics", "report", "run", "record",
                 "approve", "retry", "reconcile", "extend-budget", "revise", "verify", "inspect", "attach", "timer", "closeout", "handoff", "resume", "end-check", "execution-plan"):
        child = commands.add_parser(name)
        child.add_argument("--run-dir", required=True)
        if name in {"status", "resume", "verify"}:
            child.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        if name in {"run", "record", "approve", "retry", "reconcile", "extend-budget", "revise", "attach", "timer", "closeout", "end-check"}:
            child.add_argument("--operation-id", required=True)
        if name == "run":
            child.add_argument("--limit", type=int, default=20)
            child.add_argument("--tasks", nargs="+")
            child.add_argument("--points", nargs="+")
            child.add_argument("--problem-ids", nargs="+", help="Link repair builds/tests to existing diagnosed problems")
        if name in {"record", "approve", "closeout", "handoff", "end-check"}:
            child.add_argument("--input", required=True)
        if name in {"inspect", "attach", "retry", "reconcile", "extend-budget"}:
            child.add_argument("--task", required=True)
        if name in {"attach", "retry", "reconcile", "extend-budget"}:
            child.add_argument("--evidence", nargs="+", required=True)
        if name in {"retry", "revise", "reconcile", "extend-budget"}:
            child.add_argument("--reason", required=True)
        if name == "reconcile":
            child.add_argument("--action", choices=("accept", "retry"), default="accept")
        if name == "extend-budget":
            child.add_argument("--additional", type=int, required=True)
        if name == "revise":
            child.add_argument("--contract", required=True)
        if name == "timer":
            child.add_argument("--id", required=True)
            child.add_argument("--phase", required=True)
            child.add_argument("--action", choices=("start", "stop"), required=True)
    args = parser.parse_args(argv)
    if args.command == "start":
        args.command = "init"
    experiment = None
    projected = False
    try:
        if args.command == "capabilities":
            result = {"format": FORMAT, "opt_in": False, "implementation_id": "migration-harness",
                "entrypoint": "scripts/harness.py", "contract_format": FORMAT,
                "default_workflow": "joint_review_state_acceptance",
                "commands": sorted(commands.choices),
                "joint_review": True, "final_human_review": True, "state_coverage": True,
                "change_driven_test_plan": True, "test_plan_version": 1,
                "partial_point_results": True, "project_context": True, "selective_task_run": True,
                "independent_point_resume": True, "reconcile_completed_attempt": True,
                "review_resource_bundles": True, "failure_budget_per_input": True,
                "joint_review_html": True,
                "verified_project_capabilities": True, "project_capabilities_version": 1,
                "guarded_device_suites": True, "network_transport_assertions": True,
                "detector_fixture_checks": True, "explicit_build_output_manifest": True,
                "classified_problem_history": True, "approval_wait_autoclose": True,
                "continuation_advice": True,
                "agent_sessions": True, "end_check_receipts": True, "read_only_recovery": True,
                "host_end_hook": "explicit_invocation_required", "automatic_host_hook": False,
                "managed_agent_runner": True, "agent_adapter_protocol": "harness-agent-adapter/1",
                "agent_event_protocol": "harness-agent-event/1",
                "agent_integration_inspection": True, "agent_turn_reconciliation": True,
                "agent_enforcement_modes": ["managed_runner", "external_host_unverified", "cooperative"],
                "execution_presets": True, "execution_preparation_plan": True,
                "project_discovery": True, "stable_project_identity": True,
                "declarative_device_suites": True, "device_suite_step_recovery": True,
                "scoped_delivery_closeout": True,
                "developer_handoff_html": True, "developer_handoff_version": 1,
                "deterministic_visual_comparison": True, "visual_comparison_version": 1,
                "native_screen_capture": True, "visual_dependency_evidence": True,
                "real_visual_binding_required": True, "plan_visual_bindings": True,
                "native_screen_recording": False, "knowledge_identity_bridge": False,
                "native_android_reference": False, "device_screen_timeout_preparation": False,
                "evidence_preserving_worktree_relocation": False,
                "native_harmony_navigation": False, "bounded_paid_action_authorization": False,
                "visual_metrics": {"metric": "masked-windowed-ssim+mae", "minimum_ssim": .85,
                                   "maximum_normalized_mae": .12},
                "handoff_project_directory": "androidtoharmonyharnessdoc",
                "timing": ["executor_by_kind", "adapter_reported_phases", "agent_wall_spans", "session_lifecycle"],
                "evaluations": "advisory_adapter_protocol", "huawei_provider": "not_configured",
                "result_cache": False, "parallel_device_execution": False, "background_worker": False}
        elif args.command == "capture-screen":
            from capture import capture_screen
            result = capture_screen(args.hdc, args.output_dir, target=args.target, bundle=args.bundle)
        elif args.command == "compare-visual":
            from visual_cli import compare_capture
            result = compare_capture(args.baseline, args.harmony_capture, args.output_dir, state_id=args.state_id,
                                     regions=read_json(args.regions) if args.regions else None,
                                     diagnostic=args.diagnostic, resize_to_baseline=args.resize_to_baseline)
        elif args.command == "agent-check":
            result = inspect_adapter(args.adapter, args.require_enforcement)
        elif args.command in {"agent-drive", "agent-reconcile", "agent-status"}:
            if args.command == "agent-drive":
                result = drive_agent(args.run_dir, args.adapter, args.operation_id, args.max_turns,
                                     args.max_no_progress, args.max_reconnects, args.max_seconds, args.resume)
            elif args.command == "agent-reconcile":
                result = reconcile_agent(args.run_dir, args.operation_id, read_json(args.input))
            else:
                result = inspect_control(args.run_dir)
            experiment = Harness(args.run_dir)
        elif args.command == "project-discover":
            result = discover(args.root, args.project_id, args.deveco_home)
            output = Path(args.output).absolute()
            with output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            result["draft_path"] = str(output)
        elif args.command == "session":
            if args.run_dir:
                experiment = Harness(args.run_dir)
                with experiment.locked():
                    result = record_session(experiment.root, args.operation_id, read_json(args.input), experiment)
            else:
                directory = Path(args.journal).resolve()
                directory.mkdir(parents=True, exist_ok=True)
                with runtime.file_lock(directory / "agent-session.lock", timeout=0) as acquired:
                    if not acquired:
                        raise ValueError("Session journal is busy")
                    result = record_session(directory, args.operation_id, read_json(args.input))
        elif args.command in {"project-context", "project-verify", "project-reconcile"}:
            path = Path(args.profile).resolve(strict=True)
            roots = {key: str(Path(value).expanduser().resolve(strict=True)) for key, value in read_json(args.roots).items()}
            owner = next((key for key, value in roots.items() if Path(value) in path.parents), None)
            if owner is None:
                raise ValueError("Project profile must belong to a declared root")
            profile, _ = profile_data({"roots": roots, "project_profile": {"root": owner,
                "path": str(path.relative_to(roots[owner])), "locator": "Project onboarding profile"}})
            if args.command == "project-reconcile":
                result = reconcile_project(profile, roots, path, args.capability, args.reason, args.evidence, args.action, args.demo)
            elif args.command == "project-verify":
                result = verify_project(profile, roots, path, args.capabilities, args.demo)
            else:
                result = project_readiness(profile, roots, path, args.demo)
        elif args.command == "plan":
            path = Path(args.contract).resolve(strict=True)
            planned = compile_contract(read_json(path), read_json(Path(args.input)))
            planned = validate_contract(planned, path.parent)
            output = Path(args.output).absolute()
            with output.open("x", encoding="utf-8") as stream:
                json.dump(planned, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            result = {"status": "plan_compiled", "contract": str(output), "test_plan": plan_summary(planned)}
        elif args.command == "audit":
            path = Path(args.contract).resolve(strict=True)
            raw = read_json(path)
            gaps = []
            for field in ("states", "review_scopes", "project_profile"):
                if not raw.get(field):
                    gaps.append("Missing " + field)
            for item in raw.get("acceptance", []):
                if item.get("kind") in {"device", "visual"} and not item.get("state_id"):
                    gaps.append("Native point lacks state_id: " + str(item.get("id")))
            if raw.get("format") != FORMAT:
                gaps.append("Task-level evidence cannot replace joint approval or point results")
            else:
                try:
                    validate_contract(raw, path.parent)
                except (ValueError, OSError, KeyError, TypeError) as error:
                    gaps.append(str(error))
            result = {"status": "gaps_found" if gaps else "structurally_valid", "gaps": gaps,
                      "source_format": raw.get("format"), "acceptance_count": len(raw.get("acceptance", [])),
                      "note": "Read-only audit; does not discover undeclared states or import old approvals."}
        elif args.command == "init":
            experiment = Harness.init(args.contract, args.run_dir)
            result = experiment.status()
        else:
            experiment = Harness(args.run_dir)
            if args.command in {"status", "context", "review-packet", "coverage", "metrics", "report", "handoff", "resume", "end-check", "execution-plan"}:
                with experiment.locked():
                    status = None
                    continuation = None
                    if args.command == "execution-plan":
                        result = compile_plan(experiment)
                    elif args.command == "end-check":
                        result = end_check(experiment, args.operation_id, read_json(args.input))
                    elif args.command == "resume":
                        status = experiment.status()
                        continuation = advice(experiment, status)
                        result = recovery(experiment, status, continuation)
                    elif args.command == "context":
                        result = experiment.project_context()
                    elif args.command == "review-packet":
                        result = write_review_packet(experiment)
                    elif args.command == "coverage":
                        status = experiment.status()
                        result = {k: status[k] for k in ("coverage", "state_coverage", "points")}
                        if "test_plan" in status:
                            result["test_plan"] = status["test_plan"]
                    elif args.command == "metrics":
                        result = experiment.metrics()
                    elif args.command == "report":
                        status = experiment.status()
                        result = write_report(experiment, status)
                    elif args.command == "handoff":
                        result = write_handoff(experiment, read_json(args.input))
                    else:
                        result = experiment.status()
                        status = result
                    result = project_result(experiment, result, status, continuation)
                    projected = True
            elif args.command == "run":
                result = experiment.run(args.operation_id, args.limit, args.tasks, args.points, args.problem_ids)
            elif args.command == "record":
                result = experiment.record(args.operation_id, read_json(args.input))
            elif args.command == "approve":
                result = experiment.approve(args.operation_id, read_json(args.input))
            elif args.command == "closeout":
                result = {"delivery": record_closeout(experiment, args.operation_id, read_json(args.input))}
            elif args.command == "retry":
                result = experiment.retry(args.operation_id, args.task, args.reason, args.evidence)
            elif args.command == "reconcile":
                result = experiment.reconcile(args.operation_id, args.task, args.reason, args.evidence, args.action)
            elif args.command == "extend-budget":
                result = experiment.extend_budget(args.operation_id, args.task, args.additional, args.reason, args.evidence)
            elif args.command == "revise":
                result = experiment.revise(args.operation_id, args.contract, args.reason)
            elif args.command == "inspect":
                result = experiment.inspect(args.task)
            elif args.command == "attach":
                result = experiment.attach(args.operation_id, args.task, args.evidence)
            elif args.command == "timer":
                result = experiment.timer(args.operation_id, args.id, args.phase, args.action)
            else:
                result = experiment.verify()
        if experiment is not None and not projected:
            # This advisory layer is deliberately outside evidence fingerprints.
            # Even idempotent operation replays need fresh whole-feature advice.
            with experiment.locked():
                result = project_result(experiment, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "verify":
            return 0 if result["status"] == "delivery_ready" else 1
        if args.command == "compare-visual":
            return 0 if result["status"] == "passed" else 1
        if args.command == "end-check":
            return 0 if result["allowed"] else 1
        if args.command == "agent-drive":
            return 0 if result["can_end_turn"] else 1
        if args.command == "agent-check":
            return 0 if result["status"] in {"ready", "declared_only"} else 1
        if args.command == "agent-status":
            return 1 if result["pending_turn"] or result["journal_error"] else 0
        if args.command == "audit":
            return 1 if result["gaps"] else 0
        if args.command == "project-verify":
            return 0 if result["verification_succeeded"] else 1
        if args.command == "run" and any(r["status"] in {"needs_diagnosis", "in_doubt", "budget_exhausted", "input_missing"}
                                         for r in result.get("tasks", [])):
            return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "error": str(error),
                          "continuation": command_error(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
