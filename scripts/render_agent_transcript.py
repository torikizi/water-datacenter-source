#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from water_negotiation_lab.agents import parse_decision_response


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _json_for_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _rounds(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for event in events:
        number = int(event.get("decision_round", event.get("day", 1)))
        item = grouped.setdefault(
            number,
            {
                "round": number,
                "day": int(event["day"]),
                "date": event["date"],
                "reason": event.get("decision_reason", "scheduled_review"),
                "events": [],
            },
        )
        item["events"].append(event)
    return [grouped[number] for number in sorted(grouped)]


def _compact_water(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for row in rows:
        capacity = float(row["storage_capacity_l"])
        compact.append(
            {
                "day": int(row["day"]),
                "date": row["date"],
                "storagePct": (float(row["storage_end_l"]) / capacity * 100) if capacity else 0,
                "storageL": float(row["storage_end_l"]),
                "residentShortageL": float(row["resident_shortage_l"]),
                "dcWithdrawalL": float(row["datacenter_potable_withdrawal_l"]),
                "dcShortageL": float(row["datacenter_water_shortage_l"]),
                "restriction": float(row["datacenter_potable_restriction_multiplier"]),
                "nextRestriction": float(
                    row.get(
                        "datacenter_potable_restriction_multiplier_next_day",
                        row["datacenter_potable_restriction_multiplier"],
                    )
                ),
                "council": bool(row.get("agent_council_convened")),
            }
        )
    return compact


def _events_for_presentation(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, int]:
    presented = copy.deepcopy(events)
    legacy_long_count = 0
    normalized_count = 0
    held_count = 0
    for event in presented:
        event["display_response"] = event["parsed_response"]
        event["display_status"] = "valid" if event.get("valid") else "held"
        if event.get("valid") and event.get("normalizations"):
            event["display_status"] = "normalized"
            normalized_count += 1
            continue
        if event.get("valid") or not event.get("raw_response"):
            held_count += int(not event.get("valid"))
            continue
        reparsed, errors, normalizations = parse_decision_response(
            event["raw_response"], event.get("role", {}).get("allowed_actions", [])
        )
        finish_reason = event.get("provider_metadata", {}).get("finish_reason")
        if finish_reason not in (None, "stop"):
            errors.append(f"provider finish_reason was {finish_reason!r}, not 'stop'")
        if not errors and normalizations:
            event["display_response"] = reparsed
            event["display_status"] = "legacy_long"
            legacy_long_count += 1
        else:
            held_count += 1
    return presented, legacy_long_count, normalized_count, held_count


def render(events: list[dict[str, Any]], water_rows: list[dict[str, Any]] | None = None) -> str:
    if not events:
        raise ValueError("agent transcript requires at least one event")
    presented_events, legacy_long_count, normalized_count, held_count = (
        _events_for_presentation(events)
    )
    rounds = _rounds(presented_events)
    first = events[0]
    metadata = first.get("provider_metadata", {})
    provider_type = metadata.get("provider_type")
    is_mock = provider_type == "mock" or bool(metadata.get("mock"))
    is_ds4 = provider_type == "ds4"
    legacy_ds4 = (
        provider_type is None
        and metadata.get("model") == "deepseek-v4-flash"
        and "finish_reason" in metadata
    )
    model = "MockProvider" if is_mock else metadata.get("model", "provider未確認")
    provider_badge = (
        "● REPRODUCIBLE MOCK"
        if is_mock
        else "● REAL DS4 LOCAL INFERENCE"
        if is_ds4
        else "● LEGACY LOCAL INFERENCE · PROVIDER TAG PRE-DATES AUDIT"
        if legacy_ds4
        else "● PROVIDER UNVERIFIED"
    )
    valid_count = sum(bool(event.get("valid")) for event in events)
    accounting_days = len(water_rows or []) or max(int(event["day"]) for event in events)
    focus_days = accounting_days
    default_index = 0
    for index, item in enumerate(rounds):
        municipality = next(
            (event for event in item["events"] if event["role"]["role"] == "municipality"),
            None,
        )
        if municipality and municipality["parsed_response"]["action"] in {
            "enact_dc_restriction",
            "lift_dc_restriction",
        }:
            default_index = index
            break
    data = {
        "rounds": rounds,
        "water": _compact_water(water_rows or []),
        "model": model,
        "isMock": is_mock,
        "providerVerified": bool(is_mock or is_ds4),
        "providerBadge": provider_badge,
        "validCount": valid_count,
        "legacyLongCount": legacy_long_count,
        "normalizedCount": normalized_count,
        "heldCount": held_count,
        "eventCount": len(events),
        "defaultIndex": default_index,
        "accountingDays": accounting_days,
        "focusDays": focus_days,
    }
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Decision Timeline</title>
<style>
:root {{ color-scheme:dark; font-family:Inter,"Hiragino Sans","Yu Gothic",sans-serif; background:#06111e; color:#edf7ff; }}
* {{ box-sizing:border-box; }} body {{ margin:0; min-height:100vh; background:radial-gradient(circle at 10% 0,#153b62 0,transparent 30%),linear-gradient(145deg,#06111e,#0a1828 55%,#07111d); }}
main {{ width:min(1220px,calc(100% - 32px)); margin:auto; padding:24px 0 34px; }} .top {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-end; }}
.eyebrow {{ margin:0; color:#72d7ff; font:700 10px/1.2 ui-monospace,monospace; letter-spacing:.18em; text-transform:uppercase; }} h1 {{ margin:6px 0 4px; font-size:clamp(28px,4vw,48px); letter-spacing:-.045em; }} .lead {{ margin:0; color:#9fb5c9; font-size:13px; }}
.status {{ display:flex; gap:7px; flex-wrap:wrap; justify-content:flex-end; }} .pill {{ border:1px solid #28435c; border-radius:999px; padding:7px 10px; background:#0b2034; color:#cce8fa; font:700 10px/1 ui-monospace,monospace; }} .live {{ border-color:#287d5b; background:#0b2b22; color:#87ecb7; }} .mock {{ border-color:#8a681c; background:#2d230d; color:#ffd478; }}
.timeline {{ margin-top:16px; padding:14px 16px 12px; border:1px solid #243c53; border-radius:16px; background:rgba(8,25,41,.88); }} .chart-head {{ display:flex; justify-content:space-between; gap:12px; align-items:center; }} .chart-head b {{ font-size:13px; }} .chart-head span {{ color:#7892aa; font:600 10px/1 ui-monospace,monospace; }}
#chart {{ display:block; width:100%; height:112px; margin-top:7px; overflow:visible; }} .grid-line {{ stroke:#21394e; stroke-width:1; }} .threshold {{ stroke:#f2b84b; stroke-width:1; stroke-dasharray:5 5; }} .storage-area {{ fill:url(#waterFill); }} .storage-line {{ fill:none; stroke:#42cbff; stroke-width:3; }} .decision-dot {{ fill:#b88cff; stroke:#081728; stroke-width:2; }} .selected-dot {{ fill:#fff; stroke:#b88cff; stroke-width:4; }} .axis-label {{ fill:#7892aa; font-size:9px; font-family:ui-monospace,monospace; }}
.rounds {{ display:flex; gap:7px; overflow-x:auto; padding:8px 0 2px; scrollbar-width:thin; }} .round {{ flex:0 0 auto; min-width:92px; padding:7px 9px; border:1px solid #29445d; border-radius:9px; background:#0b1d2f; color:#9eb6ca; text-align:left; cursor:pointer; }} .round b,.round span {{ display:block; }} .round b {{ color:#dcedfa; font-size:11px; }} .round span {{ margin-top:3px; font:600 9px/1.2 ui-monospace,monospace; }} .round[aria-pressed="true"] {{ border-color:#a578ef; background:#271b42; color:#d7c3ff; box-shadow:0 0 0 1px #a578ef inset; }}
.cause {{ display:grid; grid-template-columns:1fr auto 1fr; gap:12px; align-items:center; margin:12px 0; }} .cause-card {{ min-height:70px; padding:11px 13px; border:1px solid #263f56; border-radius:12px; background:#0b1b2c; }} .cause-card small {{ display:block; color:#7893aa; font:700 9px/1.2 ui-monospace,monospace; letter-spacing:.08em; }} .cause-card strong {{ display:block; margin-top:5px; font-size:15px; }} .cause-card span {{ display:block; margin-top:3px; color:#91a9bd; font-size:11px; }} .arrow {{ color:#69d8ff; font-size:24px; }}
.metrics {{ display:flex; gap:14px; margin-top:7px; color:#9db3c6; font-size:11px; flex-wrap:wrap; }} .metrics b {{ color:#edf7ff; }} .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
.agent-card {{ min-height:220px; padding:14px; border:1px solid #263e55; border-top:3px solid var(--accent); border-radius:13px; background:linear-gradient(155deg,rgba(20,41,63,.97),rgba(7,21,36,.98)); }} .agent-card header {{ display:flex; align-items:center; gap:9px; }} .avatar {{ width:34px; height:34px; display:grid; place-items:center; border-radius:9px; background:var(--accent); color:white; font-weight:900; font-size:12px; }} .role-id {{ margin:0 0 2px; color:#6e8aa3; font:600 8px/1 ui-monospace,monospace; text-transform:uppercase; }} h2 {{ margin:0; font-size:15px; }} .valid {{ margin-left:auto; color:#75e5a8; background:#123529; border-radius:5px; padding:5px 6px; font:800 8px/1 ui-monospace,monospace; }} .valid.normalized,.valid.legacy {{ color:#ffd478; background:#3b2d0d; }} .valid.ng {{ color:#ffaaa4; background:#45201f; }}
.action {{ display:inline-block; margin:13px 0 9px; padding:6px 8px; border-left:3px solid var(--accent); background:#061422; font:800 10px/1 ui-monospace,monospace; }} .message {{ margin:0 0 10px; min-height:45px; font-size:12px; line-height:1.55; }} .reason {{ padding-top:9px; border-top:1px solid #26394d; color:#91a8bc; font-size:10px; line-height:1.5; }} .reason b {{ display:block; margin-bottom:3px; color:var(--accent); font:800 8px/1 ui-monospace,monospace; letter-spacing:.1em; }} .boundary {{ margin-top:10px; color:#6f8aa2; font-size:10px; text-align:center; }}
@media(max-width:900px) {{ .grid {{ grid-template-columns:repeat(2,1fr); }} }} @media(max-width:620px) {{ main {{ width:calc(100% - 20px); }} .top {{ align-items:flex-start; flex-direction:column; }} .status {{ justify-content:flex-start; }} .cause {{ grid-template-columns:1fr; }} .arrow {{ transform:rotate(90deg); text-align:center; }} .grid {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
<div class="top"><div><p class="eyebrow">Water Negotiation Lab / audited agent loop</p><h1>Agent Decision Timeline</h1><p class="lead">{accounting_days}日の水収支と、{focus_days}日間の定期・イベント駆動会議。判断の効果は翌日のPython計算へ反映。</p></div><div class="status" id="status"></div></div>
<section class="timeline"><div class="chart-head"><b>地域配水バッファ / 意思決定イベント</b><span>FOCUS DAY 1–{focus_days} · 破線は制限閾値25%</span></div><svg id="chart" role="img" aria-label="{focus_days}日間の貯水率とエージェント会議"><defs><linearGradient id="waterFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#36c7ff" stop-opacity=".35"/><stop offset="1" stop-color="#36c7ff" stop-opacity=".02"/></linearGradient></defs><g id="chartBody"></g></svg><div class="rounds" id="rounds" aria-label="意思決定ラウンド"></div></section>
<div class="cause"><div class="cause-card"><small id="decisionLabel">DECISION</small><strong id="decisionAction">—</strong><span id="decisionReason">—</span></div><div class="arrow">→</div><div class="cause-card"><small id="effectLabel">NEXT-DAY PYTHON EFFECT</small><strong id="effectValue">—</strong><span id="effectDetail">—</span></div></div>
<section class="grid" id="cards"></section><div class="boundary">LLMは action・message・reason のみ生成。需要・供給・貯水・不足・政策倍率は決定論的Pythonが計算します。</div>
</main><script>
const DATA={_json_for_script(data)};
const roles={{resident_representative:['住民代表','#2f80ed','住'],municipality:['自治体','#9b51e0','自'],water_utility:['水道事業者','#219653','水'],datacenter_operator:['DC事業者','#f2994a','DC']}};
const reasons={{scheduled_review:'定期レビュー',storage_restriction_threshold_crossed:'貯水率25%を下回った',storage_lift_threshold_crossed:'解除水位へ回復',resident_shortage_started:'住民不足が発生',datacenter_shortage_started:'DC用水不足が発生'}};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); const fmt=v=>{{v=Number(v||0);return v>=1e6?(v/1e6).toFixed(2)+' ML':v>=1e3?(v/1e3).toFixed(1)+' kL':Math.round(v)+' L'}}; let selected=DATA.defaultIndex;
document.getElementById('status').innerHTML=`<span class="pill ${{DATA.providerVerified&&!DATA.isMock?'live':'mock'}}">${{esc(DATA.providerBadge)}}</span><span class="pill">${{esc(DATA.model)}}</span><span class="pill">${{DATA.rounds.length}} ROUNDS / ${{DATA.eventCount}} DECISIONS</span><span class="pill">${{DATA.validCount}} STRICT${{DATA.normalizedCount?' + '+DATA.normalizedCount+' NORMALIZED':''}}${{DATA.legacyLongCount?' + '+DATA.legacyLongCount+' LEGACY LONG':''}}</span>`;
function rowFor(day){{return DATA.water.find(r=>r.day===day)||null}}
function drawChart(){{const svg=document.getElementById('chart'),g=document.getElementById('chartBody'),w=Math.max(320,svg.getBoundingClientRect().width||1100),h=112,l=30,r=10,t=8,b=17,pw=w-l-r,ph=h-t-b;svg.setAttribute('viewBox',`0 0 ${{w}} ${{h}}`);g.innerHTML='';if(!DATA.water.length)return;const x=d=>l+(d-1)/Math.max(1,DATA.water.length-1)*pw,y=p=>t+(100-Math.max(0,Math.min(100,p)))/100*ph,ns='http://www.w3.org/2000/svg',add=(tag,a,txt)=>{{const e=document.createElementNS(ns,tag);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));if(txt)e.textContent=txt;g.appendChild(e);return e}};[25,50,100].forEach(p=>{{add('line',{{x1:l,x2:w-r,y1:y(p),y2:y(p),class:p===25?'threshold':'grid-line'}});add('text',{{x:l-4,y:y(p)+3,'text-anchor':'end',class:'axis-label'}},p+'%')}});const pts=DATA.water.map(d=>`${{x(d.day).toFixed(1)}},${{y(d.storagePct).toFixed(1)}}`).join(' ');add('path',{{d:`M${{l}},${{h-b}} L${{pts.replaceAll(' ',' L')}} L${{x(DATA.water.at(-1).day)}},${{h-b}} Z`,class:'storage-area'}});add('polyline',{{points:pts,class:'storage-line'}});DATA.rounds.forEach((q,i)=>add('circle',{{cx:x(q.day),cy:y((rowFor(q.day)||{{storagePct:0}}).storagePct),r:i===selected?5:3.5,class:i===selected?'selected-dot':'decision-dot'}}));const ticks=DATA.focusDays===1?[1]:[1,Math.max(1,Math.round(DATA.focusDays/3)),Math.max(1,Math.round(DATA.focusDays*2/3)),DATA.focusDays];ticks.forEach((d,i)=>add('text',{{x:x(d),y:h-3,'text-anchor':i===0?'start':i===ticks.length-1?'end':'middle',class:'axis-label'}},'D'+d));}}
function render(){{const q=DATA.rounds[selected],row=rowFor(q.day),next=rowFor(q.day+1),municipality=q.events.find(e=>e.role.role==='municipality');document.getElementById('rounds').innerHTML=DATA.rounds.map((item,i)=>`<button class="round" aria-pressed="${{i===selected}}" data-i="${{i}}"><b>ROUND ${{item.round}} · D${{item.day}}</b><span>${{esc(reasons[item.reason]||item.reason)}}</span></button>`).join('');document.querySelectorAll('.round').forEach(b=>b.onclick=()=>{{selected=Number(b.dataset.i);render()}});const action=municipality?.parsed_response.action||'—';document.getElementById('decisionLabel').textContent=`ROUND ${{q.round}} / DAY ${{q.day}} · ${{q.date}}`;document.getElementById('decisionAction').textContent=action;document.getElementById('decisionReason').textContent=reasons[q.reason]||q.reason;const before=row?.restriction??1,after=row?.nextRestriction??before;document.getElementById('effectLabel').textContent=`DAY ${{q.day+1}} PYTHON EFFECT`;document.getElementById('effectValue').textContent=`DC上水供給上限 ${{(before*100).toFixed(0)}}% → ${{(after*100).toFixed(0)}}%`;document.getElementById('effectDetail').textContent=next?`翌日: 貯水率 ${{next.storagePct.toFixed(1)}}% / DC取水 ${{fmt(next.dcWithdrawalL)}} / DC不足 ${{fmt(next.dcShortageL)}}`:'期間末のため翌日データなし';document.getElementById('cards').innerHTML=q.events.map(e=>{{const [label,accent,mono]=roles[e.role.role]||[e.role.label_ja,'#64748b','AI'],p=e.display_response||e.parsed_response,u=e.provider_metadata?.usage?.total_tokens??'—',phase=e.role.decision_phase==='policy_resolution'||e.role.role==='municipality'?'POLICY RESOLUTION':'STAKEHOLDER INPUT',legacy=e.display_status==='legacy_long',normalized=e.display_status==='normalized',badge=legacy?'旧監査の長文・RAW保存':normalized?'TEXT SHORTENED · ACTION KEPT':e.valid?'JSON VALID':'形式エラー・行動保留',badgeClass=legacy?'legacy':normalized?'normalized':e.valid?'':'ng';return `<article class="agent-card" style="--accent:${{accent}}"><header><span class="avatar">${{mono}}</span><div><p class="role-id">${{phase}} · ${{esc(e.role.role)}}</p><h2>${{esc(label)}}</h2></div><span class="valid ${{badgeClass}}">${{badge}}</span></header><div class="action">${{esc(p.action+(legacy?'（当時は保留）':''))}}</div><p class="message">${{esc(p.message)}}</p><div class="reason"><b>REASON</b>${{esc(p.reason)}}</div><div class="metrics"><span>${{u}} tokens</span>${{row?`<span>貯水率 <b>${{row.storagePct.toFixed(1)}}%</b></span>`:''}}</div></article>`}}).join('');drawChart();}}
new ResizeObserver(drawChart).observe(document.getElementById('chart'));render();
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render audited agent JSONL as a decision timeline")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--water-balance", type=Path)
    args = parser.parse_args()
    water_rows = _read_jsonl(args.water_balance) if args.water_balance else []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(_read_jsonl(args.input), water_rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
