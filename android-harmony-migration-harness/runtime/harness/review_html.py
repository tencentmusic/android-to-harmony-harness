"""Offline joint-review display, separate from approval and execution state."""
import html
import json
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from foundation import runtime


LABELS = {"requirements": "需求", "technical": "技术方案", "ui": "UI 材料",
          "approved": "已批准", "rejected": "未批准", "awaiting_human": "待人工审核",
          "stale": "已失效", "blocked": "受阻", "passed": "通过", "failed": "失败",
          "not_checked": "未检查"}
CSP = ("default-src 'none'; style-src 'unsafe-inline' file:; img-src data: file:; "
       "font-src data: file:; media-src data: file:; frame-src 'self' file: about:; "
       "base-uri file:; form-action 'none'")
STYLE = """
*{box-sizing:border-box}html{scroll-padding-top:20px}body{margin:0;background:#fff;color:#23292d;
font:15px/1.7 system-ui,sans-serif;letter-spacing:0}a{color:#076d68;overflow-wrap:anywhere}
.layout{max-width:1480px;margin:auto;display:grid;grid-template-columns:230px minmax(0,1fr)}
nav{position:sticky;top:0;height:100vh;overflow:auto;background:#f2f5f6;padding:28px 20px;
border-right:1px solid #d9e1e4}nav a{display:block;margin:12px 0}main{min-width:0;padding:30px 36px 60px}
h1{font-size:28px;line-height:1.4}h2{font-size:22px}h3{font-size:18px}h4{font-size:16px}
section{border-top:1px solid #d9e1e4;margin-top:28px;padding-top:20px}p,li{overflow-wrap:anywhere}
.notice{background:#fff4e7;border-left:4px solid #bd7226;padding:12px 16px}.meta{color:#56656c;font-size:13px}
code{overflow-wrap:anywhere;font:12px/1.7 ui-monospace,monospace}.table{overflow:auto;max-width:100%}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{border:1px solid #d9e1e4;
padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#f2f5f6}
.source{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.8 system-ui,sans-serif;
background:#f7f9fa;padding:16px;margin:12px 0}iframe{width:100%;height:640px;border:1px solid #d9e1e4;
background:white}img{display:block;max-width:100%;max-height:560px;object-fit:contain}
figure{margin:16px 0}figcaption{font-size:13px;color:#56656c}summary{cursor:pointer;padding:10px 0}
a:focus-visible,summary:focus-visible{outline:3px solid #266bc0;outline-offset:3px}
@media(max-width:760px){.layout{display:block}nav{position:static;height:auto;padding:16px;
border-right:0;border-bottom:1px solid #d9e1e4}nav a{display:inline-block;margin:6px 14px 6px 0}
main{padding:22px 16px}h1{font-size:24px}h2{font-size:20px}iframe{height:520px}}
@media print{nav{display:none}.layout{display:block}main{padding:0}.source{background:white}
section{break-before:page}iframe{height:800px}}
"""


def escape(value):
    return html.escape(str(value), quote=True)


def label(value):
    return escape(LABELS.get(value, value))


def table(headers, rows):
    return ('<div class="table"><table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + c + '</td>' for c in row) + '</tr>'
                                             for row in rows) + '</tbody></table></div>')


def relative_url(path, output):
    return quote(Path(path).relative_to(output).as_posix(), safe='/')


def material(record, archived, output):
    source = archived[record['path']]
    url = relative_url(source, output)
    link = '<a href="' + escape(url) + '">原始材料快照</a>'
    suffix = source.suffix.lower()
    if suffix in {'.html', '.htm'}:
        # srcdoc inherits the offline CSP; sandbox blocks scripts, forms and top navigation.
        content = ('<meta http-equiv="Content-Security-Policy" content="' + escape(CSP) + '">'
                   + '<base href="' + escape(source.parent.as_uri() + '/') + '">'
                   + source.read_text(encoding='utf-8'))
        return (link + '<iframe sandbox="allow-same-origin" title="' + escape(record.get('locator', 'UI'))
                + '" srcdoc="' + escape(content) + '"></iframe>')
    if suffix in {'.png', '.jpg', '.jpeg'}:
        return link + '<img src="' + escape(url) + '" alt="' + escape(record.get('locator', '材料')) + '">'
    if suffix in {'.md', '.txt', '.json', '.json5', '.csv', '.log'}:
        return link + '<pre class="source">' + escape(source.read_text(encoding='utf-8')) + '</pre>'
    return link + '<p>此格式通过原始附件查看。</p>'


def document(experiment, packet, output, archives):
    navigation = ['<a href="#overview">总览与缺口</a>']
    sections = []
    points = {p['acceptance_id']: p for p in packet['points']}
    for gate in packet['joint_review']:
        cid = gate['capability_id']
        navigation.append('<a href="#scope-' + escape(cid) + '">' + escape(cid) + '</a>')
        content = ['<section id="scope-' + escape(cid) + '"><h2>' + escape(cid) + '</h2>',
                   '<p>评审状态：' + label(gate['status']) + '</p>',
                   '<p class="meta">input_digest：<code>' + escape(gate.get('input_digest', '不可用')) + '</code></p>']
        decisions = gate.get('decisions', {})
        content.append(table(['需求', '技术方案', 'UI'], [[label(decisions.get(d, 'awaiting_human'))
                                                        for d in ('requirements', 'technical', 'ui')]]))
        if 'scope' not in gate:
            content.append('<p class="notice">材料不可用：' + escape(gate.get('reason', '未知原因')) + '</p></section>')
            sections.append(''.join(content))
            continue
        content.append('<p>' + escape(gate['capability']['behavior']) + '</p>')
        archived = archives[cid]
        for dimension, bundle in zip(('requirements', 'technical', 'ui'), gate['material_bundles']):
            content.append('<h3>' + LABELS[dimension] + '</h3>' + material(bundle['entry'], archived, output))
        content.append('<h3>状态、来源与基准</h3>')
        for state in gate['states']:
            content.extend(['<h4>' + escape(state['id']) + ' · ' + escape(state['kind']) + '</h4>',
                            '<p>触发：' + escape(state['trigger']) + '</p><p>预期：' + escape(state['expected']) + '</p>'])
            for source in state['sources']:
                key = str((Path(experiment.contract['roots'][source['root']]) / source['path']).resolve())
                content.append('<p class="meta">来源：<a href="' + escape(relative_url(archived[key], output))
                               + '">' + escape(source['path']) + '</a> · ' + escape(source['locator']) + '</p>')
            if not state['baselines'] and state.get('baseline_gap'):
                content.append('<p class="notice">基准缺口：' + escape(state['baseline_gap']) + '</p>')
            for baseline in state['baselines']:
                key = str((Path(experiment.contract['roots'][baseline['root']]) / baseline['path']).resolve())
                content.append('<figure><img src="' + escape(relative_url(archived[key], output))
                               + '" alt="' + escape(state['id']) + ' 基准"><figcaption>'
                               + escape(baseline['locator']) + '</figcaption></figure>')
        content.append('<h3>逐点验收断言</h3>')
        rows = []
        for point in gate['acceptance']:
            current = points.get(point['id'], {})
            assertions = '<br>'.join(escape(a['id']) + ' · ' + escape(a['operator']) + ' · <code>'
                                    + escape(json.dumps(a['expected'], ensure_ascii=False)) + '</code>'
                                    for a in point['assertions'])
            rows.append([escape(point['id']), escape(', '.join(point.get('covered_state_ids', [])) or point.get('state_id', '业务规则')), escape(point['kind']),
                         escape(point['behavior']), assertions, label(current.get('status', 'not_checked'))
                         + '<br>' + escape(current.get('reason', ''))])
        content.append(table(['验收点', '状态', '类型', '行为', '断言与预期', '当前结果'], rows))
        project = gate['project']
        content.append('<details><summary>项目上下文与工具声明</summary><pre class="source">'
                       + escape(json.dumps(project, ensure_ascii=False, indent=2)) + '</pre></details></section>')
        sections.append(''.join(content))
    warning = ('<p class="notice">离线合成示例；模拟审批不代表真实人工批准或业务交付。</p>'
               if experiment.contract.get('demo') else '')
    gaps = table(['状态', '能力', '基准缺口'], [[escape(g['state_id']), escape(g['capability_id']), escape(g['reason'])]
                                            for g in packet['baseline_gaps']]) if packet['baseline_gaps'] else '<p>需要视觉检查的状态没有待补基准；文件存在不代表已验收通过。</p>'
    plan = packet.get('test_plan')
    plan_html = ''
    if plan:
        plan_html = '<h2>本次测试计划</h2><p>场景 {} · 验收点 {} · 断言 {} · 执行套件 {}</p>'.format(
            plan['scenarios'], plan['acceptance_points'], plan['assertions'], plan['execution_suites'])
        plan_html += table(['场景', '覆盖状态', '风险', '验证方式', '结果'], [
            [escape(s['behavior']), escape(', '.join(s['state_ids'])), escape(s['risk']),
             escape(', '.join(s['methods'])), label(s['status'])] for s in plan['scenario_results']])
        if plan['excluded_states']:
            plan_html += '<h3>未纳入本次生成范围</h3>' + table(['状态', '依据'], [
                [escape(s['state_id']), escape(s['reason'])] for s in plan['excluded_states']])
        if plan['revision']:
            plan_html += '<h3>计划修订</h3><pre class="source">' + escape(json.dumps(plan['revision'], ensure_ascii=False, indent=2)) + '</pre>'
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="' + escape(CSP) + '">'
            '<title>' + escape(packet['feature_id']) + ' · 联合审批</title><style>' + STYLE + '</style></head>'
            '<body><div class="layout"><nav aria-label="评审目录"><strong>联合审批材料</strong>'
            + ''.join(navigation) + '</nav><main><header id="overview"><h1>' + escape(packet['feature_id'])
            + '</h1><p>联合审批 · 需求 / 技术方案 / UI</p>' + warning
            + '<p>此页为当前材料与状态的离线快照，不授予批准。审核意见须绑定对应 input_digest，通过 approve 单独登记。'
            '材料变更后重新执行 review-packet，并展示新返回的 HTML。</p>'
            '<p class="meta">HTML 预览禁用脚本与联网；交互式 UI 的可执行效果需另行验收。Markdown/文本完整原文列于对应材料栏。</p>'
            '<p><a href="packet.json">机器可读评审包</a></p></header>' + plan_html + '<h2>基准缺口</h2>' + gaps
            + ''.join(sections) + '</main></div></body></html>')


def write_review_packet(experiment):
    """Snapshot the displayed materials without changing any gate or source bundle."""
    packet = experiment.review_packet()
    output = experiment.root / 'review-packets' / uuid.uuid4().hex
    output.mkdir(parents=True)
    try:
        archives = {}
        for gate in packet['joint_review']:
            if 'scope' not in gate:
                continue
            cid = gate['capability_id']
            record = experiment.archive_review(cid, output / cid, gate['input_digest'])
            archive = json.loads(Path(record['path']).read_text(encoding='utf-8'))
            archives[cid] = {f['source']: Path(f['archived']['path']) for f in archive['files']}
        entry = output / 'index.html'
        entry.write_text(document(experiment, packet, output, archives), encoding='utf-8')
        packet['html_path'] = str(entry)
        packet['packet_path'] = str(output / 'packet.json')
        runtime.write_json(output / 'packet.json', packet)
        return packet
    except Exception:
        shutil.rmtree(output)
        raise
