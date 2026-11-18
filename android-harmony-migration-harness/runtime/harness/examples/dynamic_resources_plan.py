"""Offline planning replay for the reviewed dynamic-resources inventory, not a test executor.

These groups are specific to that migration's implementation and approved scope.
Other migrations must derive their own groups from their change analysis.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import foundation as base
from test_plan import compile_contract, summary
from contract import validate_contract


GROUPS = [
    ("startup-cache", "update", ["u-cold", "u-warm", "u-restart", "u-privacy"], ["contract", "device"],
     "授权后首次加载、缓存重启与恢复", "启动越权请求或恢复未提交文件", "一次授权后初始化到缓存重启流程"),
    ("update-response", "update", ["u-check", "u-no-update", "u-qps", "u-request-error", "u-empty", "u-server-profile"], ["contract"],
     "更新响应与平台配置处理", "错误响应推进版本或造成重试风暴", "同一请求入口的参数化响应分支"),
    ("download-scheduling", "update", ["u-locked", "u-download", "u-version-hit"], ["contract"],
     "下载合并、并发和同版本跳过", "重复下载或完成计数无法解锁", "同一下载调度器的并发及命中分支"),
    ("atomic-publish", "update", ["u-index-bad", "u-unpack", "u-commit", "u-partial-fail"], ["contract"],
     "解压校验与失败保留旧快照", "索引损坏、越界解压或部分失败覆盖可用资源", "发布事务的故障注入矩阵"),
    ("archive-layout", "update", ["u-output-format"], ["contract"],
     "兼容现有发布切片与索引格式", "把 manifest 当索引或错误选择密度", "真实发布产物结构的批量审计"),
    ("original-bytes", "update", ["u-original-bytes"], ["contract"],
     "下载解压后保留原件字节", "重编码或预置压缩包改变交付内容", "逐文件哈希审计"),
    ("missing-image", "images", ["i-empty", "i-debug"], ["contract"],
     "缺图与 Debug/Release 来源一致", "缺图时错绑或回退到包内资源", "统一图片解析器的参数化缺失分支"),
    ("decode-failure", "images", ["i-decode-fail"], ["contract"],
     "损坏图片解码失败安全返回", "异常泄漏或保留错误绑定", "解码错误注入；真实正常解码由图片流程验证"),
    ("image-binding", "images", ["i-file", "i-density", "i-cache", "i-ready-refresh", "i-content-difference"], ["contract", "device", "visual"],
     "普通图片读取、更新与尺寸保持", "异步刷新错图、密度或内容差异被遗漏", "共享图片绑定流程加全量尺寸与内容差异审计，代表页面真机取证"),
    ("room-emoji", "images", ["i-emoji-room"], ["contract", "device"],
     "房间表情下载后刷新并保持发送协议", "公屏混排路径失效或协议值变成沙箱路径", "房间输入与公屏的独立混排实现"),
    ("private-emoji", "images", ["i-emoji-private"], ["contract", "device"],
     "私聊输入与历史消息表情兼容", "历史协议解析或离线显示回归", "私聊富文本实现与房间输入分开验证"),
    ("kucy-binding", "images", ["i-kucy"], ["device", "visual"],
     "酷炫等级资源键接入与刷新", "多级映射缺图或退出后订阅泄漏", "同一等级资源映射的代表消费者，逐引用审计由包体套件覆盖"),
    ("other-consumers", "images", ["i-medal-other"], ["device", "visual"],
     "勋章、底栏与装饰消费者动态绑定", "未迁移引用导致入口缺图或控件不可用", "资源引用全量审计并按不同绑定/生命周期选代表入口"),
    ("svga-lifecycle", "animations", ["a-svga-dir", "a-svga-file", "a-svga-missing", "a-playback", "a-exit"], ["contract", "device"],
     "SVGA 路径、播放、降级与退出", "目录路径不兼容、空动画或迟到回调恢复旧视图", "同一 SVGA 播放器的路径矩阵和一次完整生命周期"),
    ("pag-lifecycle", "animations", ["a-pag-dir", "a-pag-file", "a-pag-missing", "a-playback", "a-exit"], ["contract", "device"],
     "PAG 路径、播放、降级与退出", "图层加载错误、插件不可用或释放异常", "PAG 独立播放器的路径矩阵、图层及生命周期，连续帧验证运动"),
    ("image-package", "images", ["i-package-clean"], ["contract"],
     "图片包体清理与引用完整性", "遗留资源或改名备份仍进入包体", "全量引用及所有 HAP/HSP/HAR 的递归扫描"),
    ("animation-package", "animations", ["a-package-clean"], ["contract"],
     "动画资源包体清理", "an.pag 或 SVGA 备份回流生产包", "目标动画及别名字节的全量包体扫描")
]


def make_plan(contract):
    states = {s["id"]: s for s in contract["states"]}
    expected = {sid for _, _, state_ids, *_ in GROUPS for sid in state_ids} | {"a-legacy"}
    base.require(set(states) == expected, "This replay requires the reviewed 42-state dynamic-resources inventory")
    scopes = {s["id"]: s for s in contract["review_scopes"]}
    methods = {}
    for task in contract["tasks"]:
        if task["kind"] in base.TEST_KINDS:
            for cid in task["capability_ids"]:
                base.require((cid, task["kind"]) not in methods, "Replay needs one suite per capability/method")
                methods[cid, task["kind"]] = task["id"]
    changes = [{"id": "migrate-" + cid, "capability_id": cid, "before": before, "after": after,
                "sources": [copy.deepcopy(scopes[cid]["requirements"])]}
               for cid, before, after in [
                   ("update", "缺少本次动态资源更新链路", "授权后下载、索引发布与缓存恢复"),
                   ("images", "目标图片由包内资源提供", "由下载原件、资源键与消费者刷新提供"),
                   ("animations", "目标 SVGA/PAG 由包内文件提供", "播放器读取下载路径，缺失降级并正确释放")]]
    scenarios = []
    for sid, cid, state_ids, kinds, behavior, risk, grouping in GROUPS:
        checks = []
        for kind in kinds:
            assertions = {}
            for point in contract["acceptance"]:
                if point.get("state_id") in state_ids and point["kind"] == kind:
                    for assertion in point["assertions"]:
                        existing = assertions.get(assertion["id"])
                        base.require(existing is None or existing == assertion, "Conflicting assertions need separate scenarios")
                        assertions[assertion["id"]] = copy.deepcopy(assertion)
            base.require(bool(assertions), "Replay cannot invent missing source assertions")
            checks.append({"kind": kind, "task_id": methods[cid, kind],
                           "reason": {"contract": "确定性逻辑、故障注入或全量文件审计",
                                      "device": "当前包的真实平台 API、消费者或播放器行为",
                                      "visual": "已变化图片的内容、布局、密度和透明度"}[kind],
                           "assertions": list(assertions.values())})
        scenarios.append({"id": sid, "capability_id": cid, "change_ids": ["migrate-" + cid],
                          "state_ids": state_ids, "behavior": behavior, "risk": risk,
                          "grouping_reason": grouping, "observable_ui": cid != "update", "verifications": checks})
    return {"version": 1, "changes": changes, "scenarios": scenarios, "excluded_states": [{
        "state_id": "a-legacy", "capability_id": "animations", "reason_code": "not_migrated",
        "reason": "本次鸿蒙实现没有 migrateOldRes 兼容入口；禁止包内动画回流由 animation-package 覆盖。",
        "sources": [copy.deepcopy(scopes["animations"]["technical"])]}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.contract).resolve(strict=True)
    contract = json.loads(source.read_text(encoding="utf-8"))
    plan = make_plan(contract)
    preview = validate_contract(compile_contract(contract, plan), source.parent)
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"mode": "planning_replay_only", "test_plan": summary(preview)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
