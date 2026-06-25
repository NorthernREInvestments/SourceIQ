"""HTML report generation."""

import html
import json
import os
import webbrowser
from datetime import datetime

from market_spy.analysis import compute_market_opportunity, compute_price_range
from market_spy.config import REPORTS_DIR
from market_spy.utils import age_label, safe_niche_slug

REPORT_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>SourceIQ — PLACEHOLDER_NICHE</title>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <link href='https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap' rel='stylesheet'>
  <link rel='stylesheet' href='https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css'>
  <style>
    body{background:#071021;color:#e6eef6;font-family:Inter,system-ui,Segoe UI,Roboto,Arial;margin:0;padding:24px}
    .container{max-width:1200px;margin:20px auto}
    .card{background:#071627;border:1px solid rgba(255,255,255,0.03);padding:18px;border-radius:12px;margin-bottom:16px}
    h1{margin:0}.muted{color:#9aa8bd}
    table.dataTable thead th{color:#e6eef6}
    a{color:#4fd1c5}
  </style>
</head>
<body>
  <div class='container'>
    <div class='card'><h1>SourceIQ — PLACEHOLDER_NICHE</h1>
    <div class='muted'>Collected on PLACEHOLDER_NOW — Data freshness: PLACEHOLDER_FRESHNESS% in last 30 days</div>
    <div style='margin-top:8px'><strong>Market Opportunity Score: PLACEHOLDER_OPPORTUNITY/100</strong></div>
    <div class='muted' style='margin-top:6px'>Price range: PLACEHOLDER_PRICE_MIN to PLACEHOLDER_PRICE_MAX</div>
    </div>
    <div class='card'>
      <h3>Google Trends (3 months)</h3>
      <canvas id='trendChart' style='height:200px'></canvas>
    </div>

    <div class='card'>
      <h3>All Results</h3>
      <table id='results' class='display' style='width:100%'>
        <thead><tr><th>Source</th><th>Title</th><th>Price (last confirmed)</th><th>Engagement</th><th>Date (age)</th></tr></thead>
        <tbody>
          PLACEHOLDER_ROWS
        </tbody>
      </table>
    </div>

  </div>
  <script src='https://code.jquery.com/jquery-3.6.0.min.js'></script>
  <script src='https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js'></script>
  <script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
  <script>
    $(document).ready(function(){
      $('#results').DataTable({pageLength:25,lengthChange:false,order:[[4,'desc']]});
      var trends = PLACEHOLDER_TRENDS_JSON;
      try{
        if(trends){
          var ctx = document.getElementById('trendChart').getContext('2d');
          new Chart(ctx,{type:'line',data:{labels:trends.labels, datasets:[{label:'Interest',data:trends.values,borderColor:'#4fd1c5',backgroundColor:'rgba(79,209,197,0.08)',tension:0.2}]},options:{scales:{x:{ticks:{color:'#9aa8bd'}},y:{ticks:{color:'#9aa8bd'}}},plugins:{legend:{labels:{color:'#9aa8bd'}}}}});
        }
      }catch(e){console.warn(e)}
    });
  </script>
</body>
</html>
"""


def generate_report(niche, items, trends, out_dir=None, open_after=True):
    out_dir = out_dir or REPORTS_DIR
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.utcnow()
    ts = now.strftime("%Y%m%d_%H%M")
    safe_niche = safe_niche_slug(niche)
    filename = f"market_spy_{safe_niche}_{ts}.html"
    out_path = os.path.join(out_dir, filename)
    total = len(items)
    fresh30 = sum(1 for i in items if i.get("date") and (now - i["date"]).days <= 30)
    freshness_pct = round((fresh30 / total * 100) if total else 0, 1)
    price_min, price_max = compute_price_range(items)
    opportunity = compute_market_opportunity(items, trends)
    rows = ""
    for it in sorted(
        items,
        key=lambda x: ((now - x["date"]).days if x.get("date") else 9999, -x.get("engagement", 0)),
    ):
        date = it.get("date")
        date_str = date.strftime("%Y-%m-%d") if date else "UNDATED"
        age_tag, color = age_label(date)
        price = it.get("price")
        price_str = f"${price:.2f}" if price is not None else "—"
        price_date = it.get("price_last_confirmed")
        price_ts = price_date.strftime("%Y-%m-%d") if price_date else "UNVERIFIED"
        price_warning = ""
        if price is not None and (not price_date or (now - price_date).days > 365):
            price_warning = (
                "<span style='color:#ff4d4f;font-weight:700;margin-left:6px'>"
                "PRICE UNVERIFIED — may be outdated</span>"
            )
        if price is not None and price_date and (now - price_date).days > 365:
            price_warning = (
                "<span style='color:#ff0033;font-weight:700;margin-left:6px'>"
                "STALE LISTING — verify before using</span>"
            )
        rows += (
            f"<tr><td>{html.escape(str(it.get('source') or ''))}</td>"
            f"<td><a href='{html.escape(str(it.get('url') or '#'))}' target='_blank'>"
            f"{html.escape(str(it.get('name') or ''))}</a></td>"
            f"<td>{price_str} <small style='color:#999'>— last confirmed {price_ts}</small> "
            f"{price_warning}</td>"
            f"<td>{it.get('engagement', 0)}</td>"
            f"<td>{date_str} <span style='color:{color};font-weight:700;margin-left:6px'>"
            f"{age_tag}</span></td></tr>\n"
        )
    trends_json = (
        json.dumps({"labels": [d[0] for d in trends], "values": [d[1] for d in trends]})
        if trends
        else "null"
    )
    report_html = (
        REPORT_TEMPLATE.replace("PLACEHOLDER_NICHE", html.escape(niche))
        .replace("PLACEHOLDER_NOW", now.strftime("%Y-%m-%d %H:%M UTC"))
        .replace("PLACEHOLDER_FRESHNESS", str(freshness_pct))
        .replace("PLACEHOLDER_OPPORTUNITY", str(opportunity))
        .replace(
            "PLACEHOLDER_PRICE_MIN",
            ("$" + str(round(price_min, 2)) if price_min else "—"),
        )
        .replace(
            "PLACEHOLDER_PRICE_MAX",
            ("$" + str(round(price_max, 2)) if price_max else "—"),
        )
        .replace("PLACEHOLDER_ROWS", rows)
        .replace("PLACEHOLDER_TRENDS_JSON", trends_json)
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_html)
    print(f"Report saved to {out_path}")
    try:
        if open_after:
            if os.name == "nt":
                os.startfile(os.path.abspath(out_path))
            else:
                webbrowser.open("file://" + os.path.abspath(out_path))
    except Exception:
        pass
    return out_path
