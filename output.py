"""Writes surfaced results as CSV, JSON, and Markdown table, sorted by match score
descending. Columns match the spec: company, role title, role family, experience
level, location, remote status, sponsorship flag, match score, top matched
skills/keywords, posted date, apply link, source tier used.
"""
import csv
import html
import json
from datetime import datetime
from pathlib import Path

from tagger import experience_min_years

COLUMNS = [
    "company", "title", "posted_date", "experience_required", "senior_title_note",
    "match_score", "role_family", "seniority", "location", "remote_status",
    "sponsorship_flag", "top_keywords", "apply_link", "source_tier",
]


def _row(job: dict) -> dict:
    return {
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "role_family": job.get("role_family", ""),
        "seniority": job.get("seniority", ""),
        "experience_required": job.get("experience_required", "not stated"),
        # Title reads senior/staff/principal/etc. but the parsed experience bar is
        # <4 yrs or unstated — flagged rather than hidden, per user preference.
        "senior_title_note": "senior title, low/unstated exp" if job.get("senior_title_flag") else "",
        "location": job.get("location", ""),
        "remote_status": job.get("remote_status", ""),
        "sponsorship_flag": job.get("sponsorship_flag", ""),
        "match_score": job.get("match_score", 0.0),
        "top_keywords": ", ".join(job.get("top_keywords_list", [])) or job.get("top_keywords", ""),
        "posted_date": job.get("posted_date", ""),
        "apply_link": job.get("apply_link", ""),
        "source_tier": job.get("source_tier", ""),
    }


def write_outputs(jobs: list, out_dir: Path, run_stamp: str, suffix: str = ""):
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(jobs, key=lambda j: j.get("match_score", 0.0), reverse=True)
    rows = [_row(j) for j in ranked]

    csv_path = out_dir / f"jobs_{run_stamp}{suffix}.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    json_path = out_dir / f"jobs_{run_stamp}{suffix}.json"
    json_path.write_text(json.dumps(rows, indent=2))

    md_path = out_dir / f"jobs_{run_stamp}{suffix}.md"
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for row in rows:
        cells = [str(row[c]).replace("|", "/").replace("\n", " ")[:80] for c in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    md_path.write_text("\n".join(lines))

    return {"csv": csv_path, "json": json_path, "md": md_path, "count": len(rows)}


_HTML_HEAD = """<!doctype html><meta charset=utf-8><title>Job Matches</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}
h1{font-size:20px;margin:0 0 4px}p.sub{color:#94a3b8;font-size:13px;margin:0 0 16px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #1e293b;vertical-align:top}
th{color:#94a3b8;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.score{font-weight:700;font-size:15px}.t{color:#cbd5e1}.dim{color:#64748b}
.btn{display:inline-block;background:#2563eb;color:#fff;padding:6px 12px;border-radius:6px;
text-decoration:none;white-space:nowrap;font-weight:600}.btn:hover{background:#1d4ed8}
th.s{cursor:pointer;user-select:none;white-space:nowrap}th.s:hover{color:#e2e8f0}
th.s .ar{color:#2563eb;font-size:10px}th.s .lv{color:#64748b;font-size:9px;vertical-align:super}
p.hint{color:#64748b;font-size:12px;margin:0 0 12px}
</style>"""

# Click-to-sort, Excel style. Vanilla JS and inline — a published page has no network
# access to a CDN, and the file is opened straight off disk.
#
# Multi-level sort used to require shift-click to add a tie-breaker column (plain click
# always replaced the whole sort). That modifier key wasn't reliably reaching us — reports
# of "plain click just re-sorts by that column alone" with nothing added — so clicking
# ADDS a level by default now; there's no way to trigger the old collapse-to-one-column
# behavior by accident. A "reset sort" link clears back to a single column when needed.
_HTML_SORT_JS = """<script>
(function(){
 var tb=document.querySelector('table'),levels=[];
 // Each cell carries data-v (sort value) and data-n="1" when the value is missing
 // ("not stated" / "unknown"). Missing values always sink to the bottom regardless of
 // direction, so flipping a column never floats a row you can't evaluate to the top.
 function cmp(a,b,i,dir){
  var ca=a.cells[i],cb=b.cells[i];
  var na=ca.dataset.n==='1',nb=cb.dataset.n==='1';
  if(na!==nb)return na?1:-1;
  if(na&&nb)return 0;
  var va=ca.dataset.v,vb=cb.dataset.v;
  // Test the WHOLE string, not parseFloat: parseFloat('2026-08-03') is 2026, which
  // made every date in a year compare equal and silently no-op the date sort.
  var num=/^-?\\d+(\\.\\d+)?$/;
  var r=(num.test(va)&&num.test(vb))?(parseFloat(va)-parseFloat(vb))
                                    :String(va).localeCompare(String(vb));
  return r*dir;
 }
 function apply(){
  var rows=[].slice.call(tb.tBodies[0].rows);
  rows.sort(function(a,b){
   for(var k=0;k<levels.length;k++){
    var r=cmp(a,b,levels[k].i,levels[k].dir);
    if(r)return r;
   }
   return 0;
  });
  rows.forEach(function(r){tb.tBodies[0].appendChild(r);});
  [].forEach.call(tb.tHead.rows[0].cells,function(th,i){
   var base=th.dataset.label||'';
   var at=-1;
   for(var k=0;k<levels.length;k++)if(levels[k].i===i)at=k;
   if(at<0){th.innerHTML=base;return;}
   var arrow=levels[at].dir>0?'\\u25b2':'\\u25bc';
   var lv=levels.length>1?'<span class=lv>'+(at+1)+'</span>':'';
   th.innerHTML=base+' <span class=ar>'+arrow+'</span>'+lv;
  });
  var reset=document.getElementById('resetSort');
  if(reset)reset.style.display=levels.length>1?'inline':'none';
 }
 [].forEach.call(tb.tHead.rows[0].cells,function(th,i){
  if(!th.classList.contains('s'))return;
  th.dataset.label=th.innerHTML;
  th.addEventListener('click',function(){
   var at=-1;
   for(var k=0;k<levels.length;k++)if(levels[k].i===i)at=k;
   if(at<0){                            // new column: add as next tie-breaker level
    levels.push({i:i,dir:th.dataset.dir==='asc'?1:-1});
   }else{                               // already a level: flip its direction in place
    levels[at].dir*=-1;
   }
   apply();
  });
 });
 var resetLink=document.getElementById('resetSort');
 if(resetLink)resetLink.addEventListener('click',function(e){
  e.preventDefault();
  if(levels.length)levels=[levels[0]];
  apply();
 });
 apply();
})();
</script>"""


def write_html(jobs: list, out_dir: Path, run_stamp: str) -> dict:
    """Write a clickable, ranked HTML page of every job (each row has an Apply button).
    Writes both a timestamped copy and a stable `job_matches.html` that always reflects
    the most recent fetch, so the user has one bookmarkable file to open + click through."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(jobs, key=lambda j: j.get("match_score", 0.0), reverse=True)
    rows = []
    for j in ranked:
        score = j.get("match_score", 0.0)
        co = html.escape(str(j.get("company", "")).split(" (via")[0])
        title = html.escape(str(j.get("title", "")))
        loc = html.escape(str(j.get("location", "") or ""))
        sal = html.escape(str(j.get("salary", "") or ""))
        posted = html.escape(str(j.get("posted_date", "") or "unknown"))
        exp = html.escape(str(j.get("experience_required", "") or "not stated"))
        exp_cls = " class=dim" if exp == "not stated" else ""
        link = html.escape(str(j.get("apply_link", "")))
        spons = html.escape(str(j.get("sponsorship_flag", "")))
        color = "#16a34a" if score >= 30 else ("#ca8a04" if score >= 20 else "#64748b")
        apply_cell = (f'<a class=btn href="{link}" target=_blank rel=noopener>Apply →</a>'
                      if link else "")
        # Sort keys, read by the click-to-sort script. data-n="1" marks a missing value
        # so it sinks to the bottom in both directions instead of topping an asc sort.
        exp_years = experience_min_years(j.get("experience_required", ""))
        exp_v = "" if exp_years is None else exp_years
        exp_n = ' data-n="1"' if exp_years is None else ""
        posted_raw = str(j.get("posted_date", "") or "")[:10]
        posted_n = ' data-n="1"' if not posted_raw else ""
        # Title reads senior/staff/principal/etc. but parsed experience is <4 yrs or
        # unstated — flagged inline rather than excluded, per user preference.
        senior_badge = (' <span style="color:#ca8a04;font-size:.75em;font-weight:600">'
                         '[senior title, low/unstated exp]</span>'
                         if j.get("senior_title_flag") else "")
        rows.append(
            f'<tr><td class=score style="color:{color}" data-v="{score:.1f}">{score:.1f}</td>'
            f'<td data-v="{co} {title}"><b>{co}</b><br><span class=t>{title}</span>{senior_badge}</td>'
            f'<td data-v="{loc}">{loc}</td><td data-v="{sal}">{sal}</td>'
            f'<td data-v="{posted_raw}"{posted_n}>{posted}</td>'
            f'<td{exp_cls} data-v="{exp_v}"{exp_n}><b>{exp}</b></td>'
            f'<td data-v="{spons}">{spons}</td>'
            f'<td>{apply_cell}</td></tr>'
        )
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = (
        _HTML_HEAD
        + f'<h1>Job Matches</h1><p class=sub>{len(ranked)} US postings ranked by match '
        f'score vs your resume · fetched {gen} · <b style="color:#16a34a">green ≥30</b>, '
        f'<b style="color:#ca8a04">yellow ≥20</b></p>'
        + '<p class=hint>Click a column to sort by it · click a column already sorted to '
          'reverse it · click another column to add it as a tie-breaker (Excel-style '
          'multi-level sort, no modifier key needed) · '
          '<a href="#" id=resetSort style="display:none">reset to single-column sort</a></p>'
        + '<table><thead><tr>'
          '<th class=s data-dir=desc>Score</th>'
          '<th class=s data-dir=asc>Company / Role</th>'
          '<th class=s data-dir=asc>Location</th>'
          '<th class=s data-dir=asc>Salary</th>'
          '<th class=s data-dir=desc>Posted</th>'
          '<th class=s data-dir=asc>Experience</th>'
          '<th class=s data-dir=asc>Sponsor</th>'
          '<th></th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table>" + _HTML_SORT_JS
    )
    stable = out_dir / "job_matches.html"
    stable.write_text(page)
    stamped = out_dir / f"job_matches_{run_stamp}.html"
    stamped.write_text(page)
    return {"html": stable, "html_stamped": stamped, "count": len(ranked)}
