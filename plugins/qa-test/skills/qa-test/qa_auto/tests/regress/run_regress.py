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
        if path == "/js/sendbird.min.js":
            # 이름에 blocklist 'send' 가 든 스크립트. 막히면 SB_OK 가 안 생긴다
            body = b"window.SB_OK = 1;"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        if path == "/api/auth/refresh":
            # 로그인 유지 POST. 안전망이 막으므로 여기 오면 안 된다
            WRITES.append("POST refresh")
            return self._json({"ok": True})
        if path in ("/api/log", "/api/log2"):
            # 로그성 POST. ignore_requests 여도 막기는 하므로 여기 오면 안 된다
            WRITES.append("POST " + path[5:])
            return self._json({"ok": True})
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
        "S11": "ERROR", "S12": "PASS", "S13": "ERROR"})
    no_writes("dry: 서버 쓰기 0", ran, w)
    check("dry: 개발 전달 0건", "미반영 오류 0건" in text(f, "웍스등록.txt"), text(f, "웍스등록.txt")[:80])
    check("dry: S7 write:false 저장 클릭 사유", has_reason(res, "S7", qa_run.REASON_WRITE_FALSE_SENT),
          reason_of(res, "S7"))
    check("dry: S13 케이스 anchor body 는 anchor 없음 (요소를 찾지 못함)",
          "요소를 찾지 못함" in reason_of(res, "S13"), reason_of(res, "S13"))
    n = {k: sum(1 for r in res.values() if r["status"] == k) for k in ("BLOCKED", "SKIPPED")}
    check("dry: 콘솔 결과 줄에 차단 · 승인 대기 건수 (R3-28)",
          " / 차단 {} / 승인 대기 {} / 전체 {}".format(n["BLOCKED"], n["SKIPPED"], len(res)) in out, out[-300:])


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
    # A1 은 write:false 로 둔 조회 버튼이라 write:false 전용 사유가 난다. 사유는 조회 · 검증 누락 둘 다 열어 두고
    # 설정 키(allow_post)는 적지 않는다 (분류는 SKILL 2단계 절차)
    check("allow_post 없음: 사유가 write:false 전용 · '조회 요청인지' · 'allow_post' 없음",
          has_reason(res, "A1", qa_run.REASON_WRITE_FALSE_SENT) and "조회 요청인지" in reason_of(res, "A1")
          and "allow_post" not in reason_of(res, "A1"), reason_of(res, "A1"))
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
    check("getw: G2 사유에 blocklist 'withdraw'", "blocklist 'withdraw'" in reason_of(res, "G2")
          and has_reason(res, "G2", qa_run.REASON_REQUEST_BLOCKED), reason_of(res, "G2"))
    check("getw: 막지 못한 GET 쓰기는 의심 신호", all(res.get(c, {}).get("get_suspects") for c in ("G1", "G3")),
          str({c: res.get(c, {}).get("get_suspects") for c in ("G1", "G3")}))
    check("getw: 리포트 할 일에 'GET 쓰기 의심'", "GET 쓰기 의심" in text(f, "리포트.html"))
    # R3-28 · R3-34: 콘솔 끝 두 줄, 의심 케이스마다 머리줄 태그 · #warn 목록 한 줄
    sus_ids = [c for c, r in res.items() if r.get("get_suspects")]
    check("getw: 콘솔에 결과 줄(차단 · 승인 대기) · GET 쓰기 의심 줄",
          "[*] 결과: 통과 " in out and " / 차단 " in out and " / 승인 대기 " in out
          and qa_run.CONSOLE_GET_SUSPECT.format(len(sus_ids)) in out, out[-400:])
    rep = text(f, "리포트.html")
    warn = rep.split('id="warn"', 1)[-1].split("</details>", 1)[0] if 'id="warn"' in rep else ""
    heads = {c: rep.split('id="case-{}"'.format(c), 1)[-1].split('class="cbody"', 1)[0] for c in sus_ids}
    check("getw: 의심 케이스 머리줄 태그 · #warn 목록 케이스마다 한 줄",
          bool(sus_ids) and all("GET 쓰기 의심</span>" in h for h in heads.values())
          and all('href="#case-{}"'.format(c) in warn for c in sus_ids)
          and warn.count("<li>GET 쓰기 의심 · ") == len(sus_ids), "{} / {}".format(sus_ids, warn[:300]))
    # 신호 범위 (R3-09): 선택 변경 · 1초 뒤 · 다른 호스트 GET 도 의심 목록에, 무시 목록 경로는 없음
    expect_status("getw: 선택 · 지연 · 다른 호스트 GET 케이스", res, {"G4": "PASS", "G5": "PASS", "G6": "PASS",
                                                              "G7": "PASS"})
    sus = {c: " ".join(res.get(c, {}).get("get_suspects") or []) for c in ("G4", "G5", "G6", "G7")}
    check("getw: select onchange GET(status_update) 의심", "status_update" in sus["G4"], sus["G4"])
    check("getw: 클릭 1초 뒤 GET(delay_toggle) 의심", "delay_toggle" in sus["G5"], sus["G5"])
    check("getw: 다른 호스트 GET(remote_save) 의심", "remote_save" in sus["G6"] and "localhost" in sus["G6"],
          sus["G6"])
    check("getw: 무시 목록 경로 GET 은 신호 없음", not sus["G7"], sus["G7"])


@scenario
def sc_late():
    code, f, meta, res, out, w = run("late", "--dry-run", "--no-retry")
    ran = expect_status("late: 막은 뒤의 실패는 판정 불가", res, {"L1": "ERROR", "L2": "ERROR", "X1": "ERROR"})
    no_writes("late: 서버 쓰기 0", ran, w)
    check("late: 개발 전달 0건", "미반영 오류 0건" in text(f, "웍스등록.txt"), text(f, "웍스등록.txt")[:120])
    check("late: 사유에 '판정 불가 - 안전망 개입'", has_reason(res, "L2", qa_run.REASON_INTERFERED), reason_of(res, "L2"))


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
def sc_bypass():
    code, f, meta, res, out, w = run("bypass", "--no-retry")
    ran = expect_status("bypass: 아이콘 셀렉터 · Enter · onclick div 안 아이콘은 내장 blocklist 로 차단", res,
                        {"BY1": "BLOCKED", "BY2": "BLOCKED", "BY3": "BLOCKED", "BY0": "PASS"})
    for cid in ("BY1", "BY2", "BY3"):
        check("bypass: {} 사유에 text*=발송".format(cid),
              has_reason(res, cid, qa_run.REASON_RUNTIME_BLOCKLIST) and "text*=발송" in reason_of(res, cid),
              reason_of(res, cid))
    check("bypass: 서버 POST send_* 0", ran and not any("send" in x for x in w), str(w))
    code, f, meta, res, out, w = run("bypass_off", "--no-retry")
    ran = expect_status("bypass: blocklist_off 로 풀면 세 경로 모두 실행", res,
                        {"BY1": "PASS", "BY2": "PASS", "BY3": "PASS", "BY0": "PASS"})
    check("bypass: 풀었을 때 서버 쓰기 경로마다 1회",
          ran and sorted(w) == ["POST send_div", "POST send_enter", "POST send_icon"], str(w))


@scenario
def sc_wfalse():
    code, f, meta, res, out, w = run("wfalse", "--dry-run", "--no-retry")
    ran = expect_status("wfalse: write:false 빈 폼 제출이 쓰기 요청을 보내면 점검 필요", res, {"WF1": "ERROR"})
    rs = reason_of(res, "WF1")
    check("wfalse: 사유에 'write:false' · '입력 검증 누락' · 'allow_post' 없음",
          has_reason(res, "WF1", qa_run.REASON_WRITE_FALSE_SENT) and "write:false" in rs and "입력 검증 누락" in rs
          and "allow_post" not in rs, rs)
    check("wfalse: 사유의 주소는 경로부터 (검수 주소 앞부분 뺌)", "POST /api/write?what=apply" in rs, rs)
    no_writes("wfalse: 서버 쓰기 0", ran, w)
    rep = text(f, "리포트.html") + text(f, "개발전달.md") + text(f, "웍스등록.txt")
    check("wfalse: 리포트 전체에 'meta.allow_post' 0", f is not None and "meta.allow_post" not in rep)


@scenario
def sc_ignore():
    code, f, meta, res, out, w = run("ignore", "--dry-run", "--no-retry")
    ran = expect_status("ignore: ignore_requests 경로 · 호스트 항목은 막되 통과, 다른 호스트 같은 경로는 점검 필요",
                        res, {"IG1": "PASS", "IG2": "PASS", "IG3": "ERROR"})
    check("ignore: IG3 사유는 분류 안내 · 다른 호스트는 주소 전체",
          has_reason(res, "IG3", qa_run.REASON_REQUEST_BLOCKED) and "POST http://localhost:" in reason_of(res, "IG3"),
          reason_of(res, "IG3"))
    sig = " ".join(res.get("IG1", {}).get("signals") or [])
    check("ignore: IG1 은 막았다는 신호만 남김", "판정 무관" in sig, sig)
    no_writes("ignore: 무시한 요청도 서버에 안 감 (쓰기 0)", ran, w)


@scenario
def sc_blres():
    code, f, meta, res, out, w = run("blres", "--dry-run", "--no-retry")
    ran = expect_status("blres: blocklist 'send' 는 스크립트에 안 걸림 · 'del' GET 링크는 막힘", res,
                        {"BL1": "PASS", "BL2": "ERROR"})
    check("blres: BL2 사유에 blocklist 'del'", "blocklist 'del'" in reason_of(res, "BL2"), reason_of(res, "BL2"))
    check("blres: del 링크 GET 서버 0", ran and not any("del_item" in x for x in w), str(w))


@scenario
def sc_explore():
    """탐색 헬퍼 guarded_page 를 이 프로세스에서 연다 (브라우저 1개, 러너 실행 없음)."""
    del WRITES[:]
    spec = WORK["specs"] / "explore.json"
    marks = {}
    try:
        with qa_run.guarded_page(spec, headed=False) as page:
            page.goto(WORK["base"] + "/b2.html", wait_until="domcontentloaded")
            for sel in ("#xsave", "#xdel", "#xsendget"):
                page.evaluate("() => { document.getElementById('marker').textContent = 'none'; }")
                page.click(sel)
                page.wait_for_timeout(600)
                marks[sel] = page.inner_text("#marker")
    except Exception as e:
        check("explore: 헬퍼로 페이지 열기", False, str(e)[:200])
        return
    check("explore: 저장 POST 는 막힘", marks.get("#xsave") == "blocked", str(marks))
    check("explore: 삭제 확인창은 취소됨", marks.get("#xdel") == "cancelled", str(marks))
    check("explore: 내장 blocklist 글자(발송) 버튼 클릭은 막힘", marks.get("#xsendget") == "none", str(marks))
    check("explore: 서버 쓰기 0 (GET 발송 포함)", not WRITES, str(WRITES))
    out = WORK["tmp"] / "output"
    check("explore: 결과 폴더를 만들지 않음", not out.exists() or not any(out.glob("rg_explore_*")))


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
    check("sess: 개발 탭 숫자 '보류' · 할 일 첫 항목 '다시 로그인 후 실행' (R3-33)",
          '개발 전달용<span class="cnt">보류</span>' in rep
          and '<b>할 일</b><a class="todo" href="#abort">다시 로그인 후 실행</a>' in rep)


@scenario
def sc_sessblk():
    """앞 케이스에서 막은 로그인 유지 POST → 다음 케이스 로그인 화면 → 중단 사유 · 알림 · 콘솔에 그 경로 (R3-14)."""
    code, f, meta, res, out, w = run("sessblk", "--no-retry")
    path = "POST /api/auth/refresh"
    check("sessblk: 종료 코드 3 · SB1 은 막은 요청으로 점검 필요", code == 3 and statuses(res) == {
        "SB1": "ERROR", "SB2": "ERROR"}, "{} / {}".format(code, statuses(res)))
    rs = reason_of(res, "SB2")
    check("sessblk: 중단 사유에 직전에 막은 요청 경로 · 다시 로그인해도 같은 결과",
          has_reason(res, "SB2", qa_run.REASON_SESSION) and path in rs
          and has_reason(res, "SB2", qa_run.REASON_SESSION_BLOCKED), rs)
    info = meta.get("aborted_info") or {}
    check("sessblk: aborted_info 에 최근 막은 요청", info.get("recent_blocked") == [path], str(info))
    rep = text(f, "리포트.html")
    alert = rep.split('<div class="alert"', 1)[-1].split("</div>", 1)[0] if '<div class="alert"' in rep else ""
    check("sessblk: 리포트 중단 알림에 그 경로 · 안내", path in alert and "다시 로그인해도 같은 결과가 납니다" in alert,
          alert[:300])
    check("sessblk: 콘솔(bat 화면) 끝 안내에 그 경로 · 한 번만",
          out.count(qa_run.REASON_SESSION_BLOCKED.format(path)) == 1, out[-400:])
    no_writes("sessblk: 서버 쓰기 0 (갱신 POST 도 막힘)", bool(res), w)


@scenario
def sc_retrypop():
    """재실행 전에 1차가 연 팝업을 닫고 지금 페이지를 다시 고른다 (R3-22, D-04).

    1차가 연 팝업은 부모 창이 바뀌면(재실행 시작) POST 를 보낸다. 남아 있으면 재실행 판정이 '다른 창 요청 차단'
    으로 판정 불가가 된다. 닫으면 1차 · 재실행 모두 실패(결함)로 남는다.
    (러너 페이지에서 부른 window.close() 는 Chromium 이 무시한다. 스크립트가 연 창이 아니고 이동 기록이 2개 이상)
    """
    code, f, meta, res, out, w = run("retrypop", "--dry-run")
    r = res.get("WC1", {})
    check("retrypop: 1차 실패 · 재실행 실패 = 실패 (남은 팝업이 재실행을 흐리지 않음)",
          r.get("status") == "FAIL" and r.get("retried") and (r.get("try1") or {}).get("status") == "FAIL",
          "{} / {} / 1차 {}".format(r.get("status"), r.get("reason"), r.get("try1")))
    check("retrypop: 재실행 사유에 '다른 창 요청 차단' 없음", qa_run.INTERFERE_POPUP not in r.get("reason", ""),
          r.get("reason", ""))
    no_writes("retrypop: 서버 쓰기 0", bool(res), w)


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
    check("bat: 업데이트 안내는 Claude 에 요청 · 담당 기획자 문구 (R3-25)",
          'Claude Code 에 "qa-test 플러그인 업데이트해줘" 라고 요청한 뒤 다시 실행하세요. '
          '모르면 이 화면을 담당 기획자에게 보내 주세요.' in qa_run.BAT_TEMPLATE)

    # Python 찾기 블록 (R3-25): py 가 PATH 에 없고 대체 경로(LOCALAPPDATA)에도 없으면 안내하고 종료 코드 1
    pyblock = "rem Use the py launcher" + qa_run.FIND_RUNNER.split("rem Use the py launcher", 1)[1]
    pyblock = pyblock.replace("{{", "{").replace("}}", "}")
    bat = WORK["tmp"] / "py_find.bat"
    bat.write_text("@echo off\r\nsetlocal enabledelayedexpansion\r\n{}\r\necho PY_OK !PY!\r\n".format(
        pyblock.replace("\n", "\r\n")), encoding="cp949", errors="replace")
    import os
    empty = WORK["tmp"] / "fake_localappdata"
    empty.mkdir(exist_ok=True)
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = dict(os.environ, PATH=sysdir, LOCALAPPDATA=str(empty))
    # 새 콘솔(창 없음)로 띄워 bat 을 더블클릭했을 때와 같은 기본 코드 페이지(cp949)로 출력을 받는다
    nowin = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, stdin=subprocess.DEVNULL, env=env,
                       creationflags=nowin)
    msg = p.stdout.decode("cp949", errors="replace")
    check("bat: py 없음 + 대체 경로 없음 = 종료 코드 1 · 안내 문구",
          p.returncode == 1 and "Python 을 찾지 못했습니다. 이 화면을 담당 기획자에게 보내 주세요." in msg
          and "PY_OK" not in msg, "{} / {}".format(p.returncode, msg.strip()[:200]))
    p = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, stdin=subprocess.DEVNULL, creationflags=nowin)
    msg = p.stdout.decode("cp949", errors="replace")
    check("bat: 이 PC 환경에서는 Python 찾기 통과 (대조군)", p.returncode == 0 and "PY_OK" in msg,
          "{} / {}".format(p.returncode, msg.strip()[:200]))


ORDER = ["dry", "live", "block", "approved", "allowpost", "confirm", "getw", "late", "lateok", "popup",
         "race", "absence", "dlg", "allerr", "popsel", "boundary", "bypass", "wfalse", "ignore", "blres", "explore",
         "trace", "gate", "sess", "sessblk", "retrypop", "probe", "flaky", "trend", "shared", "smoke", "bat"]


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
