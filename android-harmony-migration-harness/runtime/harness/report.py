"""Human-readable views of the same point results used by the gate."""
from pathlib import Path
from continuation import advice


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown(experiment, status=None):
    status = experiment.status() if status is None else status
    continuation = advice(experiment, status)
    labels = {"passed": "通过", "failed": "失败", "not_checked": "未检查", "blocked": "受阻",
              "approved": "已批准", "rejected": "未批准", "awaiting_human": "待人工审核", "stale": "已失效"}
    lines = ["# 迁移评审与逐点验收", "", "功能：`" + experiment.contract["feature_id"] + "`", "",
             "整体状态：`" + status["status"] + "`。统计单位为声明的验收点，不代表未知状态已被发现。", ""]
    if experiment.contract.get("demo"):
        lines += ["这是离线合成示例；审批为模拟记录，不代表真实人工批准或业务验收。", ""]
    handoff = continuation["developer_handoff"]
    if handoff["status"] == "current":
        lines += ["[开发交接 HTML](<" + handoff["html_path"] + ">)", ""]
    plan = status.get("test_plan")
    if plan:
        lines += ["## 本次测试计划", "",
                  "场景 {scenarios}；验收点 {acceptance_points}；断言 {assertions}；执行套件 {execution_suites}。".format(**plan),
                  "套件数是计划分组数；实际执行次数与耗时见下方记录。", "",
                  "| 场景 | 覆盖状态 | 验证方式 | 结果 |", "| --- | --- | --- | --- |"]
        for scenario in plan["scenario_results"]:
            lines.append("| " + " | ".join(cell(v) for v in (scenario["behavior"], ", ".join(scenario["state_ids"]),
                         ", ".join(scenario["methods"]), labels[scenario["status"]])) + " |")
        if plan["excluded_states"]:
            lines += ["", "未纳入本次生成范围："]
            lines += ["- " + cell(s["state_id"]) + "：" + cell(s["reason"]) for s in plan["excluded_states"]]
        if plan["revision"]:
            lines += ["", "计划修订：" + cell(plan["revision"]["reason"])]
            for entry in plan["revision"].get("replacements", []) + plan["revision"].get("retirements", []):
                lines.append("- " + cell(entry["acceptance_id"]) + "：" + cell(entry["reason"]))
        lines.append("")
    delivery = continuation.get("delivery")
    if delivery:
        lines += ["## 用户限定范围交付", "", "交付状态：`" + delivery["status"] + "`。全量合同和 verify 结果保持独立。", ""]
        if delivery.get("report"):
            lines += ["[最终交付审核 HTML](<" + delivery["report"]["path"] + ">)", ""]
        if delivery.get("reason"):
            lines += ["需要修复：" + cell(delivery["reason"]), ""]
    lines += ["## 续行判断", "", "`continuation.status=" + continuation["status"] + "`，`can_end_turn=" +
              str(continuation["can_end_turn"]).lower() + "`。"]
    for action in continuation["pending_actions"]:
        target = action.get("task_id", action.get("capability_id", "当前运行"))
        lines.append("- " + cell(action["owner"]) + "：`" + cell(action["action"]) + "`（" + cell(target) + "）")
    lines += ["", "报告生成不等于工作结束；仍有 Agent 动作时继续执行。此判断不改变验收、审批或失败预算，也不会后台唤醒对话。", ""]
    lines += ["## 联合评审", "", "| 范围 | 状态 | 需求 | 技术 | UI |", "| --- | --- | --- | --- | --- |"]
    for gate in status["joint_review"]:
        scope = experiment.scopes[gate["capability_id"]]
        links = []
        for dimension in ("requirements", "technical", "ui"):
            ref = scope[dimension]
            path = Path(experiment.contract["roots"][ref["root"]]) / ref["path"]
            decision = gate.get("decisions", {}).get(dimension, "awaiting_human")
            links.append(labels[decision] + " · [" + cell(ref["locator"]) + "](<" + str(path) + ">)")
        lines.append("| " + cell(gate["capability_id"]) + " | " + labels.get(gate["status"], gate["status"]) + " | " + " | ".join(links) + " |")
    lines += ["", "## 逐点验收", "", "| 状态 ID | 验收点 | 类型 | 结果 | 原因 / 缺失断言 | 证据 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for point in status["points"]:
        evidence = " · ".join("[证据](<" + e["path"] + ">)" for e in point.get("evidence", []))
        reason = point.get("reason", "") or ", ".join(point.get("missing_assertions", []))
        lines.append("| " + " | ".join(cell(v) for v in (", ".join(point.get("covered_state_ids", [])) or point.get("state_id") or "业务规则", point["acceptance_id"],
                     point["kind"], labels[point["status"]], reason, evidence)) + " |")
    count = status["coverage"]
    lines += ["", "通过 {passed} / 必验 {required}；失败 {failed}，未检查 {not_checked}，受阻 {blocked}。".format(**count),
              "", "最终人工复核：" + labels.get(status["final_review"]["status"], status["final_review"]["status"]),
              "", "## 耗时", "", "| 执行类别 | 尝试次数 | 累计秒数 |", "| --- | --- | --- |"]
    for kind, value in status["metrics"]["executor_by_kind"].items():
        lines.append("| {} | {} | {} |".format(kind, value["attempts"], value["seconds"]))
    metrics = status["metrics"]
    session = metrics["session"]
    if session["recorded"]:
        lines += ["", "| 会话阶段 / 等待状态 | 墙钟秒数 |", "| --- | --- |"]
        lines += ["| {} | {} |".format(cell(k), seconds) for k, seconds in session["seconds_by_state_or_phase"].items()]
        lines += ["", "会话中断 {} 次，结束检查拒绝 {} 次；未观测成本为 unknown。会话墙钟与执行器时间有重叠，不相加。".format(
            session["interruptions"], session["denied_end_checks"])]
    elif session.get("status") == "invalid_journal":
        lines += ["", "会话日志需要修复：" + cell(session["reason"]) + "。会话耗时未知；结束检查保持拒绝。"]
    lines += ["", "| 成本类别 | 秒数 |", "| --- | --- |"]
    for category, seconds in metrics["executor_cost_seconds"].items():
        lines.append("| {} | {} |".format(category, seconds))
    lines += ["", "Agent 区间去重墙钟：{} 秒。未登记的外部操作耗时：unknown。".format(metrics["agent_wall_union_seconds"]),
              "项目能力接入收据已复用 {} 份，不计入本次执行耗时。".format(len(metrics["reused_project_setup_receipts"])),
              "", "## 问题与恢复", "", "| 类别 | 步骤 | 累计次数 | 状态 | 恢复动作 |", "| --- | --- | --- | --- | --- |"]
    for problem in metrics["problems"]:
        lines.append("| " + " | ".join(cell(problem.get(k, "")) for k in
            ("category", "step", "occurrences", "status", "recovery_action")) + " |")
    lines += ["", "执行器耗时包含等待；适配器细分计时和 Agent 墙钟区间见 metrics，二者可能重叠，不相加推算模型耗时。", "",
              "评分仅作辅助；截图留存不等于视觉通过。此报告不授予人工批准或交付通过。", ""]
    return "\n".join(lines)


def write_report(experiment, status=None):
    status = experiment.status() if status is None else status
    report = experiment.root / "report.md"
    report.write_text(markdown(experiment, status), encoding="utf-8")
    return {"report": str(report), "status": status["status"]}
