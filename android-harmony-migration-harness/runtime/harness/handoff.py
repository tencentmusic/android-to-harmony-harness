"""Developer handoff HTML in the Harmony project; execution evidence stays in the run."""
from __future__ import annotations

import base64
import copy
import html
import re
from pathlib import Path

from contract import artifact
import foundation as base
from foundation import runtime


FORMAT = "developer-handoff/1"
DIRECTORY = "androidtoharmonyharnessdoc"
SECTIONS = (("overview", "总体情况"), ("pending", "未完成项与后续工作"),
            ("requirements", "需求范围"), ("technical", "技术方案"), ("ui", "UI 稿与实现效果"),
            ("code", "代码交接"), ("verification", "验证与复现"), ("integration", "接入与合入"))
LABELS = {"passed": "通过", "failed": "失败", "not_checked": "未检查", "blocked": "受阻"}
STYLE = """
*{box-sizing:border-box}body{margin:0;background:#fff;color:#252c32;font:15px/1.75 system-ui,sans-serif;letter-spacing:0}
.layout{max-width:1360px;margin:auto;display:grid;grid-template-columns:220px minmax(0,1fr)}
nav{background:#f3f5f6;padding:28px 20px;border-right:1px solid #dde3e6;position:sticky;top:0;height:100vh;overflow:auto}
nav strong{display:block;margin-bottom:18px}nav a{display:block;margin:10px 0}a{color:#146d68;overflow-wrap:anywhere}
main{min-width:0;padding:28px 36px 64px}h1,h2,h3{overflow-wrap:anywhere}h1{font-size:28px;line-height:1.4}h2{font-size:21px}h3{font-size:17px}
section{padding-top:16px;margin-top:28px;border-top:1px solid #dde3e6;scroll-margin-top:20px}
p,li,dd,td,th{overflow-wrap:anywhere}ul,ol{padding-left:22px}li{margin:6px 0}
.notice{padding:12px 16px;background:#fff5e8;border-left:4px solid #c57e25}.muted{color:#5c6870}
.table{max-width:100%;overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px;table-layout:fixed}
td,th{padding:10px 12px;border:1px solid #dde3e6;text-align:left;vertical-align:top}th{background:#f3f5f6}
dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px 16px}dt{color:#5c6870}dd{margin:0}
.images{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:20px}
figure{margin:12px 0}img{display:block;width:100%;height:480px;object-fit:contain;background:#f5f7f8}
figcaption{padding:8px 0;color:#5c6870}details{margin:16px 0}summary{cursor:pointer;font-weight:600}
a:focus-visible,summary:focus-visible{outline:3px solid #2475a8;outline-offset:3px}
@media(max-width:760px){.layout{display:block}nav{position:static;height:auto;padding:16px;border-right:0;border-bottom:1px solid #dde3e6}
nav a{display:inline-block;margin:5px 14px 5px 0}main{padding:20px 16px 48px}h1{font-size:24px}dl{grid-template-columns:80px minmax(0,1fr)}
td,th{padding:8px}img{height:420px}}
@media print{nav{display:none}.layout{display:block}main{padding:0}section{break-inside:avoid}details{display:block}}
"""


def escape(value):
    return html.escape(str(value), quote=True)


def check_artifact(record):
    base.check_record({key: value for key, value in record.items() if key not in {"locator", "caption"}})


def text_list(value, name, nonempty=False):
    base.require(isinstance(value, list) and all(base.text(v) for v in value) and (value or not nonempty),
                 "Handoff needs a text list: " + name)


def project_directory(experiment, project):
    base.require(isinstance(project, dict) and project.get("root") == "harmony",
                 "Handoff project must use the Harmony root")
    relative = project.get("path")
    base.require(base.text(relative) and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                 "Select a project path relative to the Harmony root")
    root = Path(experiment.contract["roots"]["harmony"]).resolve(strict=True)
    target = (root / relative).resolve(strict=True)
    base.require(target.is_dir() and base.inside(target, root), "Handoff project escapes Harmony root")
    output = target / DIRECTORY
    base.require(not output.is_symlink() and (not output.exists() or output.is_dir()),
                 "Handoff directory must be a real directory in the Harmony project")
    return output


def validate(experiment, value):
    base.require(isinstance(value, dict) and value.get("format") == FORMAT, "Expected " + FORMAT)
    project_directory(experiment, value.get("project"))
    base.require(base.text(value.get("title")), "Handoff title is required")
    for name in ("overview", "requirements", "technical", "code", "verification", "integration"):
        section = value.get(name)
        base.require(isinstance(section, dict) and base.text(section.get("summary")), "Handoff needs " + name + ".summary")
    text_list(value["overview"].get("delivered"), "overview.delivered", nonempty=True)
    text_list(value["requirements"].get("in_scope"), "requirements.in_scope", nonempty=True)
    text_list(value["requirements"].get("out_of_scope"), "requirements.out_of_scope")
    text_list(value["technical"].get("decisions"), "technical.decisions", nonempty=True)
    for name in ("verification", "integration"):
        text_list(value[name].get("steps"), name + ".steps", nonempty=True)
    base.require(isinstance(value.get("pending"), list), "Handoff pending list is required, even when empty")
    for item in value["pending"]:
        base.require(isinstance(item, dict) and all(base.text(item.get(k)) for k in ("item", "impact", "next_step", "owner")),
                     "Pending work needs item, impact, next_step and owner")
    code = value["code"]
    base.require(all(base.text(code.get(k)) for k in ("repository", "branch", "revision", "target_branch")),
                 "Code handoff needs repository, branch, revision and target_branch")
    base.require(isinstance(code.get("entries"), list) and code["entries"], "Code entry paths are required")
    root = Path(experiment.contract["roots"]["harmony"])
    for entry in code["entries"]:
        base.require(isinstance(entry, dict) and base.text(entry.get("purpose")), "Code entry needs a purpose")
        relative = entry.get("path")
        base.require(base.text(relative) and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                     "Code entries must be relative to the Harmony root")
        path = (root / relative).resolve(strict=True)
        base.require(base.inside(path, root.resolve()), "Code entry escapes Harmony root")
    ui = value.get("ui")
    base.require(isinstance(ui, dict) and type(ui.get("applicable")) is bool, "Declare whether UI changed")
    images = []
    if ui["applicable"]:
        base.require(base.text(ui.get("summary")) and isinstance(ui.get("images"), list) and ui["images"],
                     "UI changes need a summary and selected design/implementation images")
        for reference in ui["images"]:
            record = artifact(reference, experiment.contract["roots"])
            raw = Path(record["path"]).read_bytes()
            suffix = Path(record["path"]).suffix.lower()
            valid = ((suffix == ".png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or
                     (suffix in {".jpg", ".jpeg"} and raw.startswith(b"\xff\xd8\xff")) or
                     (suffix == ".webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))
            base.require(valid, "Handoff UI image must be PNG, JPEG or WebP")
            images.append({**record, "caption": reference["locator"]})
    else:
        base.require(base.text(ui.get("reason")) and not ui.get("images"), "UI not applicable needs a reason and no images")
    topics = value.get("topics", [])
    base.require(isinstance(topics, list), "Handoff topics must be a list")
    for topic in topics:
        base.require(isinstance(topic, dict) and base.text(topic.get("title")) and base.text(topic.get("summary")),
                     "Additional handoff topic needs a title and summary")
        text_list(topic.get("items", []), "topics.items")
    knowledge = value.get("knowledge")
    base.require(isinstance(knowledge, dict) and knowledge.get("status") in {"completed", "not_applicable"}
                 and base.text(knowledge.get("summary")), "Finish knowledge backfill or record why it is not applicable")
    evidence = knowledge.get("evidence", [])
    base.require(isinstance(evidence, list) and (evidence or knowledge["status"] == "not_applicable"),
                 "Completed knowledge backfill needs evidence")
    return images, [artifact(ref, experiment.contract["roots"]) for ref in evidence]


def binding(experiment, status):
    # Final human decisions do not change source/test inputs. Keep presentation
    # outside engine fingerprints so generating a document cannot stale a build.
    return {"contract": experiment.state["contract"], "joint_review": status["joint_review"],
            "tasks": [{k: row[k] for k in ("id", "status", "input_digest") if k in row} for row in status["tasks"]],
            "receipts": {tid: saved.get("receipt") for tid, saved in experiment.state["tasks"].items()},
            "points": status["points"]}


def current(experiment, status=None):
    pointer = experiment.root / "developer-handoff.json"
    if not pointer.exists():
        return {"status": "missing"}
    try:
        saved = base.read_json(pointer)
        base.check_record(saved["packet"])
        packet = base.read_json(saved["packet"]["path"])
        base.require(packet["binding"] == binding(experiment, status or experiment.status()), "Handoff source or verification changed")
        base.check_record(packet["html"])
        for record in packet["images"] + packet["knowledge_evidence"]:
            check_artifact(record)
        return {"status": "current", "html_path": packet["html"]["path"], "packet_path": saved["packet"]["path"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "stale", "reason": str(error)}


def listing(items, ordered=False):
    tag = "ol" if ordered else "ul"
    return "<" + tag + ">" + "".join("<li>" + escape(v) + "</li>" for v in items) + "</" + tag + ">"


def table(headers, rows):
    return ('<div class="table"><table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(c) + '</td>' for c in row)
                                              + '</tr>' for row in rows) + '</tbody></table></div>')


def document(experiment, value, status, images):
    overview, requirements, code = (value[k] for k in ("overview", "requirements", "code"))
    count = status["coverage"]
    content = ["<h1>" + escape(value["title"]) + "</h1>"]
    if experiment.contract.get("demo"):
        content.append('<p class="notice">离线合成示例，不代表真实业务交付。</p>')
    content += ['<section id="overview"><h2>总体情况</h2><p>' + escape(overview["summary"]) + '</p>',
                '<h3>本次交付的功能</h3>' + listing(overview["delivered"]),
                '<p class="muted">合同验证结果：通过 {passed} / {required}；失败 {failed}，未检查 {not_checked}，受阻 {blocked}。</p>'.format(**count),
                '</section><section id="pending"><h2>未完成项与后续工作</h2>']
    if value["pending"]:
        content.append(table(["事项", "影响", "下一步", "跟进人 / 角色"],
                             [[item[k] for k in ("item", "impact", "next_step", "owner")] for item in value["pending"]]))
    else:
        content.append('<p>未另行登记开发待办。</p>')
    gaps = list(dict.fromkeys((experiment.acceptance[p["acceptance_id"]]["behavior"], LABELS[p["status"]])
                              for p in status["points"] if p["status"] != "passed"))
    if gaps:
        content.append('<div class="notice"><h3>尚未通过的合同检查</h3>' +
                       listing([behavior + "：" + result for behavior, result in gaps]) + '</div>')
    content += ['</section><section id="requirements"><h2>需求范围</h2><p>' + escape(requirements["summary"]) + '</p>',
                '<h3>本次包含</h3>' + listing(requirements["in_scope"]), '<h3>本次不包含</h3>',
                listing(requirements["out_of_scope"]) if requirements["out_of_scope"] else '<p>无额外排除项。</p>',
                '</section><section id="technical"><h2>技术方案</h2><p>' + escape(value["technical"]["summary"]) + '</p>',
                listing(value["technical"]["decisions"]), '</section><section id="ui"><h2>UI 稿与实现效果</h2>']
    ui = value["ui"]
    content.append('<p>' + escape(ui["summary"] if ui["applicable"] else ui["reason"]) + '</p>')
    if images:
        content.append('<div class="images">')
        for record in images:
            raw = Path(record["path"]).read_bytes()
            check_artifact(record)
            suffix = Path(record["path"]).suffix.lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[suffix]
            content.append('<figure><img alt="' + escape(record["caption"]) + '" src="data:' + mime + ';base64,'
                           + base64.b64encode(raw).decode("ascii") + '"><figcaption>' + escape(record["caption"]) + '</figcaption></figure>')
        content.append('</div>')
    content += ['</section><section id="code"><h2>代码交接</h2><p>' + escape(code["summary"]) + '</p><dl>']
    for key, label in (("repository", "仓库"), ("branch", "开发分支"), ("revision", "代码版本"), ("target_branch", "目标分支")):
        content.append('<dt>' + label + '</dt><dd>' + escape(code[key]) + '</dd>')
    content += ['</dl>', table(["入口 / 模块（相对鸿蒙源码根）", "职责与改动"],
                              [[e["path"], e["purpose"]] for e in code["entries"]]), '</section>']
    for name, title in SECTIONS[6:]:
        content.append('<section id="' + name + '"><h2>' + title + '</h2><p>' + escape(value[name]["summary"]) + '</p>'
                       + listing(value[name]["steps"], ordered=True) + '</section>')
    for topic in value.get("topics", []):
        content.append('<section><h2>' + escape(topic["title"]) + '</h2><p>' + escape(topic["summary"]) + '</p>'
                       + listing(topic.get("items", [])) + '</section>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'img-src data:; base-uri \'none\'; form-action \'none\'">'
            '<title>' + escape(value["title"]) + '</title><style>' + STYLE + '</style></head><body><div class="layout">'
            '<nav aria-label="交接目录"><strong>开发交接</strong>' + ''.join('<a href="#' + key + '">' + title + '</a>' for key, title in SECTIONS)
            + '</nav><main>' + ''.join(content) + '</main></div></body></html>')


def write_handoff(experiment, value):
    value = copy.deepcopy(value)
    images, knowledge = validate(experiment, value)
    output = project_directory(experiment, value["project"])
    status = experiment.status()
    bound = binding(experiment, status)
    rendered = document(experiment, value, status, images)
    digest = runtime.digest({"input": value, "binding": bound, "images": images, "knowledge": knowledge, "html": rendered})
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", experiment.contract["feature_id"]).strip("-")[:60] or "feature"
    output.mkdir(exist_ok=True)
    target = output / (slug + "-" + experiment.state["run_id"][:8] + "-" + digest[:16] + ".html")
    existed = target.exists()
    try:
        if existed:
            base.require(not target.is_symlink() and target.read_text(encoding="utf-8") == rendered, "Handoff snapshot was modified")
        else:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        base.require(binding(experiment, experiment.status()) == bound,
                     "Handoff output overlaps source inputs; narrow input patterns before exporting project documentation")
        for record in images + knowledge:
            check_artifact(record)
        packet = {"format": FORMAT, "input": value, "binding": bound, "html": base.file_record(target),
                  "images": images, "knowledge_evidence": knowledge}
        packet_path = experiment.root / "developer-handoffs" / (digest + ".json")
        runtime.write_json(packet_path, packet)
        runtime.write_json(experiment.root / "developer-handoff.json", {"packet": base.file_record(packet_path)})
    except Exception:
        if not existed:
            target.unlink(missing_ok=True)
        raise
    return current(experiment, status)
