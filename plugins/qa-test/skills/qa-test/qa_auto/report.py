"""
QA 실행 결과 -> HTML 리포트 + 개발 전달용 마크다운 생성.
qa_run.py 에서 호출한다. 단독 실행하지 않음.

리포트를 두 갈래로 나눈다. 기획팀이 먼저 전체를 보고, 그중 개발팀이 손댈 것만
추려서 넘기는 흐름이기 때문이다. 기획서에 근거가 없는 개선 제안이나 기획 자체를
고쳐야 하는 항목까지 같이 넘기면 "기획도 안 끝났는데 왜 우리한테 주냐"가 되고,
정작 근거가 확실한 결함까지 같이 흐려진다.

색은 세 가지 뜻만 쓴다. 빨강 = 개발 전달 대상, 주황 = 기획팀이 조치할 것, 회색 = 참고.
"""

import html
import json
import os
import re
from datetime import datetime

from gates import (W_NO_FIX, W_NO_AREA, W_ABSENCE_NO_ANCHOR, W_PRE_NO_ANCHOR, W_UNKNOWN_REQ,
                   W_SOFT_POSITION, W_APPROVED_FIRST_GOTO, W_SELECTOR_UPDATE, W_RESIDUE,
                   BLOCK_BLOCKLIST, BLOCK_UNDECLARED, BLOCK_RESIDUE_WRITE)

# (라벨, 글자색, 바탕색, 아이콘)
RED = ("#b42318", "#fdecea")
ORANGE = ("#9a3412", "#fff1e6")
GRAY = ("#374151", "#eef1f5")
STATUS_META = {
    "FAIL":    ("실패",) + RED + ("✕",),
    # 화면 판정까지 못 간 것 (요소 없음 · 시간 초과 · 설정 오류). 결함인지 스크립트 문제인지 사람이 본다
    "ERROR":   ("점검 필요",) + ORANGE + ("!",),
    # 1차 실패, 재실행 통과. 통과로 덮지 않고 기획팀이 판단한다
    "FLAKY":   ("불안정",) + ORANGE + ("?",),
    "BLOCKED": ("차단",) + ORANGE + ("⊘",),
    "SKIPPED": ("승인 대기",) + ORANGE + ("–",),
    # precondition 이 안 맞음. 운영 데이터가 바뀌어 검수할 대상이 없는 것
    "NA":      ("조건 미충족",) + GRAY + ("~",),
    "PASS":    ("통과",) + GRAY + ("✓",),
}
# 칩 · 케이스 정렬 순서. 할 일이 위로 온다
CHIP_ORDER = ("FAIL", "ERROR", "FLAKY", "BLOCKED", "SKIPPED", "NA", "PASS")
STATUS_RANK = {k: i for i, k in enumerate(CHIP_ORDER)}

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

TREND_META = {"fixed": "수정됨", "still": "계속 실패", "new": "새로 실패",
              "unjudged": "판정 불가로 바뀜", "still_err": "계속 점검 필요"}
# 빨강은 개발 전달(실패)에만. 점검 필요 쪽 변화는 주황
TREND_TONE = {"still": " k-red", "new": " k-red", "unjudged": " k-warn", "still_err": " k-warn"}
OTHER_AREA = "기타"

# 실패 시점 주소에서 가릴 쿼리 이름 (세션 · 토큰류)
_SECRET_Q = re.compile(r"([?&](?:[^=&#]*(?:token|sid|session|auth|key|code|pass|pw|csrf)[^=&#]*)=)[^&#]*", re.I)


def _mask_url(u):
    return _SECRET_Q.sub(r"\1***", str(u or ""))


def _aborted(run_meta):
    return bool((run_meta or {}).get("aborted"))


def _held_text(run_meta, results):
    """중단된 실행의 개발 전달 보류 문구."""
    info = (run_meta or {}).get("aborted_info") or {}
    total = info.get("total") or len(results)
    done = sum(1 for r in results if r.get("id") != "QA-잔여")
    return "검수 중단 · 전달 보류 ({}건 중 {}건 실행)".format(total, min(done, total))


def _esc(s):
    return html.escape(str(s or ""))


def _kind_of(r):
    k = (r.get("kind") or DEFAULT_KIND).strip()
    return k if k in KIND_META else DEFAULT_KIND


def _dev_items(results):
    """개발팀에 넘길 항목만 추린다: 근거가 확정 기획서인 케이스의 실패."""
    return [r for r in results
            if r["status"] == "FAIL" and KIND_META[_kind_of(r)][1]]


def _area_groups(rows):
    """area 묶음 순서대로. area 가 없는 항목은 맨 뒤 '기타' 로 모은다.

    빈 줄 하나로만 떼어 두면 바로 위 영역에 속한 것처럼 읽힌다.
    모든 항목에 area 가 없으면 묶음 제목 자체를 쓰지 않는다.
    """
    groups, order = {}, []
    for r in rows:
        a = (r.get("area") or "").strip()
        if a not in groups:
            groups[a] = []
            order.append(a)
        groups[a].append(r)
    named = [a for a in order if a]
    out = [(a, groups[a]) for a in named]
    if "" in groups:
        out.append((OTHER_AREA if named else "", groups[""]))
    return out


def _grouped_dev_items(results):
    """개발 전달 항목을 묶음 순서대로 편다. 로그 · 상세 · HTML 이 같은 번호를 쓴다."""
    out = []
    for a, rows in _area_groups(_dev_items(results)):
        rows.sort(key=lambda r: SEVERITY_RANK.get((r.get("severity") or "").strip(), 9))
        out.append((a, rows))
    return out


def _flat_dev_items(results):
    """(번호, 영역, 결과) 로 편 목록."""
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
# 러너 사정이지 사람이 하는 동작이 아니다.
REPRO_VERBS = {
    "goto":     "{} 접속",
    "click":    "{} 클릭",
    "dblclick": "{} 더블클릭",
    "hover":    "{} 위에 마우스 올림",
    "fill":     "{} 입력",
    "type":     "{} 입력",
    "select":   "{} 선택",
    "check":    "{} 체크",
    "uncheck":  "{} 체크 해제",
    "upload":   "{} 파일 업로드",
    "press":    "{} 키 입력",
    "download": "{} 눌러 파일 받기",
}


def _repro_steps(r):
    """재현 절차. 실패한 스텝까지만 자르고, 조작이 아닌 스텝 · 정리 스텝은 뺀다.

    받는 사람이 그대로 따라 해볼 수 있어야 의미가 있다.
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
    text = " / ".join(msgs) if msgs else (r["reason"] or "(메시지 없음)")
    if r.get("diagnosis"):
        text += " ({})".format(r["diagnosis"])
    return text


def _actual_value(r):
    """fix 의 {actual} 자리에 넣을 실제 값. 실패 메시지의 '실제:' 부분만."""
    m = re.search(r"실제:\s*'?(.*?)'?(?:\s*/|$)", _actual(r))
    return m.group(1).strip() if m else _actual(r)


def _env_line(spec, r=None):
    meta = spec["meta"]
    vp = meta.get("viewport") or {"width": 1600, "height": 900}
    parts = []
    if r is not None and r.get("session"):
        parts.append("로그인 상태 {}".format(r["session"]))
    parts.append("화면 너비 {}px".format(vp.get("width", 1600)))
    parts.append("크롬(Playwright)")
    return " · ".join(parts)


# ---------------------------------------------------------------- 마크다운

def _fix_line(r, with_id=False, sev_prefix=True):
    """로그 한 줄. 스펙에 적어둔 `fix` 가 있으면 그대로 쓴다.

    `{actual}` 을 넣어두면 이번 실행의 실제 값으로 바꾼다. 개발팀이 일부만 고쳐 현상이
    바뀌었는데 지난번 문장을 그대로 보내면 "고쳤는데 왜 같은 말" 이 된다.
    fix 가 없으면 테스트 제목 대신 `영역 오류: 실제 현상 (SB: 근거)` 로 만든다.
    치명 건은 앞에 [치명] 을 붙인다(개발 탭은 태그로 대신). 줄 끝 케이스 ID 는 재검수 회신 때
    "3번" 이 회차마다 다른 건을 가리키는 문제를 막는다.
    """
    line = (r.get("fix") or "").strip()
    if not line:
        line = "{} 오류: {}".format((r.get("area") or "").strip() or r["title"], _actual_value(r)[:80])
        if r.get("source"):
            line += " (SB: {})".format(r["source"])
    if "{actual}" in line:
        line = line.replace("{actual}", _actual_value(r))
    if sev_prefix and (r.get("severity") or "").strip() == "치명":
        line = "[치명] " + line
    if with_id:
        line += " · {}".format(r["id"])
    return line


def build_works_log(spec, results, out_dir, run_meta=None):
    """웍스에 그대로 붙일 미반영 오류 로그. 할 일 목록이므로 한 줄짜리 지시문만 둔다."""
    meta = spec["meta"]
    items = _dev_items(results)

    if _aborted(run_meta):
        # 일부만 돈 결과를 "오류 0건" 으로 붙여넣는 사고를 막는다
        L = ["[{}] {} {}".format(datetime.now().strftime("%m/%d"), meta["project"],
                                 _held_text(run_meta, results)),
             "", "실행이 중간에 멈춰 이 결과는 전달하지 않습니다. 다시 실행한 뒤 전달합니다."]
        path = out_dir / "웍스등록.txt"
        path.write_text("\n".join(L), encoding="utf-8")
        return path

    # "수정 요청" 으로 쓰면 신규 요청으로 읽혀 결함으로 처리되지 않는다. 기획대로 안 된 것은 오류로 쓴다
    L = ["[{}] {} 검수 · 기획 미반영 오류 {}건".format(
        datetime.now().strftime("%m/%d"), meta["project"], len(items))]
    if (run_meta or {}).get("partial"):
        L.append("(일부 케이스만 실행한 결과)")
    if not items:
        L.append("")
        L.append("미반영 오류 없음.")
    n = 0
    for area, rows in _grouped_dev_items(results):
        L.append("")
        if area:
            L.append("- {}".format(area))
        for r in rows:
            n += 1
            L.append("{}. {}".format(n, _fix_line(r, with_id=True)))

    path = out_dir / "웍스등록.txt"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _dev_shots(r):
    """개발 쪽에 붙일 스크린샷. 재실행 결과가 1차와 같으면 1차(_try1) 그림은 빼고 최종만."""
    t1 = r.get("try1") or {}
    if t1 and t1.get("status") == r["status"]:
        drop = set(t1.get("shots") or [])
        return [s for s in r["shots"] if s not in drop]
    return list(r["shots"])


def build_dev_md(spec, results, out_dir, run_meta=None):
    """개발팀에 그대로 붙여넣을 수 있는 마크다운."""
    meta = spec["meta"]
    items = _dev_items(results)
    held = _held_items(results)

    if _aborted(run_meta):
        L = ["# {} {}".format(meta["project"], _held_text(run_meta, results)), "",
             "실행이 중간에 멈춰 이 결과는 개발팀에 전달하지 않습니다. 다시 실행한 뒤 전달합니다."]
        path = out_dir / "개발전달.md"
        path.write_text("\n".join(L), encoding="utf-8")
        return path

    L = ["# {} 기획 미반영 오류".format(meta["project"]), ""]
    L.append("- 대상: {}".format(meta["base_url"]))
    L.append("- 검수일: {}".format(datetime.now().strftime("%Y-%m-%d")))
    L.append("- 실행 환경: {}".format(_env_line(spec)))
    L.append("- 전체 {}건 중 **미반영 오류 {}건**{}".format(
        len(results), len(items), " (일부 케이스만 실행)" if (run_meta or {}).get("partial") else ""))
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
        L.append("| 항목 | 내용 |")
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
        if r.get("fail_url"):
            L.append("| 주소 | {} |".format(_md_cell(_mask_url(r["fail_url"]))))
        L.append("| 기대 | {} |".format(_md_cell(r["requirement"]) or "-"))
        L.append("| 실제 | {} |".format(_md_cell(_actual(r))))
        L.append("| 근거 | {} |".format(_md_cell(r["source"]) or "-"))
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
        shots = _dev_shots(r)
        if shots:
            # 마크다운에 상대경로로만 적으면 붙여넣은 곳에서 안 보인다. 파일을 따로 첨부한다
            L.append("첨부: {} (screenshots 폴더)".format(", ".join("`{}`".format(n) for n in shots)))
            L.append("")

    if held:
        L.append("---")
        L.append("")
        L.append("## 참고 · 기획 확정 후 따로 전달할 항목 {}건".format(len(held)))
        L.append("")
        for r in held:
            L.append("- `{}` {} ({})".format(
                r["id"], r["title"], KIND_META[_kind_of(r)][0]))
        L.append("")

    path = out_dir / "개발전달.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def _md_cell(s):
    return str(s or "").replace("\n", " ").replace("|", "\\|")


# ---------------------------------------------------------------- HTML 조각

def _gate_block(r, spec_case, spec_path):
    """승인 대기 · 차단 케이스에 붙는 안내. 실제 경로와 케이스 id 를 그대로 찍는다."""
    if r["status"] == "SKIPPED":
        cleanup = (spec_case or {}).get("cleanup") or []
        steps = "".join("<li>{}</li>".format(
            _esc("{} {}".format(s.get("action"), s.get("selector", s.get("url", "")))))
            for s in cleanup)
        return (
            '<div class="gate">'
            '<b>이 케이스는 운영 데이터를 씁니다.</b> 담당 기획자가 승인한 뒤 Claude 로 실행합니다.'
            '<div class="gate-why">되돌리는 절차<ol class="gate-cleanup">{steps}</ol></div>'
            '<div class="gate-how">Claude 에게 <b>"{cid} 승인하고 실행해줘"</b> 라고 요청합니다.'
            '<div class="gate-path">{path}</div></div></div>'
        ).format(cid=_esc(r["id"]), steps=steps or "<li>(없음)</li>",
                 path=_esc(spec_path or "specs/*.json"))

    if r["status"] == "BLOCKED":
        by = r.get("blocked_by")
        if by == BLOCK_BLOCKLIST:
            why = ('이 버튼은 <b>의도적으로 막아둔 것</b>입니다. 승인 여부와 무관하게 차단됩니다. '
                   '정말 필요하면 스펙의 <code>blocklist</code> 에서 직접 빼야 합니다.')
        elif by == BLOCK_UNDECLARED:
            why = ('<b>쓰기 동작이 있는데 쓰기 승인 선언이 없습니다.</b> 조회용 버튼이면 그 스텝에 '
                   '<code>"write": false</code>, 실제 쓰기면 <code>writes</code> · <code>approved</code> · '
                   '<code>cleanup</code> 을 넣어야 실행됩니다. Claude 에게 '
                   '<b>"{} 쓰기 판정 확인해줘"</b> 라고 요청합니다.').format(_esc(r["id"]))
        elif by == BLOCK_RESIDUE_WRITE:
            why = '잔여 데이터 점검에는 읽기 스텝만 둘 수 있습니다.'
        else:
            # BLOCK_NO_CLEANUP · BLOCK_FIRST_GOTO
            why = ('쓰기 케이스인데 <b>정리(cleanup) 절차가 없습니다.</b> 만든 데이터를 되돌릴 '
                   '방법을 스펙에 넣어야 실행됩니다.')
        return '<div class="gate gate-blocked">{}<div class="gate-path">{}</div></div>'.format(
            why, _esc(spec_path or "specs/*.json"))
    return ""


STEP_ICON = {"OK": ("✓", "통과"), "FAIL": ("✕", "실패")}


def _steps_html(r):
    """스텝 목록. 실패한 케이스만 펼친 채로 둔다."""
    if not r["steps"]:
        return ""
    lis = []
    for s in r["steps"]:
        icon, sr = STEP_ICON[s["status"]]
        cls = "step-fail" if s["status"] == "FAIL" else ""
        wtag = '<span class="tag tag-w">쓰기</span>' if s["write"] else ""
        phase = '<span class="tag">정리</span>' if s["phase"] == "cleanup" else ""
        msg = '<div class="stepmsg">{}</div>'.format(_esc(s["msg"])) if s["msg"] else ""
        lis.append('<li class="{}"><span class="sico" aria-label="{}">{}</span> {} {}{}{}</li>'.format(
            cls, sr, icon, _esc(s["label"]), phase, wtag, msg))

    total = len(r["steps"])
    failed_at = next((s["n"] for s in r["steps"] if s["status"] == "FAIL"), None)
    if failed_at:
        summary = "스텝 {}개 · {}번째에서 어긋남".format(total, failed_at)
    else:
        summary = "스텝 {}개 · 전부 통과".format(total)
    return ('<details class="stepbox"{o}><summary>{s}</summary>'
            '<ol class="steps">{b}</ol></details>').format(
        o=" open" if failed_at else "", s=_esc(summary), b="".join(lis))


def _shots_html(r, big=False, names=None):
    names = r["shots"] if names is None else names
    if not names:
        return ""
    figs = "".join(
        '<figure><button type="button" class="shot" onclick="zoom(this)" '
        'aria-label="{0} 크게 보기"><img src="screenshots/{0}" alt="{0}" loading="lazy">'
        '</button><figcaption>{0}</figcaption></figure>'.format(_esc(n))
        for n in names)
    return '<div class="shots{}">{}</div>'.format(" big" if big else "", figs)


def _case_body(r, spec_case, spec_path):
    # 사유가 실패 스텝 메시지와 같은 문장이면 바로 아래에서 또 보인다. 한 번만 싣는다.
    txt = r["reason"]
    if txt and any(st["status"] == "FAIL" and st["msg"] and st["msg"] in txt
                   for st in r["steps"]):
        txt = ""
    rows = ""
    if txt:
        rows += '<dt>사유</dt><dd class="reason">{}</dd>'.format(_esc(txt))
    if r.get("diagnosis"):
        rows += '<dt>연속 사용</dt><dd>{}</dd>'.format(_esc(r["diagnosis"]))
    t1 = r.get("try1")
    if t1 and not (t1["status"] == r["status"] and t1["reason"] == r["reason"]):
        # 1차와 결과 · 사유가 같으면 같은 문장을 한 번 더 쓰지 않는다 (태그 '재실행도 같음' 으로 충분)
        rows += '<dt>1차 실행</dt><dd>{} · {}</dd>'.format(
            _esc(STATUS_META[t1["status"]][0]), _esc(t1["reason"]))
    elif r.get("flaky"):
        rows += '<dt>1차 실행</dt><dd>{}</dd>'.format(_esc(r["flaky"]))
    if r.get("fail_url"):
        rows += '<dt>실패 시점 주소</dt><dd class="src">{}</dd>'.format(_esc(_mask_url(r["fail_url"])))
    if r.get("unclean"):
        vals = ", ".join("'{}'".format(v) for v in r.get("unclean_inputs") or []) or "(입력값 없음)"
        rows += ('<dt>운영 데이터</dt><dd class="reason">정리되지 않음 - 이 값으로 만든 데이터를 '
                 '직접 찾아 지워야 합니다: {}</dd>').format(_esc(vals))
    if r.get("get_suspects"):
        rows += ('<dt>GET 쓰기 의심</dt><dd>클릭 직후 나간 요청입니다. 안전망이 막지 못하는 방식이라 '
                 '운영 데이터가 바뀌었는지 확인이 필요합니다. 쓰기 주소면 스펙 blocklist 에 적습니다.'
                 '<ul class="sus">{}</ul></dd>').format(
            "".join("<li>{}</li>".format(_esc(_mask_url(s))) for s in r["get_suspects"]))
    if r.get("changed"):
        rows += '<dt>지난 현상</dt><dd>{} <span class="ktag k-warn">현상 바뀜</span></dd>'.format(
            _esc(r["changed"]))
    if r.get("req"):
        rows += '<dt>요건 ID</dt><dd class="src">{}</dd>'.format(_esc(", ".join(r["req"])))
    if r.get("trace"):
        # 동작 기록에는 운영 세션 쿠키가 들어 있어 기획팀 안에서만 본다
        rows += ('<dt>동작 기록</dt><dd class="src"><a href="{0}">{0}</a> · 기획팀 내부용 '
                 '(넘길 때는 --strip-trace 사본)</dd>').format(_esc(r["trace"]))
    return """<dl>
    <dt>검증 요건</dt><dd>{req}</dd>
    <dt>근거</dt><dd class="src">{src}</dd>
    {rows}
  </dl>
  {gate}{steps}{signals}{shots}""".format(
        req=_esc(r["requirement"]), src=_esc(r["source"]), rows=rows,
        gate=_gate_block(r, spec_case, spec_path),
        steps=_steps_html(r), signals=_signals_html(r),
        shots=_shots_html(r, big=r["status"] in ("FAIL", "ERROR", "FLAKY")))


def _signals_html(r):
    """콘솔 · 네트워크 오류. 판정과 무관한 참고 정보라 접어둔다."""
    sig = r.get("signals") or []
    if not sig:
        return ""
    return ('<details class="case-sig"><summary>콘솔·네트워크 오류 {}건 (판정과 무관)</summary>'
            '<ul>{}</ul></details>').format(
        len(sig), "".join("<li>{}</li>".format(_esc(s)) for s in sig))


def _case_block(r, spec_case, spec_path):
    """통과는 한 줄로 접고, 나머지는 펼친다."""
    label, color, bg, icon = STATUS_META[r["status"]]
    kind = _kind_of(r)
    tags = []
    # 스펙 하나에 세션이 여럿이면(비회원 · 회원 · 어드민) 어느 로그인 상태로 본 건지 표시한다
    if r.get("session"):
        tags.append('<span class="ktag">{}</span>'.format(_esc(r["session"])))
    if kind != DEFAULT_KIND:
        tags.append('<span class="ktag">{}</span>'.format(_esc(KIND_META[kind][0])))
    sev = (r.get("severity") or "").strip()
    if sev:
        tags.append('<span class="ktag{}">{}</span>'.format(" k-red" if sev == "치명" else "", _esc(sev)))
    if r.get("flaky"):
        tags.append('<span class="ktag">재실행 통과</span>')
    elif r.get("retried"):
        tags.append('<span class="ktag">재실행도 같음</span>')
    if r.get("trend"):
        cls = TREND_TONE.get(r["trend"], "") if r["status"] in ("FAIL", "ERROR") else ""
        tags.append('<span class="ktag{}">{}</span>'.format(cls, _esc(TREND_META[r["trend"]])))

    head = ('<span class="seq">{seq}</span>'
            '<span class="badge" style="color:{c};background:{bg}">{i} {l}</span>'
            '<span class="cid">{id}</span><span class="ctitle">{t}</span>'
            '<span class="tags">{k}<span class="dur">{d}s</span></span>').format(
        seq=r.get("_seq", ""), c=color, bg=bg, i=icon, l=label, id=_esc(r["id"]),
        t=_esc(r["title"]), k="".join(tags), d=r["duration"])

    attrs = 'id="case-{id}" class="case case-{s}" data-status="{s}" data-kind="{k}"'.format(
        id=_esc(r["id"]), s=r["status"], k=kind)
    body = _case_body(r, spec_case, spec_path)

    if r["status"] == "PASS":
        return ('<details {a}><summary class="chead">{h}</summary>'
                '<div class="cbody">{b}</div></details>').format(a=attrs, h=head, b=body)
    return ('<section {a}><div class="chead">{h}</div>'
            '<div class="cbody">{b}</div></section>').format(a=attrs, h=head, b=body)


def _cases_html(results, by_id, spec_path):
    """영역별로 묶고, 묶음 안에서는 할 일이 위로 오게 상태순(같은 상태는 실행 순서)으로 놓는다.

    통과는 묶음마다 "통과 N건" 한 줄로 접는다. 100건 중 70건이 통과면 그 줄만 수천 px 이다.
    """
    groups = _area_groups(results)
    # 영역도 안에 든 가장 심한 상태 순으로. 영역 2 의 실패가 영역 1 의 조건 미충족보다 아래로 가지 않게
    groups.sort(key=lambda g: (min(STATUS_RANK.get(r["status"], 9) for r in g[1]),
                               min(r.get("_seq", 0) for r in g[1])))
    out = []
    for area, rows in groups:
        rows = sorted(rows, key=lambda r: (STATUS_RANK.get(r["status"], 9), r.get("_seq", 0)))
        todo = [r for r in rows if r["status"] != "PASS"]
        passed = [r for r in rows if r["status"] == "PASS"]
        inner = "".join(_case_block(r, by_id.get(r["id"]), spec_path) for r in todo)
        if passed:
            inner += ('<details class="passgroup"><summary>통과 {}건</summary>{}</details>').format(
                len(passed), "".join(_case_block(r, by_id.get(r["id"]), spec_path) for r in passed))
        head = ('<h2 class="areah">{} <span class="areacnt" data-total="{n}">{n}건</span></h2>'.format(
            _esc(area), n=len(rows)) if area else "")
        out.append('<div class="area">{}{}</div>'.format(head, inner))
    return "".join(out)


def _held_note(held):
    lis = "".join('<li><span class="ktag">{}</span> {} · {}</li>'.format(
        _esc(KIND_META[_kind_of(r)][0]), _esc(r["id"]), _esc(r["title"]))
        for r in held)
    return ('<div class="heldnote"><b>기획 확정 후 따로 전달할 항목 {n}건</b>'
            '<ul>{lis}</ul></div>').format(n=len(held), lis=lis)


def _dev_block(spec, results, held, md_name, log_name, log_text, run_meta=None):
    if _aborted(run_meta):
        # 일부만 돈 결과는 넘기지 않는다. 복사 버튼 · 로그 본문을 만들지 않는다
        return ('<div class="devhead"><div><b>{}</b><p>실행이 중간에 멈춰 이 결과는 개발팀에 전달하지 '
                '않습니다. 다시 실행한 뒤 전달합니다.</p></div></div>').format(_esc(_held_text(run_meta, results)))
    rows_in = _flat_dev_items(results)
    if not rows_in:
        # 넘길 게 없는데 복사 버튼을 남겨두면 읽는 사람이 헷갈린다
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
        tags = []
        if area:
            tags.append('<span class="ktag">{}</span>'.format(_esc(area)))
        sev = (r.get("severity") or "").strip()
        if sev:
            tags.append('<span class="ktag{}">{}</span>'.format(" k-red" if sev == "치명" else "", _esc(sev)))
        if r.get("trend") == "still":
            tags.append('<span class="ktag k-red">계속 실패</span>')
        if r.get("session"):
            tags.append('<span class="ktag">{}</span>'.format(_esc(r["session"])))
        url_row = ('<dt>주소</dt><dd class="src">{}</dd>'.format(_esc(_mask_url(r["fail_url"])))
                   if r.get("fail_url") else "")
        rows.append("""<article class="devitem">
  <h3><span class="devno">{n}</span><span class="devt">{title}</span>{tags}</h3>
  <dl>
    <dt>기대</dt><dd>{req}</dd>
    <dt>실제</dt><dd class="reason">{act}</dd>
    <dt>근거</dt><dd class="src">{src}</dd>
    {url_row}
    <dt>케이스</dt><dd class="src">{id}</dd>
  </dl>
  {repro}
  {shots}
</article>""".format(
            n=i, title=_esc(_fix_line(r, sev_prefix=False)), tags="".join(tags), req=_esc(r["requirement"]),
            act=_esc(_actual(r)), src=_esc(r["source"]), id=_esc(r["id"]), url_row=url_row,
            repro=('<div class="devrepro"><b>재현 절차</b><ol>{}</ol></div>'.format(steps)
                   if steps else ""),
            shots=_shots_html(r, big=True, names=_dev_shots(r))))

    return """<div class="devhead">
  <div><b>기획 미반영 오류 {n}건</b><p>{env}</p></div>
  <button type="button" id="copybtn" onclick="copyDev()">복사</button>
</div>
<pre id="devmd" class="logbox">{log}</pre>
<div class="devfile"><code>{logf}</code> · <code>{md}</code></div>
{heldnote}
<h2 class="devsub">항목별 근거</h2>
{inner}""".format(n=len(rows_in), env=_esc(_env_line(spec)), md=_esc(md_name),
                  logf=_esc(log_name), log=_esc(log_text),
                  heldnote=_held_note(held) if held else "", inner="".join(rows))


# ---------------------------------------------------------------- 요약 블록

COV_ORDER = ("미반영", "미검증", "점검 필요", "불안정", "미확인", "일부 통과", "미실행", "통과", "제외")
COV_RED = ("미반영",)
COV_ORANGE = ("미검증", "점검 필요", "불안정")


def coverage_rows(spec, results):
    """요건마다 연결된 케이스와 상태. 기획서 요건 중 케이스가 아예 없는 빈칸을 드러낸다."""
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
            elif "FLAKY" in ss:
                st, note = "불안정", "재실행에서만 통과"
            elif ss == {"PASS"}:
                st = "통과"
            elif "PASS" in ss:
                st, note = "일부 통과", "나머지 케이스는 승인 대기 · 조건 미충족 · 차단"
            else:
                st, note = "미확인", "승인 대기 · 조건 미충족 · 차단"
        rows.append({"id": q["id"], "text": q.get("text", ""), "source": q.get("source", ""),
                     "cases": cids, "status": st, "note": note})
    return rows


def _coverage_html(rows, partial):
    if not rows:
        return ""
    cnt = {k: sum(1 for x in rows if x["status"] == k) for k in COV_ORDER}
    summ = " · ".join("{} {}".format(k, cnt[k]) for k in COV_ORDER if cnt[k])

    def links(cids):
        return ", ".join('<a href="#case-{0}">{0}</a>'.format(_esc(c)) for c in cids) or "-"

    trs = "".join(
        '<tr class="{cls}"><td class="cid">{id}</td><td>{text}<div class="src">{src}</div></td>'
        '<td class="cid">{cases}</td><td><b>{st}</b><div class="src">{note}</div></td></tr>'.format(
            cls="cov-red" if x["status"] in COV_RED else ("cov-orange" if x["status"] in COV_ORANGE else ""),
            id=_esc(x["id"]), text=_esc(x["text"]), src=_esc(x["source"]), cases=links(x["cases"]),
            st=_esc(x["status"]), note=_esc(x["note"]))
        for x in sorted(rows, key=lambda x: COV_ORDER.index(x["status"])))
    part = " · 일부 케이스만 실행함" if partial else ""
    # 테두리 색은 안에 든 가장 심한 상태로
    tone = " t-red" if cnt["미반영"] else (" t-org" if any(cnt[k] for k in COV_ORANGE) else "")
    return ('<details class="info{t}" id="cov"><summary>요건 커버리지 · 요건 {n}개 · {s}{p}</summary>'
            '<div class="tablewrap"><table class="cov"><thead><tr><th>ID</th><th>요건</th><th>케이스</th>'
            '<th>상태</th></tr></thead><tbody>{trs}</tbody></table></div></details>').format(
        t=tone, n=len(rows), s=_esc(summ), p=part, trs=trs)


def _trend_html(results, prev):
    if not prev:
        return ""
    groups = {k: [r for r in results if r.get("trend") == k] for k in TREND_META}
    summ = " · ".join("{} {}".format(TREND_META[k], len(v)) for k, v in groups.items() if v) or "변화 없음"
    body = "".join(
        "<p><b>{}</b></p><ul>{}</ul>".format(
            TREND_META[k], "".join('<li><a href="#case-{0}">{0}</a> {1}</li>'.format(
                _esc(r["id"]), _esc(r["title"])) for r in v))
        for k, v in groups.items() if v)
    tone = (" t-red" if groups["still"] or groups["new"]
            else (" t-org" if groups["unjudged"] or groups["still_err"] else ""))
    return ('<details class="info{t}"><summary>지난 실행 대비 · {s}</summary>'
            '<p class="src">비교 기준: {n}</p>{b}</details>').format(
        t=tone, s=_esc(summ), n=_esc(prev["name"]), b=body)


def _list_block(tone, title, desc, items, bid=""):
    if not items:
        return ""
    return ('<details class="info {c}"{i}><summary>{t}</summary><p>{d}</p>'
            '<ul>{li}</ul></details>').format(
        c=tone, i=' id="{}"'.format(bid) if bid else "", t=_esc(title), d=desc, li="".join(items))


def _todo_html(results, cov_rows, warnings, n_dev, aborted=False):
    """칩 아래 할 일 한 줄. 누가 무엇을 해야 하는지만."""
    cnt = {k: sum(1 for r in results if r["status"] == k) for k in STATUS_META}
    items = []
    unclean = [r for r in results if r.get("unclean")]
    if unclean:
        # 운영에 남은 테스트 데이터가 가장 급하다
        items.append('<a class="todo red" href="#case-{}">운영 데이터 미정리 {}건</a>'.format(
            _esc(unclean[0]["id"]), len(unclean)))
    if n_dev and not aborted:
        items.append('<button type="button" class="todo red" onclick="tab(\'dev\')">개발 전달 {}건</button>'.format(n_dev))
    sus = [r for r in results if r.get("get_suspects")]
    if sus:
        items.append('<a class="todo" href="#case-{}">GET 쓰기 의심 {}건</a>'.format(
            _esc(sus[0]["id"]), len(sus)))
    nofix = [r for r in _dev_items(results) if not (r.get("fix") or "").strip()]
    if nofix and not aborted:
        items.append('<a class="todo" href="#lint" onclick="openInfo(\'lint\')">fix 없음 {}건</a>'.format(len(nofix)))
    for k, word in (("ERROR", "점검 필요"), ("FLAKY", "불안정"), ("BLOCKED", "차단"), ("SKIPPED", "승인 대기")):
        if cnt[k]:
            items.append('<button type="button" class="todo" onclick="flt(\'{}\')">{} {}건</button>'.format(
                k, word, cnt[k]))
    unver = sum(1 for x in cov_rows if x["status"] == "미검증")
    if unver:
        items.append('<a class="todo" href="#cov" onclick="openInfo(\'cov\')">미검증 요건 {}개</a>'.format(unver))
    if _has_warn(warnings, W_SELECTOR_UPDATE):
        items.append('<a class="todo" href="#warn" onclick="openInfo(\'warn\')">셀렉터 갱신 요청</a>')
    if _has_warn(warnings, W_RESIDUE):
        items.append('<a class="todo" href="#warn" onclick="openInfo(\'warn\')">잔여 데이터 확인</a>')
    if not items:
        return '<div class="todoline"><b>할 일</b> 없음</div>'
    return '<div class="todoline"><b>할 일</b>{}</div>'.format("".join(items))


def _aborted_html(run_meta, results):
    info = (run_meta or {}).get("aborted_info")
    if not info:
        return ""
    unclean = [r for r in results if r.get("unclean")]
    ul = ""
    if unclean:
        ul = "<p><b>정리되지 않은 쓰기 케이스</b> - 운영 데이터를 직접 확인해 지워야 합니다.</p><ul>{}</ul>".format(
            "".join("<li>{} {}{}</li>".format(
                _esc(r["id"]), _esc(r["title"]),
                " · 입력값 " + _esc(", ".join("'{}'".format(v) for v in r.get("unclean_inputs") or []))
                if r.get("unclean_inputs") else "") for r in unclean))
    if info.get("why") == "error":
        head = "{at}번째 케이스({cid})에서 실행이 멈췄습니다 ({msg}).".format(
            at=info["at"], cid=_esc(info["case"]), msg=_esc(str(info.get("msg", ""))[:120]))
        how = "원인을 확인한 뒤 다시 실행합니다."
    else:
        head = "로그인 세션 만료로 {at}번째 케이스({cid})에서 멈췄습니다.".format(
            at=info["at"], cid=_esc(info["case"]))
        how = "login_qa.bat 으로 다시 로그인한 뒤 실행합니다."
    return ('<div class="alert"><b>{head}</b> 전체 {total}건 중 {done}건 결과이며, 개발팀에 전달하지 '
            '않습니다. {how}{ul}</div>').format(
        head=head, total=info["total"], done=min(len(results), info["total"]), how=how, ul=ul)


# 스펙 점검 경고(종류별로 한 줄에 묶음, 회색)와 운영 위험 경고(주황)를 나눈다.
# 경고는 (코드, 문장). 묶음은 코드로 정한다 (문장을 고쳐도 묶음이 안 깨지게). 여기 없는 코드는 주황
LINT_KINDS = {
    W_NO_FIX: "fix 없음",
    W_NO_AREA: "area 없음",
    W_ABSENCE_NO_ANCHOR: "anchor 없는 판정",
    W_PRE_NO_ANCHOR: "anchor 없는 판정",
    W_UNKNOWN_REQ: "없는 요건 ID",
    W_SOFT_POSITION: "soft 위치",
    W_APPROVED_FIRST_GOTO: "승인 쓰기 케이스 첫 스텝 goto 아님",
}


def _has_warn(warnings, code):
    return any(c == code for c, _ in warnings)


def _warn_blocks(warnings):
    lint, risk = {}, []
    for code, w in warnings:
        kind = LINT_KINDS.get(code)
        if kind:
            lint.setdefault(kind, []).append(w.split(":", 1)[0])
        else:
            risk.append(w)
    out = ""
    if risk:
        out += ('<details class="info t-org" id="warn"><summary>확인할 경고 {}건</summary>'
                '<ul>{}</ul></details>').format(len(risk), "".join("<li>{}</li>".format(_esc(w)) for w in risk))
    if lint:
        lis = "".join("<li>{} {}건: {}</li>".format(_esc(k), len(v), _esc(", ".join(dict.fromkeys(v))))
                      for k, v in lint.items())
        out += ('<details class="info" id="lint"><summary>스펙 점검 {n}건</summary>'
                '<p>판정에는 영향이 없습니다. 스펙을 고칠 때 같이 정리합니다.</p><ul>{l}</ul></details>').format(
            n=sum(len(v) for v in lint.values()), l=lis)
    return out


# ---------------------------------------------------------------- CSS / JS

CSS = """
* { box-sizing: border-box; }
:root { --red:#b42318; --red-bg:#fdecea; --org:#9a3412; --org-bg:#fff1e6; --org-line:#ea7a2c;
        --gray:#374151; --gray-bg:#eef1f5; --sub:#5f6672; --line:#dcdfe4; --link:#178BF5; }
body { margin:0; padding:28px 24px 64px; background:#f4f5f7; color:#16191d;
       font:14px/1.6 'Malgun Gothic','맑은 고딕',system-ui,sans-serif; }
.wrap { max-width:1000px; margin:0 auto; }
/* #178BF5 는 흰 바탕 글자로 대비가 3.5:1 이라 작은 글씨로는 못 읽는다. 글자는 진하게, 밑줄을 파랑으로 */
a { color:#16191d; text-decoration-color:var(--link); text-decoration-thickness:2px; text-underline-offset:2px; }
a:hover { color:var(--link); }
:focus-visible { outline:2px solid var(--link); outline-offset:2px; }

/* 머리말 */
header.top { margin-bottom:18px; }
header.top h1 { font-size:24px; margin:0 0 2px; letter-spacing:-.3px; }
header.top .kicker { font-size:12px; color:var(--sub); font-weight:700;
                     letter-spacing:.06em; margin-bottom:6px; }
header.top .meta { color:var(--sub); font-size:12.5px; overflow-wrap:anywhere; }
header.top .meta code { background:#e9ebef; padding:1px 6px; border-radius:3px; font-size:12px; }
.drytag { display:inline-block; background:var(--gray-bg); color:var(--gray);
          padding:1px 8px; border-radius:3px; font-size:11.5px; font-weight:700; }

/* 탭 */
.tabs { display:flex; gap:2px; border-bottom:1px solid var(--line); margin-bottom:18px; }
.tabs button { background:none; border:0; border-bottom:2px solid transparent;
               padding:9px 16px; font:600 14px/1.4 inherit; color:var(--sub);
               cursor:pointer; margin-bottom:-1px; }
.tabs button:hover { color:#16191d; }
.tabs button.on { color:#16191d; border-bottom-color:#16191d; }
.tabs .cnt { display:inline-block; background:#e9ebef; color:var(--gray);
             border-radius:9px; padding:0 7px; font-size:11.5px; margin-left:6px; }
.tabs button.on .cnt { background:#16191d; color:#fff; }

/* 요약 칩 - 스크롤해도 따라온다. 링크로 이동한 대상이 칩 줄 밑에 숨지 않게 여백을 둔다 */
.chipbar { position:sticky; top:0; z-index:5; background:#f4f5f7; padding:8px 0 6px;
           border-bottom:1px solid var(--line); }
.case, .info, .area { scroll-margin-top:84px; }
.chips { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.chip { display:flex; align-items:baseline; gap:6px; background:#fff; cursor:pointer;
        border:1px solid var(--line); border-radius:999px; padding:5px 14px 5px 12px;
        font:inherit; font-size:12.5px; color:var(--gray); }
.chip b { font-size:15px; font-weight:700; color:#16191d; }
.chip:hover:not(:disabled) { border-color:#9aa1aa; }
.chip.on { background:#16191d; border-color:#16191d; color:#fff; }
.chip.on b { color:#fff; }
.chip:disabled { opacity:.45; cursor:default; }
.chip.t-red { border-color:#f0b5ae; background:#fff7f6; }
.chip.t-red b { color:var(--red); font-size:17px; }
.chip.t-org { border-color:#f5c9a6; background:#fff8f2; }
.chip.t-org b { color:var(--org); }
.chip.on b { color:#fff; }
.total { margin-left:auto; font-size:12.5px; color:var(--sub); }
.fltbar { font-size:12px; color:var(--sub); min-height:19px; padding-top:4px; }
.fltbar button { background:none; border:0; color:#16191d; cursor:pointer;
                 font:inherit; text-decoration:underline; text-decoration-color:var(--link);
                 text-decoration-thickness:2px; text-underline-offset:2px; padding:0 0 0 6px; }

/* 할 일 한 줄 */
.todoline { display:flex; flex-wrap:wrap; align-items:center; gap:6px 8px; background:#fff;
            border:1px solid var(--line); border-radius:6px; padding:8px 12px; margin:8px 0 14px;
            font-size:13px; }
.todoline > b { margin-right:4px; }
.todo { background:var(--org-bg); color:var(--org); border:0; border-radius:4px; padding:2px 9px;
        font:600 12.5px/1.6 inherit; cursor:pointer; text-decoration:none; }
.todo.red { background:var(--red-bg); color:var(--red); }
.todo:hover { text-decoration:underline; }

/* 알림 · 안내 블록 (기본 접힘) */
.info { border-radius:6px; margin-bottom:10px; font-size:12.5px; background:#fff;
        border:1px solid var(--line); border-left:3px solid #9aa1aa; }
.info > summary { cursor:pointer; padding:9px 14px; font-weight:700; }
.info ul { margin:0 0 12px; padding:0 14px 0 34px; }
.info p { margin:0 14px 8px; color:var(--gray); }
.info.t-org { border-left-color:var(--org-line); }
.info.t-red { border-left-color:var(--red); }
.alert { background:var(--org-bg); border:1px solid #f5c9a6; border-left:3px solid var(--org-line);
         border-radius:6px; padding:12px 14px; margin-bottom:14px; font-size:13px; color:#4a1d07; }
.alert ul { margin:4px 0 0; padding-left:20px; }
.alert p { margin:8px 0 0; }
.tablewrap { overflow-x:auto; }
table.cov { width:calc(100% - 28px); margin:0 14px 12px; border-collapse:collapse; }
.cov th { text-align:left; color:var(--sub); font-weight:400; border-bottom:1px solid #e3e6ea;
          padding:4px 8px; }
.cov td { border-bottom:1px solid #f0f1f3; padding:6px 8px; vertical-align:top; }
.cov tr.cov-red td { background:#fff7f6; }
.cov tr.cov-red b { color:var(--red); }
.cov tr.cov-orange b { color:var(--org); }

/* 영역 묶음 */
.areah { font-size:14px; margin:22px 0 8px; color:#16191d; }
.areah span { color:var(--sub); font-weight:400; font-size:12.5px; margin-left:4px; }
.passgroup { margin:0 0 10px; }
.passgroup > summary { cursor:pointer; font-size:12.5px; color:var(--sub); padding:6px 2px; }
.passgroup[open] > summary { margin-bottom:6px; }

/* 케이스 */
.case { background:#fff; border:1px solid #e3e6ea; border-left:3px solid #d5d9df;
        border-radius:6px; margin-bottom:10px; display:block; }
.case[hidden] { display:none; }
.case-FAIL { border-left-color:var(--red); box-shadow:0 1px 3px rgba(180,35,24,.10); }
.case-ERROR, .case-FLAKY, .case-SKIPPED, .case-BLOCKED { border-left-color:var(--org-line); }
.case-NA, .case-PASS { border-left-color:#c3c8cf; }
.case-PASS { background:#fcfcfd; }
.chead { display:flex; align-items:center; flex-wrap:wrap; gap:6px 10px; padding:11px 16px;
         list-style:none; }
details.case > summary.chead { cursor:pointer; }
details.case > summary.chead::-webkit-details-marker { display:none; }
details.case[open] > summary.chead { border-bottom:1px solid #eef0f3; }
.seq { font:12px Consolas,monospace; color:var(--sub); min-width:18px; }
.badge { padding:2px 9px; border-radius:3px; font-size:11.5px; font-weight:700; white-space:nowrap; }
.cid { font:700 12px Consolas,monospace; color:var(--sub); }
.ctitle { flex:1 1 240px; min-width:0; font-size:14px; font-weight:600; }
.case-PASS .ctitle { font-weight:400; color:var(--gray); }
.tags { display:flex; flex-wrap:wrap; gap:4px; align-items:center; }
.ktag { background:var(--gray-bg); color:var(--gray); font-size:11px; padding:1px 7px;
        border-radius:3px; white-space:nowrap; }
.ktag.k-red { background:var(--red-bg); color:var(--red); font-weight:700; }
.ktag.k-warn { background:var(--org-bg); color:var(--org); }
.dur { color:var(--sub); font-size:11.5px; }
.cbody { padding:14px 16px 16px; }
.case-sig { margin:8px 0 0; font-size:12px; color:var(--sub); }
.case-sig > summary { cursor:pointer; }
.case-sig ul { margin:4px 0 0; padding-left:20px; word-break:break-all; }

dl { display:grid; grid-template-columns:max-content 1fr; gap:3px 14px; margin:0 0 12px; font-size:13px; }
dt { color:var(--sub); font-size:12.5px; }
dd { margin:0; min-width:0; overflow-wrap:anywhere; }
.src { color:var(--sub); font-size:12px; }
.reason { color:var(--red); }
/* 빨강은 개발 전달(실패)에만. 점검 필요 · 차단 · 승인 대기는 주황, 조건 미충족은 회색 */
.case:not(.case-FAIL) .reason, .case:not(.case-FAIL) .step-fail { color:var(--org); }
.case.case-NA .reason, .case.case-NA .step-fail { color:var(--gray); }
.sus { margin:4px 0 0; padding-left:18px; color:var(--gray); font-size:12px; word-break:break-all; }

/* 승인 안내 */
.gate { background:var(--org-bg); border:1px solid #f5c9a6; border-radius:5px;
        padding:11px 13px; margin:0 0 12px; font-size:12.5px; color:#4a1d07; }
.gate-why, .gate-how { font-size:12px; margin-top:6px; }
.gate-cleanup { margin:4px 0 0; padding-left:20px; color:var(--gray); }
.gate-path { font-family:Consolas,monospace; font-size:11.5px; color:#16191d;
             background:#fff; border:1px solid #e3e6ea; border-radius:4px;
             padding:5px 8px; margin:5px 0; word-break:break-all; }

/* 스텝 */
.stepbox { margin:0; }
.stepbox > summary { cursor:pointer; font-size:12px; color:var(--sub); padding:3px 0; }
.stepbox > summary:hover { color:#16191d; }
.stepbox[open] > summary { margin-bottom:5px; }
.steps { margin:0; padding-left:22px; font-size:12.5px; color:var(--gray); }
.steps li { padding:2px 0; overflow-wrap:anywhere; }
.sico { font-weight:700; margin-right:4px; }
.step-fail { color:var(--red); font-weight:600; }
.step-skip { color:var(--sub); }
.stepmsg { background:var(--red-bg); color:var(--red); padding:6px 10px; border-radius:4px;
           margin:4px 0; font-size:12px; font-weight:400; }
.case:not(.case-FAIL) .stepmsg { background:var(--org-bg); color:var(--org); }
.case.case-NA .stepmsg { background:var(--gray-bg); color:var(--gray); }
.tag { background:var(--gray-bg); color:var(--gray); font-size:10px; padding:1px 5px;
       border-radius:3px; margin-left:4px; }
.tag-w { background:var(--org-bg); color:var(--org); }

/* 스크린샷 - 잘라내면 정작 봐야 할 부분이 사라지므로 비율을 유지한다 */
.shots { display:flex; flex-wrap:wrap; gap:12px; margin-top:12px; }
figure { margin:0; max-width:320px; }
.shots.big figure { max-width:100%; }
button.shot { display:block; padding:0; border:1px solid #e3e6ea; border-radius:4px;
              background:#fff; cursor:zoom-in; }
button.shot:hover { border-color:var(--link); }
button.shot img { display:block; max-width:100%; max-height:220px; object-fit:contain; }
.shots.big button.shot img { max-height:360px; }
figcaption { font-size:11px; color:var(--sub); margin-top:3px; word-break:break-all; }

/* 개발 전달용 */
#view-dev { display:none; }
.devhead { display:flex; align-items:flex-start; gap:16px; background:#fff;
           border:1px solid var(--line); border-radius:6px; padding:14px 16px; margin-bottom:8px; }
.devhead > div { flex:1; }
.devhead b { font-size:15px; }
.devhead p { margin:3px 0 0; color:var(--sub); font-size:12.5px; }
.devhead button { background:#16191d; color:#fff; border:0; border-radius:5px;
                  padding:8px 16px; font:600 13px/1.4 inherit; cursor:pointer; white-space:nowrap; }
.devhead button:hover { background:#31363d; }
/* 웍스에 붙었을 때와 같게 보이도록 본문 글꼴 그대로, 줄바꿈만 살린다 */
.logbox { background:#fff; border:1px solid var(--line); border-radius:6px;
          padding:16px 20px; margin:0 0 8px; white-space:pre-wrap;
          font-family:inherit; font-size:13.5px; line-height:1.9; color:#16191d; }
.devfile { font-size:12px; color:var(--sub); margin-bottom:22px; }
.devfile code { background:#e9ebef; padding:1px 6px; border-radius:3px; }
.devsub { font-size:13px; color:var(--sub); font-weight:700; margin:0 0 10px;
          border-top:1px solid #e3e6ea; padding-top:16px; }
.heldnote { background:#fff; border:1px solid var(--line); border-left:3px solid #9aa1aa;
            border-radius:6px; padding:12px 16px; margin-bottom:16px; font-size:12.5px; }
.heldnote ul { margin:6px 0 0; padding-left:18px; color:var(--gray); }
.devitem { background:#fff; border:1px solid #e3e6ea; border-radius:6px;
           padding:18px 20px; margin-bottom:12px; }
.devitem h3 { font-size:15px; margin:0 0 12px; display:flex; flex-wrap:wrap; gap:6px 10px;
              align-items:baseline; }
.devt { flex:1 1 240px; min-width:0; }
.devno { display:inline-flex; align-items:center; justify-content:center;
         min-width:22px; height:22px; background:#16191d; color:#fff;
         border-radius:50%; font-size:12px; flex:none; }
.devrepro { font-size:12.5px; margin-top:10px; }
.devrepro b { color:var(--sub); font-size:12.5px; font-weight:400; }
.devrepro ol { margin:4px 0 0; padding-left:20px; color:var(--gray); }
.devrepro li { padding:1px 0; overflow-wrap:anywhere; }

/* 확대 보기 */
#lb { position:fixed; inset:0; background:rgba(15,18,21,.93); z-index:999;
      display:none; overflow:auto; }
#lb.on { display:block; }
#lb .bar { position:sticky; top:0; display:flex; align-items:center; gap:14px;
           padding:10px 16px; background:rgba(15,18,21,.97); color:#e5e7eb; font-size:12px; }
#lb .bar b { font-weight:600; flex:1; word-break:break-all; }
#lb button { background:#374151; color:#e5e7eb; border:0; border-radius:4px;
             padding:6px 12px; font-size:12px; cursor:pointer; font-family:inherit; }
#lb button:hover { background:#4b5563; }
#lb .stage { padding:16px; text-align:center; min-height:calc(100% - 37px); }
#lb img { max-width:100%; background:#fff; border-radius:4px; cursor:zoom-in; }
#lb.full img { max-width:none; cursor:zoom-out; }

footer { margin-top:28px; padding-top:14px; border-top:1px solid #dfe2e6;
         font-size:12px; color:var(--sub); }
footer ul { margin:4px 0 0; padding-left:20px; }

@media (max-width:600px) {
  body { padding:16px 16px 48px; }
  /* 좁은 화면에서는 칩 줄이 화면의 1/6 을 차지해 따라오지 않게 한다 */
  .chipbar { position:static; }
  .case, .info, .area { scroll-margin-top:8px; }
  .tabs button { padding:9px 10px; }
  .total { margin-left:0; width:100%; }
  .chead { padding:10px 12px; }
  .cbody { padding:12px; }
  .devhead { flex-direction:column; }
  dl { grid-template-columns:1fr; }
  dt { margin-top:6px; }
}
"""

JS = """
// 탭 전환 - 기획팀 전체 보기 / 개발 전달용
function tab(v) {
  // '' 로 되돌리면 CSS 의 display:none 이 다시 먹으므로 값을 그대로 지정한다
  document.getElementById('view-qa').style.display  = (v === 'qa')  ? 'block' : 'none';
  document.getElementById('view-dev').style.display = (v === 'dev') ? 'block' : 'none';
  document.querySelectorAll('.tabs button').forEach(function (b) {
    b.classList.toggle('on', b.dataset.view === v);
    b.setAttribute('aria-selected', b.dataset.view === v ? 'true' : 'false');
  });
}
document.querySelectorAll('.tabs button').forEach(function (b) {
  b.addEventListener('click', function () { tab(b.dataset.view); });
});

// 요약 칩 클릭 -> 해당 상태만 보기. 같은 칩을 다시 누르면 해제.
// 필터가 켜지면 안내 블록은 숨겨 해당 케이스가 첫 화면에 오게 한다.
var cur = null, openSaved = null;
function flt(k) {
  tab('qa');
  var prev = cur;
  cur = (cur === k) ? null : k;
  var groups = document.querySelectorAll('.passgroup');
  // 필터를 켜기 전에 사용자가 열어 둔 통과 묶음을 기억했다가, 끌 때 그대로 돌려 놓는다
  if (prev === null && cur !== null) {
    openSaved = Array.prototype.map.call(groups, function (g) { return g.open; });
  }
  document.querySelectorAll('.case').forEach(function (el) {
    el.hidden = cur !== null && el.dataset.status !== cur;
  });
  groups.forEach(function (g, i) {
    g.open = cur === null ? !!(openSaved && openSaved[i]) : cur === 'PASS';
    g.hidden = cur !== null && cur !== 'PASS';
  });
  if (cur === null) openSaved = null;
  document.querySelectorAll('.area').forEach(function (a) {
    var vis = a.querySelectorAll('.case:not([hidden])').length;
    a.hidden = cur !== null && !vis;
    var c = a.querySelector('.areacnt');
    if (c) c.textContent = (cur === null ? '' : vis + '/') + c.dataset.total + '건';
  });
  document.querySelectorAll('#view-qa .info, #view-qa .todoline').forEach(function (d) {
    d.hidden = cur !== null;
  });
  document.querySelectorAll('.chip').forEach(function (b) {
    b.classList.toggle('on', b.dataset.filter === cur);
    b.setAttribute('aria-pressed', b.dataset.filter === cur ? 'true' : 'false');
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

// 링크로 간 케이스가 접힌 묶음 안에 있으면 펼친다
function openTarget() {
  var id = decodeURIComponent(location.hash.slice(1));
  var el = id && document.getElementById(id);
  if (!el) return;
  if (cur !== null) flt(cur);
  for (var p = el; p; p = p.parentElement) { if (p.tagName === 'DETAILS') p.open = true; }
  el.scrollIntoView({block: 'start'});
}
function openInfo(id) { var d = document.getElementById(id); if (d) d.open = true; }
window.addEventListener('hashchange', openTarget);

// 개발 전달용 텍스트 복사.
// file:// 에서는 navigator.clipboard 가 막히는 브라우저가 있어 예전 방식으로 한 번 더 시도한다.
function copyDev() {
  var txt = document.getElementById('devmd').textContent;
  var btn = document.getElementById('copybtn');
  function done(ok) {
    btn.textContent = ok ? '복사했습니다' : '복사 실패 - 웍스등록.txt 를 열어주세요';
    setTimeout(function () { btn.textContent = '복사'; }, 2200);
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
var lbFrom = null;
function zoom(btn) {
  var img = btn.querySelector('img');
  lbFrom = btn;
  document.getElementById('lbimg').src = img.src;
  document.getElementById('lbname').textContent = img.alt;
  lb.classList.remove('full');
  document.getElementById('lbfit').textContent = '원본 크기';
  lb.classList.add('on');
  lb.scrollTop = 0;
  document.body.style.overflow = 'hidden';
  document.getElementById('lbclose').focus();
}
function closeLb(e) {
  if (e) e.stopPropagation();
  if (!lb.classList.contains('on')) return;
  lb.classList.remove('on');
  document.body.style.overflow = '';
  if (lbFrom) lbFrom.focus();
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
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') closeLb();
  // 확대 보기 안에서 Tab 이 바깥으로 나가지 않게 한다
  if (e.key === 'Tab' && lb.classList.contains('on')) {
    var f = lb.querySelectorAll('button');
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
if (location.hash) openTarget();
"""

DOC = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA 리포트 - {project}</title>
<style>{css}</style></head><body><div class="wrap">

<header class="top">
  <div class="kicker">QA 자동 검수 리포트</div>
  <h1>{project}</h1>
  <div class="meta">{stamp} &nbsp;&middot;&nbsp; <code>{base}</code> &nbsp;&middot;&nbsp; {env} {dry}</div>
</header>

<nav class="tabs" role="tablist">
  <button data-view="qa" class="on" role="tab" aria-selected="true">기획팀 검토<span class="cnt">{n_all}</span></button>
  <button data-view="dev" role="tab" aria-selected="false">개발 전달용<span class="cnt">{n_dev}</span></button>
</nav>

<div id="view-qa">
  {alert}
  <div class="chipbar">
    <div class="chips">{chips}<span class="total">{total}</span></div>
    <div class="fltbar" id="fltbar"></div>
  </div>
  {todo}
  {blocks}
  {cases}
</div>

<div id="view-dev">{dev}</div>

<footer>
  <b>근거 자료</b>
  <ul>{sources}</ul>
</footer>
</div>

<div id="lb" role="dialog" aria-modal="true" aria-label="스크린샷 크게 보기">
  <div class="bar">
    <b id="lbname"></b>
    <button onclick="toggleFull(event)" id="lbfit">원본 크기</button>
    <button onclick="closeLb(event)" id="lbclose">닫기 (Esc)</button>
  </div>
  <div class="stage"><img id="lbimg" alt="" onclick="toggleFull(event)"></div>
</div>
<script>{js}</script>
</body></html>"""

CHIP_TONE = {"FAIL": " t-red", "ERROR": " t-org", "FLAKY": " t-org", "BLOCKED": " t-org",
             "SKIPPED": " t-org"}


def build_report(spec, results, warnings, out_dir, dry_run, spec_path=None,
                 prev=None, partial=False, run_meta=None):
    meta = spec["meta"]
    for i, r in enumerate(results, 1):
        r["_seq"] = i                 # 실행 순서. 상태순으로 정렬해도 앞뒤 연쇄를 읽을 수 있게
    counts = {k: sum(1 for r in results if r["status"] == k) for k in STATUS_META}
    by_id = {c["id"]: c for c in spec.get("cases", [])}

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = build_dev_md(spec, results, out_dir, run_meta)
    log_path = build_works_log(spec, results, out_dir, run_meta)
    aborted = _aborted(run_meta)
    dev_items, held_items = _dev_items(results), _held_items(results)
    n_dev_shown = 0 if aborted else len(dev_items)

    chips = "".join(
        '<button type="button" class="chip{t}" data-filter="{k}" aria-pressed="false"{dis}>'
        '<b>{n}</b><span>{l}</span></button>'.format(
            k=k, t=CHIP_TONE.get(k, ""), n=counts[k], l=STATUS_META[k][0],
            dis="" if counts[k] else " disabled")
        for k in CHIP_ORDER if counts[k] or k in ("FAIL", "PASS"))

    def li(r, extra=""):
        return '<li><a href="#case-{0}">{0}</a> {1}{2}</li>'.format(
            _esc(r["id"]), _esc(r["title"]), " · " + _esc(extra) if extra else "")

    cov_rows = coverage_rows(spec, results)

    # 불안정 · 점검 필요 · 승인 대기는 칩 · 할 일 · 케이스 카드에 이미 있다. 같은 목록 블록은 두지 않는다
    blocks = "".join([
        _warn_blocks(warnings),
        _coverage_html(cov_rows, partial),
        _trend_html(results, prev),
        _list_block("", "재실행으로 통과 {}건".format(sum(1 for r in results if r.get("flaky"))),
                    "1차 실행이 시간 초과 등으로 판정까지 못 갔다가 다시 돌려 통과했습니다.",
                    [li(r, r["flaky"][:120]) for r in results if r.get("flaky")]),
        _list_block("", "콘솔·네트워크 오류가 난 케이스 {}건".format(
                        sum(1 for r in results if r.get("signals"))),
                    "판정과 무관한 참고 정보입니다.",
                    [li(r, r["signals"][0] + (" 외 {}건".format(len(r["signals"]) - 1)
                                              if len(r["signals"]) > 1 else ""))
                     for r in results if r.get("signals")]),
    ])

    vp = meta.get("viewport") or {"width": 1600}
    doc = DOC.format(
        css=CSS, js=JS,
        project=_esc(meta["project"]),
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        base=_esc(meta["base_url"]),
        env="화면 너비 {}px".format(vp.get("width", 1600)),
        dry='<span class="drytag">읽기만 실행 (쓰기 케이스 건너뜀)</span>' if dry_run else "",
        n_all=len(results), n_dev=n_dev_shown,
        total=_total_text(run_meta, results),
        alert=_aborted_html(run_meta, results),
        chips=chips, todo=_todo_html(results, cov_rows, warnings, len(dev_items), aborted),
        blocks=blocks,
        cases=_cases_html(results, by_id, spec_path),
        dev=_dev_block(spec, results, held_items, md_path.name, log_path.name,
                       log_path.read_text(encoding="utf-8"), run_meta),
        sources="".join("<li>{}</li>".format(_esc(s)) for s in meta.get("sources", [])),
    )

    path = out_dir / "리포트.html"
    path.write_text(doc, encoding="utf-8")

    # 원본 결과도 남긴다 (재검수 · 지난 실행 비교용). 실행 조건을 같이 적어야
    # 부분 실행 · 중단된 실행이 비교 기준으로 잡히지 않는다. 쓰다 끊겨 깨진 파일이 남지 않게
    # 임시 파일에 다 쓴 뒤 바꿔 끼운다.
    clean = [{k: v for k, v in r.items() if k != "_seq"} for r in results]
    tmp = out_dir / "results.json.tmp"
    tmp.write_text(json.dumps({"meta": run_meta or {}, "results": clean}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(str(tmp), str(out_dir / "results.json"))

    return path


def _total_text(run_meta, results):
    info = (run_meta or {}).get("aborted_info")
    if info:
        return "전체 {}건 중 {}건 실행".format(info["total"], min(len(results), info["total"]))
    return "전체 {}건".format(len(results))
