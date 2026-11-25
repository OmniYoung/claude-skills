"""
안전장치 · 판정 회귀 테스트. 러너를 고친 뒤 커밋 전에 돌린다.

  py qa_auto/tests/regress/run_regress.py                 전체
  py qa_auto/tests/regress/run_regress.py --only dry,late   일부 시나리오만
  py qa_auto/tests/regress/run_regress.py --list            시나리오 이름
  py qa_auto/tests/regress/run_regress.py --keep            끝나도 임시 폴더를 남김

로컬 HTTP 서버를 빈 포트로 띄워 픽스처를 내보낸다. 쓰기 버튼은 이 서버에 쓰기 요청을 보내고
서버가 받은 횟수를 센다. "dry-run 에서 쓰기 0" 같은 기준을 화면이 아니라 서버 쪽에서 잰다.

스펙과 결과는 실행마다 새 임시 폴더에 쓴다. 플러그인 폴더에는 아무것도 쓰지 않고,
다른 실행과 포트 · 폴더가 겹치지 않는다. 브라우저는 한 번에 하나만 띄운다(시나리오를 차례로).
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
QA = HERE.parents[1]
RUNNER = QA / "qa_run.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(QA))
import make_specs  # noqa: E402
import qa_run  # noqa: E402  사유 문장 상수 (REASON_*)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

WRITES = []           # 서버가 받은 쓰기 ("POST saved", "GET toggle" ...)
WORK = {}             # tmp, specs, base


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE / "site"), **k)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _what(self):
        return parse_qs(urlparse(self.path).query).get("what", ["?"])[0]

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/slow/"):
            # 다음 페이지 응답을 1.5초 늦춘다. 앞 문서가 그동안 살아 있으며 타이머를 돌린다 (케이스 경계)
            time.sleep(1.5)
            self.path = self.path[len("/slow"):]
            return super().do_GET()
        if path == "/api/count":
            return self._json({"n": len(WRITES)})
        if path in ("/api/getwrite", "/api/wget"):
            # 레거시 GET 쓰기. 받으면 서버 데이터가 바뀐 것으로 센다
            WRITES.append("GET " + self._what())
            self.send_response(302)
            self.send_header("Location", "/bypass.html")
            self.end_headers()
            return
        return super().do_GET()

    def _write(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        path = urlparse(self.path).path
        if path == "/api/write":
            WRITES.append("{} {}".format(self.command, self._what()))
            return self._json({"ok": True})
        if path in ("/api/search", "/api/search2", "/api/goods/search"):
            return self._json({"n": 3})
        self.send_response(404)
        self.end_headers()

    do_POST = do_PUT = do_DELETE = _write

    def log_message(self, *a):
        pass


def run(spec, *flags):
    """러너 실행. (종료 코드, 결과 폴더, 실행 정보, {id: 결과}, 출력, 서버 쓰기 목록)."""
    del WRITES[:]
    out = WORK["tmp"] / "output"
    last = out / "_last_run.txt"
    if last.exists():
        last.unlink()
    p = subprocess.run([sys.executable, str(RUNNER), str(WORK["specs"] / (spec + ".json"))] + list(flags),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(WORK["tmp"]))
    folder = None
    if last.exists():
        folder = out / last.read_text(encoding="cp949").strip()
    meta, res = {}, {}
    if folder and (folder / "results.json").exists():
        data = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        meta = data.get("meta", {})
        res = {r["id"]: r for r in data["results"]}
    return p.returncode, folder, meta, res, p.stdout + p.stderr, list(WRITES)


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


def statuses(res):
    return {k: v["status"] for k, v in res.items()}


def expect_status(label, res, want):
    """기대한 케이스가 실제로 돌았는지 먼저 본다. 빈 결과로 '쓰기 0' 같은 검사가 통과하면 안 된다."""
    got = statuses(res)
    missing = [k for k in want if k not in got]
    if missing:
        check(label, False, "돌지 않은 케이스: {} (결과 {})".format(missing, sorted(got)))
        return False
    bad = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    check(label, not bad, "다름(실제, 기대): {}".format(bad) if bad else "")
    return True


def no_writes(label, ran, writes):
    check(label, ran and not writes, "실행 안 됨" if not ran else "서버 쓰기 {}".format(writes))


def has_reason(res, cid, template):
    """케이스 사유가 qa_run 의 사유 상수 문장을 담는지. 자리표시({...})를 뺀 고정 부분이 모두 있어야 한다.

    문장을 qa_run 에서 고치면 기대값도 같이 따라간다 (문자열을 여기에 따로 적지 않는다).
    """
    reason = res.get(cid, {}).get("reason", "")
    parts = [p for p in re.split(r"\{[^}]*\}", template) if len(p.strip()) >= 2]
    return bool(parts) and all(p in reason for p in parts)


def reason_of(res, cid):
    return res.get(cid, {}).get("reason", "")


def text(folder, name):
    p = folder / name if folder else None
    return p.read_text(encoding="utf-8") if p and p.exists() else ""


# ---------------------------------------------------------------- 시나리오

SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__.replace("sc_", "")] = fn
    return fn


@scenario
def sc_dry():
    code, f, meta, res, out, w = run("dry", "--dry-run")
    ran = expect_status("dry: 쓰기 경로 차단", res, {
        "S1": "BLOCKED", "S2": "BLOCKED", "S3": "ERROR", "S4": "ERROR", "S6": "BLOCKED",
        "S7": "ERROR", "S8": "BLOCKED", "S8b": "PASS", "S9": "PASS", "S10": "ERROR",
        "S11": "ERROR", "S12": "PASS"})
    no_writes("dry: 서버 쓰기 0", ran, w)
    check("dry: 개발 전달 0건", "미반영 오류 0건" in text(f, "웍스등록.txt"), text(f, "웍스등록.txt")[:80])


@scenario
def sc_live():
    code, f, meta, res, out, w = run("live")
    ran = expect_status("live: 선언 없는 쓰기 차단", res, {"N1": "BLOCKED", "N2": "BLOCKED", "N3": "PASS"})
    no_writes("live: 서버 쓰기 0", ran, w)
    check("live: N1 사유 '선언 누락'", has_reason(res, "N1", qa_run.REASON_UNDECLARED), reason_of(res, "N1"))


@scenario
def sc_block():
    code, f, meta, res, out, w = run("block")
    ran = expect_status("block: 화면 글자로 blocklist", res, {"B1": "BLOCKED", "B2": "BLOCKED"})
    no_writes("block: 서버 쓰기 0", ran, w)


@scenario
def sc_approved():
    code, f, meta, res, out, w = run("approved")
    ran = expect_status("approved: 승인된 쓰기", res, {"W1": "PASS", "W2": "ERROR", "W3": "PASS"})
    check("approved: 서버 쓰기 정확히 2회 (W1 저장 · W3 삭제, 재실행 없음)",
          ran and sorted(w) == ["POST deleted", "POST saved"], "쓰기 {}".format(w))


@scenario
def sc_allowpost():
    code, f, meta, res, out, w = run("allowpost_off", "--dry-run")
    expect_status("allow_post 없음: POST 조회 차단", res, {"A1": "ERROR"})
    check("allow_post 없음: 사유에 allow_post 안내", has_reason(res, "A1", qa_run.REASON_REQUEST_BLOCKED),
          reason_of(res, "A1"))
    code, f, meta, res, out, w = run("allowpost_on", "--dry-run")
    expect_status("allow_post 있음: POST 조회 통과", res, {"A2": "PASS"})
    code, f, meta, res, out, w = run("ap_method", "--dry-run", "--no-retry")
    ran = expect_status("allow_post: PUT · DELETE · 쿼리 우회는 차단", res,
                        {"R1a": "ERROR", "R1b": "ERROR", "R1c": "ERROR"})
    no_writes("allow_post: 서버 쓰기 0", ran, w)


@scenario
def sc_confirm():
    code, f, meta, res, out, w = run("confirm", "--dry-run")
    ran = expect_status("confirm: 확인창 취소", res, {"C1": "PASS", "C2": "ERROR"})
    no_writes("confirm: 서버 쓰기 0", ran, w)


@scenario
def sc_getw():
    code, f, meta, res, out, w = run("getw", "--dry-run", "--no-retry")
    ran = expect_status("getw: blocklist 주소 링크는 차단", res, {"G1": "PASS", "G2": "ERROR", "G3": "PASS"})
    check("getw: blocklist 에 적은 GET 쓰기 0", ran and not any("withdraw" in x for x in w), str(w))
    check("getw: 막지 못한 GET 쓰기는 의심 신호", all(res.get(c, {}).get("get_suspects") for c in ("G1", "G3")),
          str({c: res.get(c, {}).get("get_suspects") for c in ("G1", "G3")}))
    check("getw: 리포트 할 일에 'GET 쓰기 의심'", "GET 쓰기 의심" in text(f, "리포트.html"))


@scenario
def sc_late():
    code, f, meta, res, out, w = run("late", "--dry-run", "--no-retry")
    ran = expect_status("late: 막은 뒤의 실패는 판정 불가", res, {"L1": "ERROR", "L2": "ERROR", "X1": "ERROR"})
    no_writes("late: 서버 쓰기 0", ran, w)
    check("late: 개발 전달 0건", "미반영 오류 0건" in text(f, "웍스등록.txt"), text(f, "웍스등록.txt")[:120])
    check("late: 사유에 '화면 흐름이 달라짐'", has_reason(res, "L2", qa_run.REASON_INTERFERED), reason_of(res, "L2"))


@scenario
def sc_lateok():
    code, f, meta, res, out, w = run("lateok", "--no-retry")
    ran = expect_status("lateok: 늦게 뜬 확인창도 승인 쓰기 스텝 구간이면 수락", res, {"D1": "PASS"})
    check("lateok: 서버 쓰기 1회", ran and w == ["POST deleted"], str(w))


@scenario
def sc_popup():
    code, f, meta, res, out, w = run("popup", "--no-retry")
    ran = expect_status("popup: 다른 창 · 앞 케이스 요청 차단", res, {
        "PP1": "PASS", "PP2": "PASS", "PP3": "PASS", "K1": "PASS", "K2": "PASS", "K3": "BLOCKED"})
    check("popup: 서버 쓰기는 승인 케이스의 저장 2회뿐", ran and w == ["POST saved", "POST saved"], str(w))


@scenario
def sc_race():
    code, f, meta, res, out, w = run("race", "--no-retry")
    ran = expect_status("race: 바뀐 글자로 판정 · 내장 blocklist '발송'", res, {"R1": "BLOCKED"})
    no_writes("race: 서버 쓰기 0", ran, w)
    code, f, meta, res, out, w = run("race_off", "--no-retry")
    ran = expect_status("race: blocklist_off 로 풀면 실행", res, {"R2": "PASS"})
    check("race: 풀었을 때 서버 쓰기 1회", ran and w == ["POST send"], str(w))


@scenario
def sc_absence():
    code, f, meta, res, out, w = run("absence", "--dry-run", "--no-retry")
    expect_status("absence: 늦은 문구는 실패 · 늦게 빠지는 행은 통과", res,
                  {"R3a": "FAIL", "R3b": "FAIL", "R4": "PASS"})
    expect_status("absence: not_contains 요소 없음 · 늦게 생김 · anchor 사라짐 · 요소 떨어짐", res,
                  {"R3c": "PASS", "R3d": "ERROR", "R3e": "FAIL", "R3f": "ERROR", "R3g": "ERROR"})
    check("absence: anchor 없는 not_contains 요소 없음은 부재 판정 불가",
          has_reason(res, "R3d", qa_run.REASON_ABSENCE_UNKNOWN), reason_of(res, "R3d"))
    check("absence: 관찰 중 anchor 사라짐 사유", "기준 요소(anchor)가 사라짐" in reason_of(res, "R3f"),
          reason_of(res, "R3f"))
    d = res.get("R3g", {}).get("duration", 99)
    check("absence: 요소가 떨어져도 5초 안에 끝남", d < 5, "{}초 / {}".format(d, reason_of(res, "R3g")))


@scenario
def sc_dlg():
    code, f, meta, res, out, w = run("dlg", "--dry-run", "--no-retry")
    ran = expect_status("dlg: 확인창 취소 뒤 판정 · precondition 개입", res,
                        {"DLG1": "ERROR", "DLG2": "FAIL", "DLG3": "FAIL", "PRE1": "ERROR"})
    check("dlg: DLG1 사유 '판정 불가 · 확인창 취소'",
          has_reason(res, "DLG1", qa_run.REASON_INTERFERED) and qa_run.INTERFERE_DIALOG in reason_of(res, "DLG1"),
          reason_of(res, "DLG1"))
    check("dlg: PRE1 은 조건 미충족이 아니라 조건 확인 불가(안전망 개입)",
          has_reason(res, "PRE1", qa_run.REASON_PRE_INTERFERED), reason_of(res, "PRE1"))
    log = text(f, "웍스등록.txt")
    check("dlg: 개발 전달 0건 · 웍스 항목 0", ran and meta.get("n_dev") == 0 and "1. " not in log, log[:200])
    no_writes("dlg: 서버 쓰기 0", ran, w)


@scenario
def sc_allerr():
    code, f, meta, res, out, w = run("allerr", "--dry-run", "--no-retry")
    ran = expect_status("allerr: 전부 점검 필요", res, {"Z1": "ERROR", "Z2": "ERROR", "Z3": "ERROR"})
    log, md, rep = text(f, "웍스등록.txt"), text(f, "개발전달.md"), text(f, "리포트.html")
    first2 = log.splitlines()[:2]
    check("allerr: 웍스 첫 두 줄 '판정한 0건 중' · '판정하지 못한 3건'",
          ran and len(first2) == 2 and first2[0].endswith("rg_allerr 검수 · 판정한 0건 중 기획 미반영 오류 0건")
          and first2[1] == "판정하지 못한 3건은 기획팀 확인 뒤 다시 전달합니다.", str(first2))
    dev_tab = rep.split('id="view-dev"', 1)[-1]
    words = [x for x in ("미반영 오류 없음", "어긋난 항목", "전달할 내용 없음") for body in (log, md, dev_tab)
             if x in body]
    check("allerr: 웍스 · md · 개발 탭에 '오류 없음' 류 0", ran and not words, str(words))
    check("allerr: 개발 탭 '전달 보류 · 판정 못 한 건 3건'", "전달 보류 · 판정 못 한 건 3건" in dev_tab)


@scenario
def sc_popsel():
    code, f, meta, res, out, w = run("popsel", "--dry-run", "--no-retry")
    expect_status("popsel: 같은 세션 팝업 요청 차단 뒤 판정은 점검 필요", res, {"SEL1": "ERROR"})
    check("popsel: 사유에 '다른 창 요청 차단'", qa_run.INTERFERE_POPUP in reason_of(res, "SEL1")
          and has_reason(res, "SEL1", qa_run.REASON_INTERFERED), reason_of(res, "SEL1"))
    check("popsel: 개발 전달 0건", meta.get("n_dev") == 0, str(meta.get("n_dev")))


@scenario
def sc_boundary():
    code, f, meta, res, out, w = run("boundary", "--dry-run", "--no-retry")
    expect_status("boundary: 앞 문서의 늦은 요청 · 알럿이 다음 케이스에 섞이지 않음", res,
                  {"BK1": "PASS", "BK2": "PASS", "BA1": "PASS", "BA2": "PASS"})
    check("boundary: 서버 쓰기 0", not w, str(w))


@scenario
def sc_trace():
    code, f, meta, res, out, w = run("trace")
    md, log, rep = text(f, "개발전달.md"), text(f, "웍스등록.txt"), text(f, "리포트.html")
    dev_tab = rep.split('id="view-dev"', 1)[-1]
    leak = [n for n, body in (("개발전달.md", md), ("웍스 로그", log), ("개발 탭", dev_tab))
            if "traces/" in body or "playwright.dev" in body or "동작 기록" in body]
    check("trace: 결과 있음", bool(res), out[-200:])
    check("trace: 개발전달.md · 웍스 로그 · 개발 탭에 동작 기록 0", res and not leak, str(leak))
    check("trace: 기획팀 탭에는 동작 기록 링크", "traces/T1.zip" in rep)
    check("trace: 웍스 로그 줄 끝 케이스 ID · {actual} 치환 · 기타 묶음",
          " · T1" in log and "'리뷰 페이지'" in log and "- 기타" in log, log)
    check("trace: md 표 머리줄", "| 항목 | 내용 |" in md, md[:200])
    tz = f / "traces" / "T1.zip" if f else None
    if tz and tz.exists():
        with zipfile.ZipFile(tz) as z:
            has_secret = any(b"SECRET123" in z.read(n) for n in z.namelist())
        subprocess.run([sys.executable, str(RUNNER), "--strip-trace", str(tz)], capture_output=True)
        sz = tz.with_name("T1_전달용.zip")
        left = True
        if sz.exists():
            with zipfile.ZipFile(sz) as z:
                left = any(b"SECRET123" in z.read(n) for n in z.namelist())
        check("trace: --strip-trace 사본에 세션 쿠키 없음", has_secret and not left,
              "원본에 쿠키 {} · 사본에 남음 {}".format(has_secret, left))
    else:
        check("trace: trace 파일 생성", False)


@scenario
def sc_gate():
    code, f, meta, res, out, w = run("gate", "--dry-run")
    g1 = statuses(res)
    code, f, meta, res, out, w = run("gate")
    check("gate: 모드 1 · 전체 실행 판정 일치 (둘 다 차단)",
          g1 == statuses(res) == {"TC-101": "BLOCKED", "TC-102": "BLOCKED"},
          "{} / {}".format(g1, statuses(res)))
    check("gate: 개발 전달 0건", "미반영 오류 0건" in text(f, "웍스등록.txt"))


@scenario
def sc_sess():
    code, f, meta, res, out, w = run("sess")
    check("sess: 종료 코드 3", code == 3, str(code))
    check("sess: 중단돼도 리포트 · results.json(aborted)", f is not None and (f / "리포트.html").exists()
          and meta.get("aborted") is True)
    rep, log, md = text(f, "리포트.html"), text(f, "웍스등록.txt"), text(f, "개발전달.md")
    check("sess: '1번째 케이스' 중단 표시", "1번째 케이스" in rep)
    check("sess: 웍스 · md · 개발 탭이 '전달 보류 (2건 중 1건 실행)'",
          all("검수 중단 · 전달 보류 (2건 중 1건 실행)" in x for x in (log, md, rep)), log[:120])
    check("sess: 중단된 실행은 복사 버튼 없음", 'id="copybtn"' not in rep)
    check("sess: 칩 옆 총계 '전체 2건 중 1건 실행'", "전체 2건 중 1건 실행" in rep)


@scenario
def sc_probe():
    code, f, meta, res, out, w = run("probe", "--dry-run")
    expect_status("probe: 선언 없는 쓰기 · 오타 부재 판정 · 화면 글자", res,
                  {"P-1": "BLOCKED", "P-2": "ERROR", "P-3": "PASS"})
    code, f, meta, res, out, w = run("probe_kind")
    check("probe_kind: 분류값 오타는 실행 전 멈춤", code == 1 and "kind 'improvement'" in out
          and not res, out[-200:])
    code, f, meta, res, out, w = run("probe2")
    expect_status("probe2: 늦은 렌더 · 숨은 문구 · 늦은 알럿 · 세션 만료", res,
                  {"Q-1": "FAIL", "Q-1b": "FAIL", "Q-2": "PASS", "Q-3": "PASS", "Q-5": "ERROR"})
    q5 = res.get("Q-5", {})
    check("probe2: 세션 만료 케이스도 cleanup 실행", any(s["phase"] == "cleanup" and s["status"] == "OK"
                                                    for s in q5.get("steps", [])))
    check("probe2: 중단 뒤 케이스 미실행 · 잔여 점검 생략", res and "Q-6" not in res and "QA-잔여" not in res)
    check("probe2: 종료 코드 3", code == 3, str(code))


@scenario
def sc_flaky():
    code, f, meta, res, out, w = run("flaky")
    expect_status("flaky: 1차 실패 · 재실행 통과는 불안정, 재실행 점검 필요는 점검 필요", res,
                  {"F1": "FLAKY", "F2": "ERROR"})
    check("flaky: 1차 실패 · 재실행 점검 필요 사유가 '1차 실패:' 로 시작",
          reason_of(res, "F2").startswith("1차 실패: ") and has_reason(res, "F2", qa_run.REASON_RETRY_ERROR),
          reason_of(res, "F2"))
    rep = text(f, "리포트.html")
    check("flaky: 커버리지에서 통과로 안 침", "불안정 1" in rep)
    check("flaky: 1차 스크린샷 _try1 보존", any("_try1" in s for s in res.get("F1", {}).get("shots", [])))
    check("flaky: 개발 전달 제외", "미반영 오류 0건" in text(f, "웍스등록.txt"))


@scenario
def sc_trend():
    run("trend")
    run("trend", "--case", "P2")
    code, f, meta, res, out, w = run("trend")
    check("trend: 부분 실행 뒤 전체 실행에서 P1 '계속 실패'", res.get("P1", {}).get("trend") == "still",
          str(res.get("P1", {}).get("trend")))
    code, f, meta, res, out, w = run("trend", "--rerun-failed", "latest")
    check("trend: --rerun-failed latest 가 P1 을 잡음", set(res) == {"P1"}, str(set(res)))
    # FAIL -> ERROR 는 '판정 불가로 바뀜'. 임시 폴더의 스펙만 고친다
    sp = WORK["specs"] / "trend.json"
    spec = json.loads(sp.read_text(encoding="utf-8"))
    spec["cases"][0]["steps"][1] = {"action": "expect_text", "selector": "#nope", "contains": "x", "timeout": 800}
    sp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    code, f, meta, res, out, w = run("trend", "--no-retry")
    check("trend: 실패가 점검 필요로 바뀌면 '판정 불가로 바뀜'", res.get("P1", {}).get("trend") == "unjudged",
          str(res.get("P1", {}).get("trend")))
    code, f, meta, res, out, w = run("trend", "--no-retry")
    check("trend: 점검 필요가 이어지면 '계속 점검 필요'", res.get("P1", {}).get("trend") == "still_err",
          str(res.get("P1", {}).get("trend")))


@scenario
def sc_shared():
    run("m1", "--no-retry")
    run("m2")
    code, f, meta, res, out, w = run("m1", "--rerun-failed", "latest", "--no-retry")
    check("shared: 같은 project 의 다른 스펙 실행이 재검수 기준을 가로채지 않음", set(res) == {"M1"},
          str(set(res)) + " " + out[-200:])
    check("shared: 비교 표시 '계속 실패'", res.get("M1", {}).get("trend") == "still",
          str(res.get("M1", {}).get("trend")))


@scenario
def sc_smoke():
    del WRITES[:]
    out = WORK["tmp"] / "output"
    p = subprocess.run([sys.executable, str(RUNNER), str(QA / "_smoke.json")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(WORK["tmp"]))
    last = out / "_last_run.txt"
    res = {}
    if last.exists():
        folder = out / last.read_text(encoding="cp949").strip()
        if (folder / "results.json").exists():
            data = json.loads((folder / "results.json").read_text(encoding="utf-8"))
            res = {r["id"]: r for r in data["results"]}
    expect_status("smoke: 기존 결과 유지", res, {
        "TC-001": "PASS", "TC-002": "FAIL", "TC-003": "SKIPPED", "TC-004": "PASS", "TC-005": "BLOCKED",
        "TC-006": "BLOCKED", "TC-007": "PASS", "TC-008": "ERROR", "TC-009": "NA", "TC-010": "FAIL",
        "TC-011": "PASS", "TC-012": "FAIL", "TC-013": "PASS", "QA-잔여": "PASS"})
    check("smoke: 플러그인 폴더에 산출물 없음", not (QA / "output").exists() and not (QA.parent / "output").exists())


@scenario
def sc_bat():
    """bat 의 러너 버전 확인 블록만 떼어 가짜 러너로 돌린다 (브라우저 없음)."""
    import qa_run
    block = qa_run.FIND_RUNNER.split("rem Old runners", 1)[1].split("rem Use the py launcher", 1)[0]
    block = ("rem Old runners" + block).format(need=qa_run.RUNNER_VERSION).replace("{{", "{").replace("}}", "}")
    results = {}
    for ver in (qa_run.RUNNER_VERSION - 1, qa_run.RUNNER_VERSION):
        fake = WORK["tmp"] / "fake_runner_{}.py".format(ver)
        fake.write_text("RUNNER_VERSION = {}\n".format(ver), encoding="ascii")
        bat = WORK["tmp"] / "ver_{}.bat".format(ver)
        bat.write_text("@echo off\r\nsetlocal enabledelayedexpansion\r\nset \"RUNNER={}\"\r\n{}\r\necho VERSION_OK\r\n".format(
            fake, block.replace("\n", "\r\n")), encoding="cp949", errors="replace")
        p = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, stdin=subprocess.DEVNULL)
        results[ver] = (p.returncode, b"VERSION_OK" in p.stdout)
    old, new = results[qa_run.RUNNER_VERSION - 1], results[qa_run.RUNNER_VERSION]
    check("bat: 구버전 러너면 멈춤", old == (1, False), str(old))
    check("bat: 현재 버전이면 통과", new == (0, True), str(new))
    check("bat: 종료 코드 3 에서만 로그인 안내", '"%RESULT%"=="3"' in qa_run.BAT_TEMPLATE
          and '"%RESULT%"=="2"' not in qa_run.BAT_TEMPLATE)


ORDER = ["dry", "live", "block", "approved", "allowpost", "confirm", "getw", "late", "lateok", "popup",
         "race", "absence", "dlg", "allerr", "popsel", "boundary", "trace", "gate", "sess", "probe", "flaky", "trend",
         "shared", "smoke", "bat"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="쉼표로 시나리오 이름")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않음")
    args = ap.parse_args()
    assert set(ORDER) == set(SCENARIOS), set(SCENARIOS) ^ set(ORDER)
    if args.list:
        print(" ".join(ORDER))
        return 0
    names = ORDER
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in names if n not in SCENARIOS]
        if unknown:
            print("없는 시나리오: {} (가능: {})".format(unknown, " ".join(ORDER)))
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="qa_regress_"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)       # 빈 포트
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    WORK.update(tmp=tmp, specs=tmp / "specs", base=base)
    make_specs.write(WORK["specs"], base)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for n in names:
            print("  ... {}".format(n), flush=True)
            SCENARIOS[n]()
    finally:
        srv.shutdown()
        srv.server_close()
        if args.keep:
            print("임시 폴더: {}".format(tmp))
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    w = max(len(n) for n, _, _ in CHECKS)
    fails = 0
    for name, ok, detail in CHECKS:
        print("  [{}] {}{}".format("통과" if ok else "실패", name.ljust(w),
                                   "  " + detail if detail and not ok else ""))
        fails += not ok
    print("\n회귀 {}건 중 실패 {}건 (시나리오 {}개)".format(len(CHECKS), fails, len(names)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
