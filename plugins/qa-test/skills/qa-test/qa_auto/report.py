"""
QA 실행 결과 -> HTML 리포트 + 개발 전달용 마크다운 생성.
qa_run.py 에서 호출한다. 단독 실행하지 않음.

리포트를 두 갈래로 나눈다. 기획팀이 먼저 전체를 보고, 그중 개발팀이 손댈 것만
추려서 넘기는 흐름이기 때문이다. 기획서에 근거가 없는 개선 제안이나 기획 자체를
고쳐야 하는 항목까지 같이 넘기면 "기획도 안 끝났는데 왜 우리한테 주냐"가 되고,
정작 근거가 확실한 결함까지 같이 흐려진다.
"""

import html
import json
from datetime import datetime
from pathlib import Path

STATUS_META = {
    "PASS":    ("통과",       "#0f7b3f", "#e7f6ed"),
    "FAIL":    ("실패",       "#c0362c", "#fdecea"),
    # 화면 판정까지 못 간 것 (요소 없음 · 시간 초과 · 설정 오류). 결함인지 스크립트 문제인지 사람이 본다
    "ERROR":   ("점검 필요",   "#9a3412", "#fff1e6"),
    # precondition 이 안 맞음. 운영 데이터가 바뀌어 검수할 대상이 없는 것
    "NA":      ("조건 미충족", "#4b5563", "#eef1f5"),
    "SKIPPED": ("승인 대기",   "#8a6100", "#fff6e0"),
    "BLOCKED": ("차단",       "#5b4bbd", "#eeebfa"),
}
CHIP_ORDER = ("FAIL", "ERROR", "PASS", "NA", "SKIPPED", "BLOCKED")

# 케이스의 성격. 실패했을 때 그 실패를 누구에게 넘기는지가 달라진다.
#   to_dev=True  -> 확정 기획서에 근거가 있으므로 구현 결함. 개발팀 전달 대상.
#   to_dev=False -> 기획팀이 먼저 판단해야 하는 것. 개발팀에 넘기지 않는다.
KIND_META = {
    "defect":  ("결함",      True),
    "improve": ("개선 요청",  False),
    "gap":     ("기획 보완",  False),
    "qa":      ("QA 데이터",  False),   # 잔여 데이터 점검. 우리 쪽 정리 문제라 전달 대상 아님
}
DEFAULT_KIND = "defect"

# 같은 영역 안에서 개발 전달 순서를 정한다. 없으면 맨 뒤.
SEVERITY_RANK = {"치명": 0, "주요": 1, "경미": 2}

TREND_META = {"fixed": "수정됨", "still": "계속 실패", "new": "새로 실패"}


def _esc(s):
    return html.escape(str(s or ""))


def _kind_of(r):
    k = (r.get("kind") or DEFAULT_KIND).strip()
    return k if k in KIND_META else DEFAULT_KIND


def _dev_items(results):
    """개발팀에 넘길 항목만 추린다: 근거가 확정 기획서인 케이스의 실패."""
    return [r for r in results
            if r["status"] == "FAIL" and KIND_META[_kind_of(r)][1]]


def _grouped_dev_items(results):
    """개발 전달 항목을 `area` 묶음 순서대로 편다.

    로그와 상세가 같은 번호를 쓰게 하려고 순서를 한 곳에서 정한다. 번호만 맞으면
    "3번 건" 이라고만 해도 서로 같은 걸 가리키므로 로그에 케이스 id 를 안 실어도 된다.
    """
    groups, order = {}, []
    for r in _dev_items(results):
        a = (r.get("area") or "").strip()
        if a not in groups:
            groups[a] = []
            order.append(a)
        groups[a].append(r)
    for a in order:
        groups[a].sort(key=lambda r: SEVERITY_RANK.get((r.get("severity") or "").strip(), 9))
    return [(a, groups[a]) for a in order]


def _flat_dev_items(results):
    """(번호, 영역, 결과) 로 편 목록. 로그·상세·HTML 이 모두 이 번호를 쓴다."""
    out, n = [], 0
    for area, rows in _grouped_dev_items(results):
        for r in rows:
            n += 1
            out.append((n, area, r))
    return out


def _held_items(results):
    """실패했지만 기획팀이 먼저 판단해야 하는 항목."""
    return [r for r in results
            if r["status"] == "FAIL" and not KIND_META[_kind_of(r)][1]
            and _kind_of(r) != "qa"]


# 재현 절차에 적을 조작. `expect_*` 는 검증문이라 빼고, `wait`·`screenshot` 은
# 러너 사정이지 사람이 하는 동작이 아니다. 그대로 두면 "expect_hidden #urgent" 같은
# 줄이 절차로 남아 받는 쪽이 무슨 뜻인지 알 수 없다.
REPRO_VERBS = {
    "goto":     "{} 접속",
    "click":    "{} 클릭",
    "dblclick": "{} 더블클릭",
    "hover":    "{} 위에 마우스 올림",
    "fill":     "{} 에 값 입력",
    "type":     "{} 에 값 입력",
    "select":   "{} 에서 항목 선택",
    "check":    "{} 체크",
    "uncheck":  "{} 체크 해제",
    "upload":   "{} 에 파일 업로드",
    "press":    "{} 키 입력",
    "download": "{} 눌러 파일 받기",
}


def _repro_steps(r):
    """재현 절차. 실패한 스텝까지만 자르고, 조작이 아닌 스텝은 뺀다.

    받는 사람이 그대로 따라 해볼 수 있어야 의미가 있다. 무엇이 어긋났는지는
    기대/실제 줄이 말해주므로, 여기에는 거기까지 가는 길만 남긴다.
    """
    out = []
    test = [st for st in r["steps"] if st["phase"] != "cleanup"]
    # soft 스텝이 있으면 실패가 여러 개다. 마지막 실패까지 절차를 남긴다
    last = max((i for i, st in enumerate(test) if st["status"] == "FAIL"), default=len(test) - 1)
    for st in test[:last + 1]:
        act = st.get("action")
        if act in REPRO_VERBS:
            target = st["label"][len(act):].strip() if act else st["label"]
            out.append({"text": REPRO_VERBS[act].format(target),
                        "failed": st["status"] == "FAIL"})
    return out


def _actual(r):
    """실제로 어떻게 나왔는지. 실패 스텝의 메시지가 가장 구체적이다 (soft 면 여러 개)."""
    msgs = [s["msg"] for s in r["steps"]
            if s["status"] == "FAIL" and s["msg"] and s["phase"] != "cleanup"]
    if msgs:
        return " / ".join(msgs)
    return r["reason"] or "(메시지 없음)"


def _trace_note(r):
    return "`{}` · https://trace.playwright.dev 에 올리면 동작이 재생됨".format(r["trace"])


# ---------------------------------------------------------------- 마크다운

def build_works_log(spec, results, out_dir):
    """웍스에 그대로 붙일 미반영 오류 로그.

    상세본(개발전달.md)은 근거·재현 절차까지 담아 "참고 자료"처럼 읽힌다. 그대로 올리면
    받는 쪽이 추가 검토 요청으로 받아들인다. 로그는 **할 일 목록**이어야 하므로
    한 줄짜리 지시문만 번호로 세워둔다. 설명·부탁·근거는 상세본에 남기고 여기서 뺀다.
    """
    meta = spec["meta"]
    items = _dev_items(results)

    # "수정 요청" 으로 쓰면 신규 요청으로 읽혀 결함으로 처리되지 않는다. 기획대로 안 된 것은 오류로 쓴다
    L = ["[{}] {} 검수 · 기획 미반영 오류 {}건".format(
        datetime.now().strftime("%m/%d"), meta["project"], len(items))]
    if not items:
        L.append("")
        L.append("미반영 오류 없음.")
        path = out_dir / "웍스등록.txt"
        path.write_text("\n".join(L), encoding="utf-8")
        return path

    last = object()
    for n, area, r in _flat_dev_items(results):
        if area != last:
            L.append("")
            if area:
                L.append("- {}".format(area))
            last = area
        L.append("{}. {}".format(n, _fix_line(r)))

    path = out_dir / "웍스등록.txt"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _fix_line(r):
    """로그 한 줄. 스펙에 적어둔 `fix` 가 있으면 그대로 쓴다.

    없으면 제목으로 대신하되, 제목은 보통 "~되지 않는다" 처럼 기대를 서술한 문장이라
    지시문으로는 약하다. 스펙 단계에서 `fix` 를 적어두는 게 맞다.
    치명 건은 앞에 [치명] 을 붙여 목록에서 먼저 눈에 띄게 한다.
    """
    line = (r.get("fix") or "").strip() or r["title"]
    return "[치명] " + line if (r.get("severity") or "").strip() == "치명" else line


def build_dev_md(spec, results, out_dir):
    """개발팀에 그대로 붙여넣을 수 있는 마크다운.

    HTML 리포트는 스크린샷이 붙어 있어 폴더째 넘겨야 하는데, 실무에서는 메일이나
    이슈 트래커에 텍스트로 붙이는 일이 더 잦다. 그래서 같은 내용을 파일로도 남긴다.
    """
    meta = spec["meta"]
    items = _dev_items(results)
    held = _held_items(results)

    L = ["# {} 기획 미반영 오류".format(meta["project"]), ""]
    L.append("- 대상: {}".format(meta["base_url"]))
    L.append("- 검수일: {}".format(datetime.now().strftime("%Y-%m-%d")))
    L.append("- 전체 {}건 중 **미반영 오류 {}건**".format(len(results), len(items)))
    L.append("")

    if not items:
        L.append("---")
        L.append("")
        L.append("확정 기획서 기준으로 어긋난 항목은 없습니다.")
        L.append("")
    for i, area, r in _flat_dev_items(results):
        L.append("---")
        L.append("")
        L.append("## {}. {}".format(i, _fix_line(r)))
        L.append("")
        L.append("| | |")
        L.append("|---|---|")
        if area:
            L.append("| 영역 | {} |".format(area))
        L.append("| 케이스 | `{}` |".format(r["id"]))
        if r.get("severity"):
            L.append("| 심각도 | {} |".format(r["severity"]))
        if r.get("session"):
            L.append("| 로그인 상태 | {} |".format(r["session"]))
        if r.get("trend") == "still":
            L.append("| 지난 검수 | 계속 실패 |")
        L.append("| 기대 | {} |".format(r["requirement"].replace("\n", " ") or "-"))
        L.append("| 실제 | {} |".format(_actual(r).replace("\n", " ").replace("|", "\\|")))
        L.append("| 근거 | {} |".format(r["source"] or "-"))
        if r.get("trace"):
            L.append("| 동작 기록 | {} |".format(_trace_note(r)))
        L.append("")
        # 접속만 하면 보이는 건이면 절차를 적을 게 없다. 위의 `대상` 줄과 겹칠 뿐이다.
        steps = _repro_steps(r)
        if len(steps) > 1:
            L.append("재현 절차")
            L.append("")
            for n, st in enumerate(steps, 1):
                mark = "  ← 여기서 멈춤" if st["failed"] else ""
                L.append("{}. {}{}".format(n, st["text"], mark))
            L.append("")
        if r["shots"]:
            L.append("스크린샷: {}".format(
                ", ".join("`screenshots/{}`".format(n) for n in r["shots"])))
            L.append("")

    if held:
        L.append("---")
        L.append("")
        L.append("## 참고 · 이번 전달에서 뺀 항목 {}건".format(len(held)))
        L.append("")
        L.append("기획 확정이 먼저 필요해 기획팀에서 정리합니다. 대응하지 않으셔도 됩니다.")
        L.append("")
        for r in held:
            L.append("- `{}` {} ({})".format(
                r["id"], r["title"], KIND_META[_kind_of(r)][0]))
        L.append("")

    path = out_dir / "개발전달.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


# ---------------------------------------------------------------- HTML 조각

def _gate_block(r, spec_case, spec_path):
    """승인 대기·차단 케이스에 붙는 안내.

    "spec.json 에서 approved 를 바꾸세요" 처럼 적으면 그 파일이 어디 있는지 알 수가 없다.
    실제 경로와 케이스 id 를 그대로 찍어서 바로 찾아갈 수 있게 한다.
    """
    if r["status"] == "SKIPPED":
        cleanup = (spec_case or {}).get("cleanup") or []
        steps = "".join("<li>{}</li>".format(
            _esc("{} {}".format(s.get("action"), s.get("selector", s.get("url", "")))))
            for s in cleanup)
        return (
            '<div class="gate">'
            '<b>이 케이스는 운영 데이터를 씁니다.</b> 승인해야 실행됩니다.'
            '<div class="gate-why">되돌리는 절차<ol class="gate-cleanup">{steps}</ol></div>'
            '<div class="gate-how">승인하려면 아래 파일에서 <code>"id": "{cid}"</code> 를 찾아 '
            '<code>"approved": true</code> 로 바꾸고 다시 실행합니다.'
            '<div class="gate-path">{path}</div>'
            'Claude 를 쓰는 중이면 <b>"{cid} 승인해줘"</b> 라고 해도 됩니다.</div></div>'
        ).format(cid=_esc(r["id"]), steps=steps or "<li>(없음)</li>",
                 path=_esc(spec_path or "specs/*.json"))

    if r["status"] == "BLOCKED":
        if "blocklist" in r["reason"]:
            why = ('이 버튼은 <b>의도적으로 막아둔 것</b>입니다. 승인 여부와 무관하게 차단됩니다. '
                   '정말 필요하면 스펙의 <code>blocklist</code> 에서 직접 빼야 합니다.')
        else:
            why = ('쓰기 케이스인데 <b>정리(cleanup) 절차가 없습니다.</b> 만든 데이터를 되돌릴 '
                   '방법을 스펙에 넣어야 실행됩니다.')
        return '<div class="gate gate-blocked">{}<div class="gate-path">{}</div></div>'.format(
            why, _esc(spec_path or "specs/*.json"))
    return ""


def _steps_html(r):
    """스텝 목록.

    `goto`·`wait` 는 모든 케이스에 똑같이 들어가는 배경 소음이라, 펼쳐두면 정작 봐야 할
    실패가 통과 스텝 사이에 묻힌다. 실패한 케이스만 펼친 채로 둔다.
    """
    if not r["steps"]:
        return ""
    lis = []
    for s in r["steps"]:
        icon = {"OK": "O", "FAIL": "X", "SKIP": "-"}[s["status"]]
        cls = "step-fail" if s["status"] == "FAIL" else (
            "step-skip" if s["status"] == "SKIP" else "")
        wtag = '<span class="tag tag-w">쓰기</span>' if s["write"] else ""
        phase = '<span class="tag">정리</span>' if s["phase"] == "cleanup" else ""
        msg = '<div class="stepmsg">{}</div>'.format(_esc(s["msg"])) if s["msg"] else ""
        lis.append('<li class="{}"><code>{}</code> {} {}{}{}</li>'.format(
            cls, icon, _esc(s["label"]), phase, wtag, msg))

    total = len(r["steps"])
    failed_at = next((s["n"] for s in r["steps"] if s["status"] == "FAIL"), None)
    if failed_at:
        summary = "스텝 {}개 · {}번째에서 어긋남".format(total, failed_at)
    elif any(s["status"] == "SKIP" for s in r["steps"]):
        n = sum(1 for s in r["steps"] if s["status"] == "SKIP")
        summary = "스텝 {}개 · {}개 건너뜀".format(total, n)
    else:
        summary = "스텝 {}개 · 전부 통과".format(total)
    return ('<details class="stepbox"{o}><summary>{s}</summary>'
            '<ol class="steps">{b}</ol></details>').format(
        o=" open" if failed_at else "", s=_esc(summary), b="".join(lis))


def _shots_html(r):
    if not r["shots"]:
        return ""
    figs = "".join(
        '<figure><img src="screenshots/{0}" alt="{0}" loading="lazy" '
        'onclick="zoom(this)"><figcaption>{0}</figcaption></figure>'.format(_esc(n))
        for n in r["shots"])
    return '<div class="shots">{}</div>'.format(figs)


def _case_body(r, spec_case, spec_path):
    # 사유가 실패 스텝 메시지와 같은 문장이면 바로 아래에서 또 보인다. 한 번만 싣는다.
    txt = r["reason"]
    if txt and any(st["status"] == "FAIL" and st["msg"] and st["msg"] in txt
                   for st in r["steps"]):
        extra = txt.split("]")[0].split("[")[-1] if "[" in txt else ""
        txt = "[{}]".format(extra) if extra else ""
    reason = ('<dt>사유</dt><dd class="reason">{}</dd>'.format(_esc(txt))
              if txt else "")
    if r.get("flaky"):
        reason += '<dt>1차 실행</dt><dd class="reason">{}</dd>'.format(_esc(r["flaky"]))
    if r.get("req"):
        reason += '<dt>요건 ID</dt><dd class="src">{}</dd>'.format(_esc(", ".join(r["req"])))
    if r.get("trace"):
        reason += ('<dt>동작 기록</dt><dd class="src"><a href="{0}">{0}</a> · '
                   'https://trace.playwright.dev 에 올리면 재생</dd>').format(_esc(r["trace"]))
    return """<dl>
    <dt>검증 요건</dt><dd>{req}</dd>
    <dt>근거</dt><dd class="src">{src}</dd>
    {reason}
  </dl>
  {gate}{steps}{signals}{shots}""".format(
        req=_esc(r["requirement"]), src=_esc(r["source"]), reason=reason,
        gate=_gate_block(r, spec_case, spec_path),
        steps=_steps_html(r), signals=_signals_html(r), shots=_shots_html(r))


def _signals_html(r):
    """콘솔 · 네트워크 오류. 판정과 무관한 참고 정보라 접어둔다."""
    sig = r.get("signals") or []
    if not sig:
        return ""
    return ('<details class="sig"><summary>콘솔·네트워크 오류 {}건 (판정과 무관)</summary>'
            '<ul>{}</ul></details>').format(
        len(sig), "".join("<li>{}</li>".format(_esc(s)) for s in sig))


def _case_block(r, spec_case, spec_path):
    """통과는 한 줄로 접고, 나머지는 펼친다.

    37건짜리 리포트에서 통과 28건이 실패 9건과 같은 자리를 차지하면 스크롤만 길어진다.
    통과도 근거로 남겨야 하니 지우지는 않고, 눌러서 펼치도록 접어둔다.
    """
    label, color, bg = STATUS_META[r["status"]]
    kind = _kind_of(r)
    ktag = ""
    if kind != DEFAULT_KIND:
        ktag = '<span class="ktag">{}</span>'.format(_esc(KIND_META[kind][0]))
    # 스펙 하나에 세션이 여럿이면(비회원 · 회원 · 어드민) 어느 로그인 상태로 본 건지 표시한다
    if r.get("session"):
        ktag = '<span class="ktag">{}</span>'.format(_esc(r["session"])) + ktag
    if r.get("severity"):
        ktag += '<span class="ktag">{}</span>'.format(_esc(r["severity"]))
    if r.get("flaky"):
        ktag += '<span class="ktag ktag-warn">재실행 통과</span>'
    elif r.get("retried"):
        ktag += '<span class="ktag">재실행도 같음</span>'
    if r.get("trend"):
        ktag += '<span class="ktag trend-{}">{}</span>'.format(
            r["trend"], _esc(TREND_META[r["trend"]]))

    head = ('<span class="badge" style="color:{c};background:{bg}">{l}</span>'
            '<span class="cid">{id}</span><span class="ctitle">{t}</span>'
            '{k}<span class="dur">{d}s</span>').format(
        c=color, bg=bg, l=label, id=_esc(r["id"]), t=_esc(r["title"]),
        k=ktag, d=r["duration"])

    attrs = 'class="case case-{s}" data-status="{s}" data-kind="{k}"'.format(
        s=r["status"], k=kind)
    body = _case_body(r, spec_case, spec_path)

    if r["status"] == "PASS":
        return ('<details {a}><summary class="chead">{h}</summary>'
                '<div class="cbody">{b}</div></details>').format(a=attrs, h=head, b=body)
    return ('<section {a}><div class="chead">{h}</div>'
            '<div class="cbody">{b}</div></section>').format(a=attrs, h=head, b=body)


def _held_note(held):
    lis = "".join('<li><span class="ktag">{}</span> {} · {}</li>'.format(
        _esc(KIND_META[_kind_of(r)][0]), _esc(r["id"]), _esc(r["title"]))
        for r in held)
    return ('<div class="heldnote"><b>전달에서 뺀 항목 {n}건</b>'
            '<p>기획 확정이 먼저 필요한 것들입니다. 개발팀에 넘기지 않습니다.</p>'
            '<ul>{lis}</ul></div>').format(n=len(held), lis=lis)


def _dev_block(results, held, md_name, log_name, log_text):
    rows_in = _flat_dev_items(results)
    if not rows_in:
        # 넘길 게 없는데 복사 버튼과 "이대로 전달하면 됩니다" 를 남겨두면 읽는 사람이 헷갈린다
        head = ('<div class="devhead"><div><b>전달할 내용 없음</b>'
                '<p>확정 기획서 기준으로 어긋난 항목이 없습니다.</p></div></div>')
        return head + (_held_note(held) if held else "")
    rows = []
    for i, area, r in rows_in:
        rp = _repro_steps(r)
        steps = "".join(
            '<li class="{}">{}</li>'.format(
                "step-fail" if st["failed"] else "", _esc(st["text"]))
            for st in rp) if len(rp) > 1 else ""
        extra = ""
        if r.get("severity"):
            extra += '<dt>심각도</dt><dd>{}</dd>'.format(_esc(r["severity"]))
        if r.get("trend") == "still":
            extra += '<dt>지난 검수</dt><dd>계속 실패</dd>'
        if r.get("trace"):
            extra += ('<dt>동작 기록</dt><dd class="src"><a href="{0}">{0}</a> · '
                      'https://trace.playwright.dev 에 올리면 재생</dd>').format(_esc(r["trace"]))
        rows.append("""<article class="devitem">
  <h3><span class="devno">{n}</span>{title}</h3>
  <dl>
    {area}<dt>기대</dt><dd>{req}</dd>
    <dt>실제</dt><dd class="reason">{act}</dd>
    <dt>근거</dt><dd class="src">{src}</dd>
    <dt>케이스</dt><dd class="src">{id}</dd>
    {extra}
  </dl>
  {repro}
  {shots}
</article>""".format(
            n=i, title=_esc(_fix_line(r)), req=_esc(r["requirement"]),
            area=('<dt>영역</dt><dd>{}</dd>'.format(_esc(area)) if area else ""),
            act=_esc(_actual(r)), src=_esc(r["source"]), id=_esc(r["id"]), extra=extra,
            repro=('<div class="devrepro"><b>재현 절차</b><ol>{}</ol></div>'.format(steps)
                   if steps else ""),
            shots=_shots_html(r)))

    inner = "".join(rows)
    heldnote = _held_note(held) if held else ""

    return """<div class="devhead">
  <div>
    <b>기획 미반영 오류 {n}건</b>
    <p>아래 그대로 웍스에 올리면 됩니다. 근거·재현 절차는 담지 않았습니다.</p>
  </div>
  <button type="button" id="copybtn" onclick="copyDev()">복사</button>
</div>
<pre id="devmd" class="logbox">{log}</pre>
<div class="devfile">
  <code>{logf}</code> 웍스 등록용 · <code>{md}</code> 근거·재현 절차 포함한 상세본
</div>
{heldnote}
<h2 class="devsub">항목별 근거</h2>
{inner}""".format(n=len(rows_in), md=_esc(md_name), logf=_esc(log_name),
                  log=_esc(log_text), heldnote=heldnote, inner=inner)


# ---------------------------------------------------------------- 요약 블록

COV_ORDER = ("미검증", "미반영", "점검 필요", "미확인", "일부 통과", "미실행", "통과", "제외")
COV_BAD = ("미검증", "미반영", "점검 필요")


def coverage_rows(spec, results):
    """요건마다 연결된 케이스와 상태. 케이스 -> 근거 말고 요건 -> 케이스 방향으로 본다.

    케이스마다 근거를 붙여도, 기획서 요건 중 케이스가 아예 안 만들어진 것은 리포트 어디에도
    드러나지 않는다. 그 빈칸을 보이게 하는 게 이 표의 목적이다.
    """
    reqs = spec.get("requirements") or []
    if not reqs:
        return []
    linked = {}
    for c in spec.get("cases", []):
        for rid in c.get("req") or []:
            linked.setdefault(rid, []).append(c["id"])
    by_id = {r["id"]: r for r in results}
    rows = []
    for q in reqs:
        cids = linked.get(q["id"], [])
        note = ""
        if q.get("exclude"):
            st, note = "제외", q["exclude"]
        elif not cids:
            st, note = "미검증", "연결된 케이스 없음"
        else:
            ss = {by_id[c]["status"] for c in cids if c in by_id}
            if not ss:
                st = "미실행"
            elif "FAIL" in ss:
                st = "미반영"
            elif "ERROR" in ss:
                st = "점검 필요"
            elif ss == {"PASS"}:
                st = "통과"
            elif "PASS" in ss:
                st, note = "일부 통과", "나머지 케이스는 승인 대기·조건 미충족·차단"
            else:
                st, note = "미확인", "승인 대기·조건 미충족·차단"
        rows.append({"id": q["id"], "text": q.get("text", ""), "source": q.get("source", ""),
                     "cases": cids, "status": st, "note": note})
    return rows


def _coverage_html(rows, partial):
    if not rows:
        return ""
    cnt = {k: sum(1 for x in rows if x["status"] == k) for k in COV_ORDER}
    summ = " · ".join("{} {}".format(k, cnt[k]) for k in COV_ORDER if cnt[k])
    trs = "".join(
        '<tr class="{cls}"><td class="cid">{id}</td><td>{text}<div class="src">{src}</div></td>'
        '<td class="cid">{cases}</td><td><b>{st}</b><div class="src">{note}</div></td></tr>'.format(
            cls="cov-bad" if x["status"] in COV_BAD else "", id=_esc(x["id"]), text=_esc(x["text"]),
            src=_esc(x["source"]), cases=_esc(", ".join(x["cases"]) or "-"),
            st=_esc(x["status"]), note=_esc(x["note"]))
        for x in sorted(rows, key=lambda x: COV_ORDER.index(x["status"])))
    opened = " open" if any(x["status"] in COV_BAD for x in rows) else ""
    part = " · 일부 케이스만 실행함" if partial else ""
    return ('<details class="cov"{o}><summary>요건 커버리지 · 요건 {n}개 · {s}{p}</summary>'
            '<table><thead><tr><th>ID</th><th>요건</th><th>케이스</th><th>상태</th></tr></thead>'
            '<tbody>{trs}</tbody></table></details>').format(
        o=opened, n=len(rows), s=_esc(summ), p=part, trs=trs)


def _trend_html(results, prev):
    if not prev:
        return ""
    groups = {k: [r for r in results if r.get("trend") == k] for k in TREND_META}
    summ = " · ".join("{} {}".format(TREND_META[k], len(v)) for k, v in groups.items())
    body = "".join(
        "<p><b>{}</b></p><ul>{}</ul>".format(
            TREND_META[k], "".join("<li>{} {}</li>".format(_esc(r["id"]), _esc(r["title"])) for r in v))
        for k, v in groups.items() if v)
    return ('<details class="pending trend"><summary>지난 실행 대비 · {s}</summary>'
            '<p class="src">비교 기준: {n}</p>{b}</details>').format(
        s=_esc(summ), n=_esc(prev["name"]), b=body)


def _list_block(cls, title, desc, items):
    if not items:
        return ""
    return ('<details class="pending {c}"><summary>{t}</summary><p>{d}</p>'
            '<ul>{li}</ul></details>').format(
        c=cls, t=_esc(title), d=desc, li="".join(items))


# ---------------------------------------------------------------- CSS / JS

CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:28px 24px 64px; background:#f4f5f7; color:#16191d;
       font:14px/1.6 'Malgun Gothic','맑은 고딕',system-ui,sans-serif; }
.wrap { max-width:1000px; margin:0 auto; }

/* 머리말 */
header.top { margin-bottom:18px; }
header.top h1 { font-size:24px; margin:0 0 2px; letter-spacing:-.3px; }
header.top .kicker { font-size:12px; color:#8b919a; font-weight:700;
                     letter-spacing:.06em; margin-bottom:6px; }
header.top .meta { color:#6b7280; font-size:12.5px; }
header.top .meta code { background:#e9ebef; padding:1px 6px; border-radius:3px;
                        font-size:12px; }
.drytag { display:inline-block; background:#fff2d6; color:#8a6100;
          padding:1px 8px; border-radius:3px; font-size:11.5px; font-weight:700; }

/* 탭 */
.tabs { display:flex; gap:2px; border-bottom:1px solid #dcdfe4; margin-bottom:18px; }
.tabs button { background:none; border:0; border-bottom:2px solid transparent;
               padding:9px 16px; font:600 14px/1.4 inherit; color:#6b7280;
               cursor:pointer; margin-bottom:-1px; }
.tabs button:hover { color:#16191d; }
.tabs button.on { color:#16191d; border-bottom-color:#16191d; }
.tabs .cnt { display:inline-block; background:#e9ebef; color:#4b5563;
             border-radius:9px; padding:0 7px; font-size:11.5px; margin-left:6px; }
.tabs button.on .cnt { background:#16191d; color:#fff; }

/* 요약 칩 */
.chips { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px; }
.chip { display:flex; align-items:baseline; gap:6px; background:#fff; cursor:pointer;
        border:1px solid #dcdfe4; border-radius:999px; padding:5px 14px 5px 12px;
        font:inherit; font-size:12.5px; color:#4b5563; transition:.12s; }
.chip b { font-size:15px; font-weight:700; color:#16191d; }
.chip:hover:not(:disabled) { border-color:#9aa1aa; }
.chip.on { background:#16191d; border-color:#16191d; color:#fff; }
.chip.on b { color:#fff; }
.chip:disabled { opacity:.4; cursor:default; }
.chip-FAIL { border-color:#f0b5ae; background:#fff7f6; }
.chip-FAIL b { color:#c0362c; font-size:17px; }
.chip-FAIL.on b { color:#fff; }
.total { margin-left:auto; font-size:12.5px; color:#8b919a; }
.fltbar { font-size:12px; color:#6b7280; margin-bottom:16px; min-height:19px; }
.fltbar button { background:none; border:0; color:#3b6fd4; cursor:pointer;
                 font:inherit; text-decoration:underline; padding:0 0 0 6px; }

/* 알림 */
.warn, .pending { border-radius:6px; margin-bottom:14px; font-size:12.5px;
                  background:#fff; border:1px solid #dcdfe4; }
.warn > summary, .pending > summary { cursor:pointer; padding:10px 14px; font-weight:700; }
.warn { border-left:3px solid #d99a00; }
.pending { border-left:3px solid #3b6fd4; }
.warn ul, .pending ul { margin:0 0 12px; padding:0 14px 0 34px; }
.pending p { margin:0 14px 8px; color:#4b5563; }

/* 케이스 */
.case { background:#fff; border:1px solid #e3e6ea; border-left:3px solid #d5d9df;
        border-radius:6px; margin-bottom:10px; display:block; }
.case[hidden] { display:none; }
.case-FAIL { border-left-color:#c0362c; box-shadow:0 1px 3px rgba(192,54,44,.10); }
.case-SKIPPED { border-left-color:#d99a00; }
.case-BLOCKED { border-left-color:#5b4bbd; }
.case-ERROR { border-left-color:#ea7a2c; }
.case-NA { border-left-color:#9aa1aa; }
.chip-ERROR { border-color:#f5c9a6; background:#fff8f2; }
.chip-ERROR b { color:#9a3412; }
.chip-ERROR.on b { color:#fff; }
.ktag-warn { background:#fff1e6; color:#9a3412; }
.trend-fixed { background:#e7f6ed; color:#0f7b3f; }
.trend-still { background:#fdecea; color:#c0362c; }
.trend-new { background:#fdecea; color:#c0362c; font-weight:700; }
.pending.err { border-left-color:#ea7a2c; }
.pending.flaky { border-left-color:#d99a00; }
.pending.sig, .pending.trend { border-left-color:#9aa1aa; }
.pending > p.src { margin:0 14px 8px; }
.sig { margin:8px 0 0; font-size:12px; color:#6b7280; }
.sig > summary { cursor:pointer; }
.sig ul { margin:4px 0 0; padding-left:20px; word-break:break-all; }

/* 요건 커버리지 */
.cov { background:#fff; border:1px solid #dcdfe4; border-left:3px solid #16191d;
       border-radius:6px; margin-bottom:14px; font-size:12.5px; }
.cov > summary { cursor:pointer; padding:10px 14px; font-weight:700; }
.cov table { width:calc(100% - 28px); margin:0 14px 12px; border-collapse:collapse; }
.cov th { text-align:left; color:#8b919a; font-weight:400; border-bottom:1px solid #e3e6ea;
          padding:4px 8px; }
.cov td { border-bottom:1px solid #f0f1f3; padding:6px 8px; vertical-align:top; }
.cov tr.cov-bad td { background:#fff7f6; }
.cov tr.cov-bad b { color:#c0362c; }
.case-PASS { background:#fcfcfd; }
.chead { display:flex; align-items:center; gap:10px; padding:11px 16px;
         list-style:none; }
details.case > summary.chead { cursor:pointer; }
details.case > summary.chead::-webkit-details-marker { display:none; }
details.case[open] > summary.chead { border-bottom:1px solid #eef0f3; }
.badge { padding:2px 9px; border-radius:3px; font-size:11.5px; font-weight:700;
         white-space:nowrap; }
.cid { font:700 12px Consolas,monospace; color:#8b919a; }
.ctitle { flex:1; font-size:14px; font-weight:600; }
.case-PASS .ctitle { font-weight:400; color:#4b5563; }
.ktag { background:#eef1f5; color:#4b5563; font-size:11px; padding:1px 7px;
        border-radius:3px; white-space:nowrap; }
.dur { color:#b0b6be; font-size:11.5px; }
.cbody { padding:14px 16px 16px; }

dl { display:grid; grid-template-columns:max-content 1fr; gap:3px 14px;
     margin:0 0 12px; font-size:13px; }
dt { color:#8b919a; font-size:12.5px; }
dd { margin:0; }
.src { color:#8b919a; font-size:12px; }
.reason { color:#c0362c; }

/* 승인 안내 */
.gate { background:#fff8e8; border:1px solid #f0d488; border-radius:5px;
        padding:11px 13px; margin:0 0 12px; font-size:12.5px; }
.gate-why { color:#6b5200; font-size:12px; margin-top:6px; }
.gate-cleanup { margin:4px 0 0; padding-left:20px; color:#4b5563; }
.gate-how { font-size:12px; margin-top:8px; color:#6b5200; }
.gate-path { font-family:Consolas,monospace; font-size:11.5px; color:#16191d;
             background:#fff; border:1px solid #e3e6ea; border-radius:4px;
             padding:5px 8px; margin:5px 0; word-break:break-all; }
.gate-blocked { background:#f1eefb; border-color:#c9c0ee; color:#3f3577; }

/* 스텝 */
.stepbox { margin:0; }
.stepbox > summary { cursor:pointer; font-size:12px; color:#8b919a; padding:3px 0; }
.stepbox > summary:hover { color:#16191d; }
.stepbox[open] > summary { margin-bottom:5px; }
.steps { margin:0; padding-left:22px; font-size:12.5px; color:#374151; }
.steps li { padding:2px 0; }
.steps code { font-weight:700; margin-right:4px; }
.step-fail { color:#c0362c; font-weight:600; }
.step-skip { color:#b0b6be; }
.stepmsg { background:#fdecea; color:#c0362c; padding:6px 10px; border-radius:4px;
           margin:4px 0; font-size:12px; font-weight:400; }
.tag { background:#eef1f5; color:#4b5563; font-size:10px; padding:1px 5px;
       border-radius:3px; margin-left:4px; }
.tag-w { background:#fdecea; color:#c0362c; }

/* 스크린샷 - 잘라내면 정작 봐야 할 부분이 사라지므로 비율을 유지한다 */
.shots { display:flex; flex-wrap:wrap; gap:12px; margin-top:12px; }
figure { margin:0; max-width:320px; }
figure img { display:block; max-width:100%; max-height:220px; object-fit:contain;
             border:1px solid #e3e6ea; border-radius:4px; cursor:zoom-in;
             background:#fff; transition:border-color .12s; }
figure img:hover { border-color:#3b6fd4; }
figcaption { font-size:10.5px; color:#b0b6be; margin-top:3px; word-break:break-all; }

/* 개발 전달용 */
#view-dev { display:none; }
.devhead { display:flex; align-items:flex-start; gap:16px; background:#fff;
           border:1px solid #dcdfe4; border-radius:6px; padding:14px 16px;
           margin-bottom:8px; }
.devhead > div { flex:1; }
.devhead b { font-size:15px; }
.devhead p { margin:3px 0 0; color:#6b7280; font-size:12.5px; }
.devhead button { background:#16191d; color:#fff; border:0; border-radius:5px;
                  padding:8px 16px; font:600 13px/1.4 inherit; cursor:pointer;
                  white-space:nowrap; }
.devhead button:hover { background:#31363d; }
/* 웍스에 붙었을 때와 같게 보이도록 본문 글꼴 그대로, 줄바꿈만 살린다 */
.logbox { background:#fff; border:1px solid #dcdfe4; border-radius:6px;
          padding:16px 20px; margin:0 0 8px; white-space:pre-wrap;
          font-family:inherit; font-size:13.5px; line-height:1.9;
          color:#16191d; }
.devfile { font-size:12px; color:#8b919a; margin-bottom:22px; }
.devsub { font-size:13px; color:#8b919a; font-weight:700; margin:0 0 10px;
          border-top:1px solid #e3e6ea; padding-top:16px; }
.devfile code { background:#e9ebef; padding:1px 6px; border-radius:3px; }
.devnone { background:#fff; border:1px solid #dcdfe4; border-radius:6px;
           padding:28px; text-align:center; color:#6b7280; line-height:1.9; }
.heldnote { background:#fff; border:1px solid #dcdfe4; border-left:3px solid #9aa1aa;
            border-radius:6px; padding:12px 16px; margin-bottom:16px; font-size:12.5px; }
.heldnote p { margin:3px 0 8px; color:#6b7280; }
.heldnote ul { margin:0; padding-left:18px; color:#4b5563; }
.heldnote li { padding:1px 0; }
.devitem { background:#fff; border:1px solid #e3e6ea; border-radius:6px;
           padding:18px 20px; margin-bottom:12px; }
.devitem h3 { font-size:15px; margin:0 0 12px; display:flex; gap:10px;
              align-items:baseline; }
.devno { display:inline-flex; align-items:center; justify-content:center;
         min-width:22px; height:22px; background:#16191d; color:#fff;
         border-radius:50%; font-size:12px; flex:none; }
.devrepro { font-size:12.5px; margin-top:10px; }
.devrepro b { color:#8b919a; font-size:12.5px; font-weight:400; }
.devrepro ol { margin:4px 0 0; padding-left:20px; color:#374151; }
.devrepro li { padding:1px 0; }

/* 확대 보기 */
#lb { position:fixed; inset:0; background:rgba(15,18,21,.93); z-index:999;
      display:none; overflow:auto; }
#lb.on { display:block; }
#lb .bar { position:sticky; top:0; display:flex; align-items:center; gap:14px;
           padding:10px 16px; background:rgba(15,18,21,.97); color:#e5e7eb;
           font-size:12px; }
#lb .bar b { font-weight:600; flex:1; word-break:break-all; }
#lb button { background:#374151; color:#e5e7eb; border:0; border-radius:4px;
             padding:6px 12px; font-size:12px; cursor:pointer; font-family:inherit; }
#lb button:hover { background:#4b5563; }
#lb .stage { padding:16px; text-align:center; min-height:calc(100% - 37px); }
#lb img { max-width:100%; background:#fff; border-radius:4px; cursor:zoom-in; }
#lb.full img { max-width:none; cursor:zoom-out; }

footer { margin-top:28px; padding-top:14px; border-top:1px solid #dfe2e6;
         font-size:12px; color:#8b919a; }
footer ul { margin:4px 0 0; padding-left:20px; }
"""

JS = """
// 탭 전환 - 기획팀 전체 보기 / 개발 전달용
function tab(v) {
  // '' 로 되돌리면 CSS 의 display:none 이 다시 먹으므로 값을 그대로 지정한다
  document.getElementById('view-qa').style.display  = (v === 'qa')  ? 'block' : 'none';
  document.getElementById('view-dev').style.display = (v === 'dev') ? 'block' : 'none';
  document.querySelectorAll('.tabs button').forEach(function (b) {
    b.classList.toggle('on', b.dataset.view === v);
  });
}
document.querySelectorAll('.tabs button').forEach(function (b) {
  b.addEventListener('click', function () { tab(b.dataset.view); });
});

// 요약 칩 클릭 -> 해당 상태만 보기. 같은 칩을 다시 누르면 해제.
var cur = null;
function flt(k) {
  cur = (cur === k) ? null : k;
  document.querySelectorAll('.case').forEach(function (el) {
    el.hidden = cur !== null && el.dataset.status !== cur;
  });
  document.querySelectorAll('.chip').forEach(function (b) {
    b.classList.toggle('on', b.dataset.filter === cur);
  });
  var bar = document.getElementById('fltbar');
  if (cur === null) { bar.textContent = ''; return; }
  var n = document.querySelectorAll('.case:not([hidden])').length;
  var label = document.querySelector('.chip[data-filter="' + cur + '"] span').textContent;
  bar.innerHTML = label + ' ' + n + '건만 보는 중' +
    '<button type="button" id="fltclear">전체 보기</button>';
  document.getElementById('fltclear').onclick = function () { flt(cur); };
}
document.querySelectorAll('.chip').forEach(function (b) {
  b.addEventListener('click', function () { flt(b.dataset.filter); });
});

// 개발 전달용 텍스트 복사.
// file:// 에서는 navigator.clipboard 가 막히는 브라우저가 있어 예전 방식으로 한 번 더 시도한다.
function copyDev() {
  var txt = document.getElementById('devmd').textContent;
  var btn = document.getElementById('copybtn');
  function done(ok) {
    btn.textContent = ok ? '복사했습니다' : '복사 실패 - md 파일을 열어주세요';
    setTimeout(function () { btn.textContent = '텍스트 복사'; }, 2200);
  }
  function legacy() {
    var ta = document.createElement('textarea');
    ta.value = txt;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    done(ok);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(function () { done(true); }, legacy);
  } else {
    legacy();
  }
}

var lb = document.getElementById('lb');
function zoom(el) {
  document.getElementById('lbimg').src = el.src;
  document.getElementById('lbname').textContent = el.alt;
  lb.classList.remove('full');
  document.getElementById('lbfit').textContent = '원본 크기';
  lb.classList.add('on');
  lb.scrollTop = 0;
  document.body.style.overflow = 'hidden';
}
function closeLb(e) {
  if (e) e.stopPropagation();
  lb.classList.remove('on');
  document.body.style.overflow = '';
}
function toggleFull(e) {
  if (e) e.stopPropagation();
  lb.classList.toggle('full');
  document.getElementById('lbfit').textContent =
    lb.classList.contains('full') ? '화면에 맞춤' : '원본 크기';
}
// 이미지·버튼 밖(오버레이 여백)을 누르면 닫는다. stage 가 lb 를 덮고 있으므로 같이 본다.
lb.addEventListener('click', function (e) {
  if (e.target === lb || e.target.classList.contains('stage')) closeLb();
});
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeLb(); });
"""

DOC = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>QA 리포트 - {project}</title>
<style>{css}</style></head><body><div class="wrap">

<header class="top">
  <div class="kicker">QA 자동 검수 리포트</div>
  <h1>{project}</h1>
  <div class="meta">{stamp} &nbsp;&middot;&nbsp; <code>{base}</code> {dry}</div>
</header>

<nav class="tabs">
  <button data-view="qa" class="on">기획팀 검토<span class="cnt">{n_all}</span></button>
  <button data-view="dev">개발 전달용<span class="cnt">{n_dev}</span></button>
</nav>

<div id="view-qa">
  <div class="chips">{chips}<span class="total">전체 {n_all}건</span></div>
  <div class="fltbar" id="fltbar"></div>
  {warn}{pending}
  {cases}
</div>

<div id="view-dev">{dev}</div>

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
<script>{js}</script>
</body></html>"""


def build_report(spec, results, warnings, out_dir, dry_run, spec_path=None,
                 prev=None, partial=False):
    meta = spec["meta"]
    counts = {k: sum(1 for r in results if r["status"] == k) for k in STATUS_META}
    by_id = {c["id"]: c for c in spec.get("cases", [])}

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = build_dev_md(spec, results, out_dir)
    log_path = build_works_log(spec, results, out_dir)
    dev_items, held_items = _dev_items(results), _held_items(results)

    chips = "".join(
        '<button class="chip chip-{k}" data-filter="{k}"{dis}>'
        '<b>{n}</b><span>{l}</span></button>'.format(
            k=k, n=counts[k], l=STATUS_META[k][0], dis="" if counts[k] else " disabled")
        for k in CHIP_ORDER if counts[k] or k in ("FAIL", "PASS"))

    def li(r, extra=""):
        return "<li>{} {}{}</li>".format(_esc(r["id"]), _esc(r["title"]),
                                         " · " + _esc(extra) if extra else "")

    notices = "".join([
        _trend_html(results, prev),
        _list_block("err", "점검 필요 {}건".format(counts["ERROR"]),
                    "화면 판정까지 가지 못했습니다 (요소 없음 · 시간 초과 · 설정 오류). "
                    "스크린샷과 동작 기록으로 결함인지 검수 스크립트 문제인지 확인합니다. "
                    "개발 전달에는 넣지 않았습니다.",
                    [li(r, r["reason"][:120]) for r in results if r["status"] == "ERROR"]),
        _list_block("flaky", "재실행으로 통과 {}건".format(sum(1 for r in results if r.get("flaky"))),
                    "1차 실행에서 실패했다가 다시 돌려 통과했습니다. 반복되면 화면이 불안정한 것입니다.",
                    [li(r, r["flaky"][:120]) for r in results if r.get("flaky")]),
        _list_block("sig", "콘솔·네트워크 오류가 난 케이스 {}건".format(
                        sum(1 for r in results if r.get("signals"))),
                    "판정과 무관한 참고 정보입니다.",
                    [li(r, r["signals"][0] + (" 외 {}건".format(len(r["signals"]) - 1)
                                              if len(r["signals"]) > 1 else ""))
                     for r in results if r.get("signals")]),
    ])
    cov_html = _coverage_html(coverage_rows(spec, results), partial)

    warn_html = ""
    if warnings:
        items = "".join("<li>{}</li>".format(_esc(w)) for w in warnings)
        warn_html = ('<details class="warn"><summary>경고 {}건</summary>'
                     '<ul>{}</ul></details>').format(len(warnings), items)

    pending = [r for r in results if r["status"] == "SKIPPED"]
    pend_html = ""
    if pending:
        items = "".join(
            "<li>{} {}</li>".format(_esc(r["id"]), _esc(r["title"])) for r in pending)
        pend_html = (
            '<details class="pending"><summary>승인 대기 {n}건</summary>'
            '<p>쓰기가 필요해 실행하지 않았습니다. 아래 파일에서 해당 케이스를 '
            '<code>"approved": true</code> 로 바꾸고 다시 실행하세요.</p>'
            '<div class="gate-path" style="margin:0 14px 8px">{path}</div>'
            '<ul>{items}</ul></details>'
        ).format(n=len(pending), path=_esc(spec_path or "specs/*.json"), items=items)

    doc = DOC.format(
        css=CSS, js=JS,
        project=_esc(meta["project"]),
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        base=_esc(meta["base_url"]),
        dry='<span class="drytag">DRY-RUN (쓰기 없음)</span>' if dry_run else "",
        n_all=len(results), n_dev=len(dev_items),
        chips=chips, warn=warn_html, pending=cov_html + notices + pend_html,
        cases="".join(_case_block(r, by_id.get(r["id"]), spec_path) for r in results),
        dev=_dev_block(results, held_items, md_path.name, log_path.name,
                       log_path.read_text(encoding="utf-8")),
        sources="".join("<li>{}</li>".format(_esc(s)) for s in meta.get("sources", [])),
    )

    path = out_dir / "리포트.html"
    path.write_text(doc, encoding="utf-8")

    # 원본 결과도 남긴다 (재분석 · 이력 비교용)
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    return path
