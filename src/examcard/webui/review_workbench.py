"""审阅工作台 HTML 内核（规范源 r83 渲染器，参数化；可续传/进度参考 r87）。

纯函数生成静态单文件 HTML：队列 JSON 注入、科目/呈现/搜索过滤侧栏、源页图检视、
typed contract 结构预检、四态决策表单、decisions JSON 导出。无 flask 依赖——
按骨架职责切分，gates 只承载判定、webui 只承载 Web 层（webui → gates 合法向下）。

两界原则（结构保障）：导出的每条决定 decision_sha256 恒为 pending 占位串、
browser_demo 恒 true——该形态在类型层面就无法通过 gates.review.decisions 的
权威模型校验，浏览器产物必须经 CLI 严格摄取重算哈希方可入链。
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.parse import quote as url_quote

from ..gates.review.browser import PENDING_DECISION_HASH
from ..gates.review.dispositions import PRESENTATIONS

DEFAULT_CONFIRMATIONS: tuple[tuple[str, str], ...] = (
    ("sourceText", "source text 确认"),
    ("anchor", "anchor 确认"),
    ("sourcePage", "source page 确认"),
    ("imageHash", "image hash/尺寸确认"),
    ("contractCheck", "typed contract 确认"),
)


def compact_queue_entry(item: Any, candidate: Any) -> dict[str, Any]:
    """工作台队列条目投影（r83 `_compact_queue_entry` 形态，鸭子类型输入）。"""
    return {
        "candidate_id": candidate.candidate_id,
        "queue_item_id": item.queue_item_id,
        "subject_id": item.subject_id,
        "subject_label": getattr(item, "subject_label", item.subject_id),
        "record_id": item.record_id,
        "fact_id": item.fact_id,
        "evidence_id": item.evidence_id,
        "presentation": item.presentation,
        "source_manifest_sha256": item.source_manifest_sha256,
        "anchor_ids": list(item.anchor_ids),
        "pages": [
            {
                "page_ref_id": page.page_ref_id,
                "page": page.page,
                "source_file": page.source_file,
                # 从仓库根服务源页并对非 ASCII 路径段做百分号编码，浏览器才能稳定解析。
                "image_uri": url_quote("/" + str(page.image_path).replace("\\", "/").lstrip("/"), safe="/"),
                "image_sha256": page.image_sha256,
                "image_width": page.image_width,
                "image_height": page.image_height,
            }
            for page in item.pages
        ],
        "disposition": getattr(candidate, "disposition", "manual_review"),
    }


def render_review_workbench(
    entries: Sequence[Mapping[str, Any]],
    *,
    queue_sha256: str,
    title: str = "source-page typed Evidence 审核工作台",
    decision_schema_version: str = "examcard_review_raw_decision_v1",
    manifest_schema_version: str = "examcard_review_raw_draft_manifest_v1",
    presentations: Sequence[str] = PRESENTATIONS,
    confirmations: Sequence[tuple[str, str]] = DEFAULT_CONFIRMATIONS,
    export_filename: str = "review-browser-decisions.json",
) -> str:
    """渲染单文件工作台 HTML（UTF-8 中文文案；`</` 转义防脚本注入截断）。"""
    data = json.dumps(list(entries), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    subjects = sorted({str(entry["subject_id"]) for entry in entries})
    subject_options = "".join(f'<option value="{subject}">{subject}</option>' for subject in subjects)
    presentation_options = "".join(f"<option>{item}</option>" for item in presentations)
    check_labels = "".join(
        f'<label><input id="{key}" type="checkbox">{label}</label>' for key, label in confirmations
    )
    check_keys = json.dumps([key for key, _ in confirmations])
    return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>{title}</title>
<style>
:root {{ color-scheme:light; --ink:#182333; --muted:#697687; --line:#d8e0e8; --accent:#1769aa; --bg:#f4f7fa; --warn:#fff8e6; }}
* {{ box-sizing:border-box; }} body {{ margin:0; font:14px/1.45 system-ui,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
header {{ padding:18px 24px; background:#122b43; color:#fff; }} header h1 {{ margin:0 0 5px; font-size:20px; }} header p {{ margin:0; color:#cbd9e5; }}
main {{ display:grid; grid-template-columns:390px minmax(0,1fr); min-height:calc(100vh - 84px); }} aside {{ padding:15px; background:#fff; border-right:1px solid var(--line); overflow:auto; }} section {{ padding:20px; min-width:0; }}
label {{ display:block; margin:10px 0 4px; color:var(--muted); font-size:12px; }} select,input,textarea {{ width:100%; padding:8px; border:1px solid var(--line); border-radius:4px; background:#fff; color:var(--ink); }} textarea {{ min-height:100px; resize:vertical; font-family:ui-monospace,Consolas,monospace; }}
.meta {{ color:var(--muted); font-size:12px; }} #count {{ margin:14px 0 8px; font-weight:600; }} .item {{ padding:9px; border:1px solid var(--line); border-radius:4px; margin:6px 0; cursor:pointer; background:#fff; }} .item:hover,.item.active {{ border-color:var(--accent); background:#eef6fd; }}
.tag {{ display:inline-block; padding:2px 6px; border-radius:3px; background:#e8f0f8; color:#145585; font-size:11px; }} .grid {{ display:grid; grid-template-columns:repeat(3,minmax(150px,1fr)); gap:10px; margin:14px 0; }} .value {{ word-break:break-word; }}
.notice {{ padding:10px; background:var(--warn); border:1px solid #ebd28e; border-radius:4px; }} .image-box img {{ display:block; max-width:100%; max-height:600px; border:1px solid var(--line); margin-top:7px; }} .checks label {{ display:flex; gap:7px; align-items:center; }} .checks input {{ width:auto; }} button {{ margin:10px 8px 0 0; padding:9px 12px; border:0; border-radius:4px; color:#fff; background:var(--accent); cursor:pointer; }} button.secondary {{ background:#526273; }} #contractStatus {{ white-space:pre-wrap; }}
@media(max-width:900px) {{ main {{ grid-template-columns:1fr; }} aside {{ max-height:47vh; border-right:0; border-bottom:1px solid var(--line); }} .grid {{ grid-template-columns:1fr; }} }}
</style>
<body><header><h1>{title}</h1><p>仅审查 manual_review 代表项；必须逐项录入 typed contract。浏览器决定是 audit，不是生产授权。queue hash: {queue_sha256}</p></header>
<main><aside><label for="subject">科目</label><select id="subject"><option value="">全部</option>{subject_options}</select><label for="presentation">表达类型</label><select id="presentation"><option value="">全部</option>{presentation_options}</select><label for="query">搜索</label><input id="query" placeholder="record / Fact / 文件"><div id="count" class="meta"></div><div id="list"></div></aside>
<section><div id="empty" class="notice">从左侧选择审核项。必须对照原始页面图像，不能用 OCR、assertion、文件名或 legacy hint 代替 typed contract。</div><div id="detail" hidden><h2 id="title"></h2><div class="grid" id="facts"></div><div id="pages" class="image-box"></div><label for="contract">typed contract JSON（仅保存到浏览器内存）</label><textarea id="contract" placeholder='{{"presentation":"diagram",...}}'></textarea><button id="validateContract">校验 typed contract</button><span id="contractStatus" class="meta"></span><div class="notice" style="margin-top:14px">全部确认必须来自人工 source-page 复核。即使全部勾选，本页面导出的 browser decision 也不会授权生产 fixture。</div><label for="decision">处置</label><select id="decision"><option>manual_review</option><option>blocked</option><option>not_applicable</option><option>reviewed_candidate</option></select><label for="reviewer">审核标识</label><input id="reviewer" placeholder="reviewer id"><label for="reason">审核依据</label><textarea id="reason" placeholder="记录 source page、anchor、逐项文本/结构和噪声排除依据"></textarea><div class="checks">{check_labels}</div><button id="record">记录本地决定</button><button id="download" class="secondary">导出 decisions JSON</button><div id="status" class="meta"></div></div></section></main>
<script id="queue-data" type="application/json">{data}</script><script>
const queue=JSON.parse(document.getElementById('queue-data').textContent),decisions=new Map(),CHECKS={check_keys};let current=null;const $=id=>document.getElementById(id);
function esc(v){{return String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
function filtered(){{const s=$('subject').value,p=$('presentation').value,q=$('query').value.trim().toLowerCase();return queue.filter(x=>(!s||x.subject_id===s)&&(!p||x.presentation===p)&&(!q||[x.queue_item_id,x.record_id,x.fact_id,(x.pages[0]||{{}}).source_file||''].join(' ').toLowerCase().includes(q)));}}
function renderList(){{const xs=filtered();$('count').textContent=`${{xs.length}} / ${{queue.length}}`;$('list').innerHTML=xs.map(x=>`<div class="item ${{current===x.queue_item_id?'active':''}}" data-id="${{esc(x.queue_item_id)}}"><b>${{esc(x.subject_label)}}</b> <span class="tag">${{esc(x.presentation)}}</span><div class="meta">${{esc(x.record_id)}} · p.${{(x.pages[0]||{{}}).page||'-'}}</div></div>`).join('')||'<div class="meta">无匹配项</div>';document.querySelectorAll('.item').forEach(e=>e.onclick=()=>select(e.dataset.id));}}
function select(id){{current=id;const x=queue.find(y=>y.queue_item_id===id);if(!x)return;$('empty').hidden=true;$('detail').hidden=false;$('title').textContent=`${{x.subject_label}} / ${{x.presentation}}`;$('facts').innerHTML=[['candidate',x.candidate_id],['queue item',x.queue_item_id],['record',x.record_id],['Fact',x.fact_id],['Evidence',x.evidence_id],['manifest',x.source_manifest_sha256],['anchor',x.anchor_ids.join(', ')||'-']].map(([a,b])=>`<div><label>${{esc(a)}}</label><div class="value">${{esc(b)}}</div></div>`).join('');$('pages').innerHTML=x.pages.map(p=>`<div><div class="meta">page ${{p.page}} · ${{esc(p.image_sha256)}} · ${{p.image_width}}×${{p.image_height}}</div><img src="${{esc(p.image_uri)}}" alt="source page ${{p.page}}"></div>`).join('');const old=decisions.get(id);$('decision').value=old?.disposition||'manual_review';$('reviewer').value=old?.reviewer||'';$('reason').value=old?.reason||'';$('contract').value=old?.typed_contract_payload?JSON.stringify(old.typed_contract_payload,null,2):'';CHECKS.forEach(k=>$(k).checked=!!old?.confirmations?.[k]);$('contractStatus').textContent='';$('status').textContent='';renderList();}}
function validateContract(){{if(!$('contract').value.trim()){{$('contractStatus').textContent='typed contract 为空，拒绝';return false;}}let v;try{{v=JSON.parse($('contract').value);}}catch(e){{$('contractStatus').textContent='JSON 无法解析，拒绝';return false;}}const x=queue.find(y=>y.queue_item_id===current);if(!v||v.presentation!==x.presentation||!v.schema_version||!v.contract_sha256){{$('contractStatus').textContent='presentation/schema/hash 不完整，拒绝';return false;}}if(x.presentation==='diagram'&&(!Array.isArray(v.nodes)||!v.nodes.length||!Array.isArray(v.edges))){{$('contractStatus').textContent='diagram nodes/edges 不完整，拒绝';return false;}}if(x.presentation==='table'&&(!Array.isArray(v.cells)||!v.cells.length||!(v.rows>0&&v.columns>0))){{$('contractStatus').textContent='table grid/cells 不完整，拒绝';return false;}}if(x.presentation==='ordered_list'&&(!Array.isArray(v.items)||!v.items.length||v.continuation_binding_ids?.length)){{$('contractStatus').textContent='ordered-list items/continuation 不完整，拒绝';return false;}}if((x.presentation==='constraint'||x.presentation==='formula')&&(!Array.isArray(v.tokens)||!v.tokens.length)){{$('contractStatus').textContent='token contract 不完整，拒绝';return false;}}$('contractStatus').textContent='typed contract 结构通过浏览器预检；最终 hash/provenance 仍须 CLI strict schema 校验';return true;}}
function recordDecision(){{if(!current)return;const x=queue.find(y=>y.queue_item_id===current),disposition=$('decision').value;if(disposition==='reviewed_candidate'&&!validateContract())return;const confirmations={{}};CHECKS.forEach(k=>confirmations[k]=$(k).checked);const d={{schema_version:'{decision_schema_version}',draft_id:'draft:ui:'+crypto.randomUUID().replaceAll('-',''),candidate_id:x.candidate_id,queue_item_id:x.queue_item_id,queue_sha256:'{queue_sha256}',disposition,reviewer:$('reviewer').value.trim(),reason:$('reason').value.trim(),confirmations,typed_contract_payload:disposition==='reviewed_candidate'?JSON.parse($('contract').value):null,recorded_at:new Date().toISOString(),browser_demo:true,production_fixture_authorized:false,decision_sha256:'{PENDING_DECISION_HASH}'}};decisions.set(x.queue_item_id,d);$('status').textContent='已记录到页面内存；导出后仍须 CLI strict schema 校验，且浏览器不能授权生产 fixture。';renderList();}}
function downloadDecisions(){{const raw={{schema_version:'{manifest_schema_version}',queue_sha256:'{queue_sha256}',decisions:[...decisions.values()]}};const blob=new Blob([JSON.stringify(raw,null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='{export_filename}';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0);}}
['subject','presentation','query'].forEach(id=>$(id).oninput=renderList);$('record').onclick=recordDecision;$('validateContract').onclick=validateContract;$('download').onclick=downloadDecisions;renderList();
</script></body></html>"""
