"""
QA 실행 결과 -> HTML 리포트 생성.
qa_run.py 에서 호출한다. 단독 실행하지 않음.
"""

import html
import json
from datetime import datetime
from pathlib import Path

STATUS_META = {
    "PASS":    ("통과",     "#0f7b3f", "#e7f6ed"),
    "FAIL":    ("실패",     "#c0362c", "#fdecea"),
    "SKIPPED": ("승인 대기", "#8a6100", "#fff6e0"),
    "BLOCKED": ("차단",     "#5b4bbd", "#eeebfa"),
}


def _esc(s):
    return html.escape(str(s or ""))


def _case_block(r):
    label, color, bg = STATUS_META[r["status"]]

    steps = []
    for s in r["steps"]:
        icon = {"OK": "O", "FAIL": "X", "SKIP": "-"}[s["status"]]
        cls = "step-fail" if s["status"] == "FAIL" else (
            "step-skip" if s["status"] == "SKIP" else "")
        wtag = '<span class="wtag">쓰기</span>' if s["write"] else ""
        phase = '<span class="ptag">정리</span>' if s["phase"] == "cleanup" else ""
        msg = '<div class="stepmsg">{}</div>'.format(_esc(s["msg"])) if s["msg"] else ""
        steps.append(
            '<li class="{}"><code>{}</code> {} {}{}{}</li>'.format(
                cls, icon, _esc(s["label"]), phase, wtag, msg)
        )

    shots = "".join(
        '<figure><img src="screenshots/{0}" alt="{0}" loading="lazy" '
        'onclick="zoom(this)"><figcaption>{0}</figcaption></figure>'.format(_esc(n))
        for n in r["shots"]
    )

    return """
<section class="case">
  <header>
    <span class="badge" style="color:{color};background:{bg}">{label}</span>
    <h3>{id} &middot; {title}</h3>
    <span class="dur">{dur}s</span>
  </header>
  <dl>
    <dt>검증 요건</dt><dd>{req}</dd>
    <dt>근거</dt><dd class="src">{src}</dd>
    {reason}
  </dl>
  {steps_html}
  {shots_html}
</section>""".format(
        color=color, bg=bg, label=label,
        id=_esc(r["id"]), title=_esc(r["title"]), dur=r["duration"],
        req=_esc(r["requirement"]), src=_esc(r["source"]),
        reason='<dt>사유</dt><dd class="reason">{}</dd>'.format(_esc(r["reason"])) if r["reason"] else "",
        steps_html='<ol class="steps">{}</ol>'.format("".join(steps)) if steps else "",
        shots_html='<div class="shots">{}</div>'.format(shots) if shots else "",
    )


def build_report(spec, results, warnings, out_dir, dry_run):
    meta = spec["meta"]
    counts = {k: sum(1 for r in results if r["status"] == k) for k in STATUS_META}

    cards = "".join(
        '<div class="card" style="background:{bg}"><b style="color:{c}">{n}</b><span>{l}</span></div>'.format(
            bg=STATUS_META[k][2], c=STATUS_META[k][1], n=counts[k], l=STATUS_META[k][0])
        for k in ("PASS", "FAIL", "SKIPPED", "BLOCKED")
    )

    warn_html = ""
    if warnings:
        items = "".join("<li>{}</li>".format(_esc(w)) for w in warnings)
        warn_html = '<div class="warn"><b>경고 {}건</b><ul>{}</ul></div>'.format(len(warnings), items)

    pending = [r for r in results if r["status"] == "SKIPPED"]
    pend_html = ""
    if pending:
        items = "".join(
            "<li><code>{}</code> {} &mdash; <span>{}</span></li>".format(
                _esc(r["id"]), _esc(r["title"]), _esc(r["reason"]))
            for r in pending)
        pend_html = (
            '<div class="pending"><b>승인 대기 {}건</b>'
            '<p>쓰기가 필요한 케이스입니다. 검토 후 spec.json에서 해당 케이스의 '
            '<code>"approved": true</code>로 바꾸고 재실행하세요.</p><ul>{}</ul></div>'
        ).format(len(pending), items)

    sources = "".join("<li>{}</li>".format(_esc(s)) for s in meta.get("sources", []))

    doc = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>QA 리포트 - {project}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:32px; background:#f5f6f8; color:#1c1f23;
         font:14px/1.6 'Malgun Gothic','맑은 고딕',system-ui,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:#6b7280; font-size:13px; margin-bottom:24px; }}
  .drytag {{ display:inline-block; background:#fff6e0; color:#8a6100;
             padding:2px 8px; border-radius:4px; font-size:12px; font-weight:700; }}
  .cards {{ display:flex; gap:12px; margin-bottom:24px; }}
  .card {{ flex:1; padding:16px; border-radius:8px; text-align:center; }}
  .card b {{ display:block; font-size:28px; line-height:1.2; }}
  .card span {{ font-size:12px; color:#4b5563; }}
  .warn, .pending {{ padding:16px; border-radius:8px; margin-bottom:20px; font-size:13px; }}
  .warn {{ background:#fff6e0; border-left:4px solid #d99a00; }}
  .pending {{ background:#eef3ff; border-left:4px solid #3b6fd4; }}
  .warn ul, .pending ul {{ margin:8px 0 0; padding-left:20px; }}
  .pending p {{ margin:6px 0 0; color:#374151; }}
  .case {{ background:#fff; border:1px solid #e3e6ea; border-radius:8px;
           padding:20px; margin-bottom:16px; }}
  .case header {{ display:flex; align-items:center; gap:10px; margin-bottom:12px; }}
  .case h3 {{ font-size:15px; margin:0; flex:1; }}
  .badge {{ padding:3px 10px; border-radius:4px; font-size:12px; font-weight:700; }}
  .dur {{ color:#9ca3af; font-size:12px; }}
  dl {{ display:grid; grid-template-columns:80px 1fr; gap:4px 12px;
        margin:0 0 12px; font-size:13px; }}
  dt {{ color:#6b7280; }}
  dd {{ margin:0; }}
  .src {{ color:#6b7280; font-size:12px; }}
  .reason {{ color:#c0362c; }}
  .steps {{ margin:0; padding-left:22px; font-size:12.5px; color:#374151; }}
  .steps li {{ padding:2px 0; }}
  .steps code {{ font-weight:700; margin-right:4px; }}
  .step-fail {{ color:#c0362c; }}
  .step-skip {{ color:#9ca3af; }}
  .stepmsg {{ background:#fdecea; color:#c0362c; padding:6px 10px;
              border-radius:4px; margin:4px 0; font-size:12px; }}
  .wtag {{ background:#fdecea; color:#c0362c; font-size:10px;
           padding:1px 5px; border-radius:3px; margin-left:4px; }}
  .ptag {{ background:#eef1f5; color:#4b5563; font-size:10px;
           padding:1px 5px; border-radius:3px; margin-left:4px; }}
  .shots {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:14px; }}
  figure {{ margin:0; width:340px; }}
  figure img {{ width:100%; max-height:260px; object-fit:cover; object-position:top;
                border:1px solid #e3e6ea; border-radius:4px; cursor:zoom-in;
                background:#fff; transition:border-color .12s; }}
  figure img:hover {{ border-color:#3b6fd4; }}
  figcaption {{ font-size:11px; color:#9ca3af; margin-top:4px;
                word-break:break-all; }}

  /* 확대 보기 */
  #lb {{ position:fixed; inset:0; background:rgba(17,20,24,.92); z-index:999;
         display:none; overflow:auto; }}
  #lb.on {{ display:block; }}
  #lb .bar {{ position:sticky; top:0; display:flex; align-items:center; gap:14px;
              padding:10px 16px; background:rgba(17,20,24,.96); color:#e5e7eb;
              font-size:12px; }}
  #lb .bar b {{ font-weight:600; flex:1; word-break:break-all; }}
  #lb button {{ background:#374151; color:#e5e7eb; border:0; border-radius:4px;
                padding:6px 12px; font-size:12px; cursor:pointer; font-family:inherit; }}
  #lb button:hover {{ background:#4b5563; }}
  #lb .stage {{ padding:16px; text-align:center; min-height:calc(100% - 37px); }}
  #lb img {{ max-width:100%; background:#fff; border-radius:4px; cursor:zoom-in; }}
  #lb.full img {{ max-width:none; cursor:zoom-out; }}
  footer {{ margin-top:28px; padding-top:16px; border-top:1px solid #e3e6ea;
            font-size:12px; color:#6b7280; }}
  footer ul {{ margin:4px 0 0; padding-left:20px; }}
</style></head><body><div class="wrap">
<h1>QA 자동화 리포트 &mdash; {project}</h1>
<div class="sub">{stamp} &middot; {base} {dry}</div>
<div class="cards">{cards}</div>
{warn}{pending}
{cases}
<footer>
  <b>근거 자료</b>
  <ul>{sources}</ul>
</footer>
</div>
<div id="lb">
  <div class="bar">
    <b id="lbname"></b>
    <button onclick="toggleFull(event)" id="lbfit">원본 크기</button>
    <button onclick="closeLb(event)">닫기 (Esc)</button>
  </div>
  <div class="stage"><img id="lbimg" alt="" onclick="toggleFull(event)"></div>
</div>
<script>
  var lb = document.getElementById('lb');
  function zoom(el) {{
    document.getElementById('lbimg').src = el.src;
    document.getElementById('lbname').textContent = el.alt;
    lb.classList.remove('full');
    document.getElementById('lbfit').textContent = '원본 크기';
    lb.classList.add('on');
    lb.scrollTop = 0;
    document.body.style.overflow = 'hidden';
  }}
  function closeLb(e) {{
    if (e) e.stopPropagation();
    lb.classList.remove('on');
    document.body.style.overflow = '';
  }}
  function toggleFull(e) {{
    if (e) e.stopPropagation();
    lb.classList.toggle('full');
    document.getElementById('lbfit').textContent =
      lb.classList.contains('full') ? '화면에 맞춤' : '원본 크기';
  }}
  // 이미지·버튼 밖(오버레이 여백)을 누르면 닫는다. stage 가 lb 를 덮고 있으므로 같이 본다.
  lb.addEventListener('click', function (e) {{
    if (e.target === lb || e.target.classList.contains('stage')) closeLb();
  }});
  document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape') closeLb(); }});
</script>
</body></html>""".format(
        project=_esc(meta["project"]),
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        base=_esc(meta["base_url"]),
        dry='<span class="drytag">DRY-RUN (쓰기 없음)</span>' if dry_run else "",
        cards=cards, warn=warn_html, pending=pend_html,
        cases="".join(_case_block(r) for r in results),
        sources=sources,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "리포트.html"
    path.write_text(doc, encoding="utf-8")

    # 원본 결과도 남긴다 (재분석 · 이력 비교용)
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    return path
