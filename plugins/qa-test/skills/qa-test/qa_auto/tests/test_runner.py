"""qa_run.py 의 브라우저 없는 부분 단위 테스트:  py qa_auto/tests/test_runner.py

지난 실행 정리(prune) · 비교 표시(mark_trend) · 비교 기준 · trace 사본 · 폴더 이름 ·
판정 변환(final_status) · 재실행 병합(merge_retry) · 경고 코드 묶음 · run_case 상태 전이(FakeRunner).
임시 폴더만 쓰고, 끝나면 그 폴더 하나만 지운다.
"""

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qa_run  # noqa: E402
import report  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CASES = []


def t(name, got, want):
    CASES.append((name, got == want, got, want))


# -- 비교 표시 --------------------------------------------------
def trend(prev, now):
    r = [{"id": "X", "status": now, "reason": "b"}]
    qa_run.mark_trend(r, [{"id": "X", "status": prev, "reason": "a"}] if prev else [])
    return r[0].get("trend")


t("실패 → 실패: 계속 실패", trend("FAIL", "FAIL"), "still")
t("점검 필요 → 실패: 새로 실패", trend("ERROR", "FAIL"), "new")
t("통과 → 실패: 새로 실패", trend("PASS", "FAIL"), "new")
t("새 케이스 실패: 새로 실패", trend(None, "FAIL"), "new")
t("통과 → 점검 필요: 판정 불가로 바뀜", trend("PASS", "ERROR"), "unjudged")
t("실패 → 점검 필요: 판정 불가로 바뀜", trend("FAIL", "ERROR"), "unjudged")
t("점검 필요 → 점검 필요: 계속 점검 필요", trend("ERROR", "ERROR"), "still_err")
t("실패 → 통과: 수정됨", trend("FAIL", "PASS"), "fixed")
t("새 케이스 점검 필요: 표시 없음", trend(None, "ERROR"), None)

# -- 비교 기준 --------------------------------------------------
key = {"spec": "a.json", "ids": {"A1", "A2"}, "mode": False}
t("다른 스펙 실행은 기준 아님", qa_run._is_baseline({"spec": "b.json", "dry_run": True}, [], False, key), False)
t("쓰기 없는 스펙은 모드 무관", qa_run._is_baseline({"spec": "a.json", "dry_run": True}, [], False, key), True)
t("쓰기 있는 스펙은 같은 모드만",
  qa_run._is_baseline({"spec": "a.json", "dry_run": True}, [], False, dict(key, mode=True)), False)
t("예전 형식은 케이스 ID 로 판단", qa_run._is_baseline({}, [{"id": "A1"}, {"id": "A2"}], False, key), True)
t("예전 형식 다른 스펙", qa_run._is_baseline({}, [{"id": "B1"}], False, key), False)
t("부분 실행은 기준 아님", qa_run._is_baseline({"spec": "a.json", "partial": True}, [], False, key), False)

# -- 폴더 이름 --------------------------------------------------
t("한글 프로젝트 이름 유지", qa_run.safe_name("재고 대시보드"), "재고_대시보드")
t("cp949 밖 글자만 바꿈", qa_run.safe_name("재고·대시"), "재고·대시")
t("이모지는 _", qa_run.safe_name("재고✅"), "재고_")

# -- 웍스 로그 한 줄 --------------------------------------------
r = {"id": "TC-1", "title": "제목", "fix": "", "area": "목록", "source": "SB-01",
     "severity": "치명", "steps": [{"phase": "test", "status": "FAIL",
                                   "msg": "문구 불일치 - 기대:'a' 포함 / 실제:'b'"}], "reason": ""}
t("fix 없으면 영역 오류: 실제 현상", report._fix_line(r, with_id=True), "[치명] 목록 오류: b (SB: SB-01) · TC-1")
t("개발 탭은 [치명] 없이", report._fix_line(r, sev_prefix=False), "목록 오류: b (SB: SB-01)")
t("주소의 토큰 가림", report._mask_url("https://a/x?sid=abc&page=2&access_token=zz"),
  "https://a/x?sid=***&page=2&access_token=***")


# -- 판정 변환 (final_status) 현행 동작 표 ----------------------
# (이름, 예외, interfered, dismissed, blocked_log, 기대 상태, 기대 사유)
FS = qa_run.final_status
REQ, DLG = qa_run.INTERFERE_REQUEST, qa_run.INTERFERE_DIALOG
FS_TABLE = [
    ("예외 없음 = 통과", None, set(), [], [], "PASS", ""),
    ("세션 만료", qa_run.SessionExpired("만료"), set(), [], [], "ERROR", "세션 만료로 중단 - 만료"),
    ("쓰기 차단", qa_run.WriteBlocked("막음", "undeclared"), set(), [], [], "BLOCKED", "막음"),
    ("조건 미충족", qa_run.PreconditionUnmet("없음"), set(), [], [], "NA", "조건 미충족 - 없음"),
    ("스크립트 문제", qa_run.StepFailure("요소 없음"), set(), [], [], "ERROR", "요소 없음"),
    ("스크립트 문제는 개입 있어도 사유 그대로", qa_run.StepFailure("요소 없음"), {REQ}, [], ["POST /a"],
     "ERROR", "요소 없음"),
    ("시간 초과", qa_run.PWTimeout("Timeout 3000ms exceeded."), set(), [], [], "ERROR",
     "시간 초과 - 요소가 없거나 셀렉터가 바뀌었을 수 있음 (Timeout 3000ms exceeded.)"),
    ("판정 실패", qa_run.AssertFail("문구 불일치"), set(), [], [], "FAIL", "문구 불일치"),
    ("요청 차단 뒤 판정 실패", qa_run.AssertFail("문구 불일치"), {REQ}, [], ["POST /a", "POST /b", "POST /c"],
     "ERROR", "판정 불가 - 요청 차단로 화면 흐름이 달라짐: 문구 불일치 / 막은 요청: POST /a / POST /b"),
    ("확인창 취소 뒤 판정 실패", qa_run.AssertFail("문구 불일치"), {DLG}, ["삭제?"], [],
     "ERROR", "판정 불가 - 확인창 취소로 화면 흐름이 달라짐: 문구 불일치"),
    ("둘 다 개입", qa_run.AssertFail("x"), {DLG, REQ}, [], ["POST /a"],
     "ERROR", "판정 불가 - 요청 차단 · 확인창 취소로 화면 흐름이 달라짐: x / 막은 요청: POST /a"),
    # 확인창 4조합 (R3-01). 취소 기록은 dismissed 에만 쌓이고, expect_dialog 가 통과하면 interfered 를 켠다
    ("취소 → expect_dialog 통과(interfered 켜짐) → 판정 실패 = 점검 필요", qa_run.AssertFail("x"), {DLG}, [], [],
     "ERROR", "판정 불가 - 확인창 취소로 화면 흐름이 달라짐: x"),
    ("expect_dialog 문구 불일치(취소 기록 소비) = 실패", qa_run.AssertFail("알럿 불일치"), set(), [], [],
     "FAIL", "알럿 불일치"),
    ("판정 스텝 도중 취소(dismissed 만 남음) + 판정 실패 = 점검 필요", qa_run.AssertFail("x"), set(), ["삭제?"], [],
     "ERROR", "판정 불가 - 확인창 취소로 화면 흐름이 달라짐: x"),
    ("확인창 뜬 뒤 expect_no_dialog 실패(취소 기록 소비, 개입 없음) = 실패", qa_run.AssertFail("알럿이 뜸"),
     set(), [], [], "FAIL", "알럿이 뜸"),
    ("같은 세션 팝업 요청 차단 뒤 판정 실패 = 점검 필요", qa_run.AssertFail("x"), {qa_run.INTERFERE_POPUP}, [],
     ["POST /p (다른 창)"], "ERROR", "판정 불가 - 다른 창 요청 차단로 화면 흐름이 달라짐: x / 막은 요청: POST /p (다른 창)"),
]
for name, exc, itf, dis, blog, want_s, want_r in FS_TABLE:
    t("final_status: " + name, FS(exc, [], itf, dis, blog), (want_s, want_r))

# -- 재실행 병합 (merge_retry) 현행 동작 ------------------------
def res(status, reason="", shots=(), duration=1.0):
    return {"id": "R", "status": status, "reason": reason, "shots": list(shots), "duration": duration}


first, second = res("FAIL", "a", ["R_try1.png"], 1.2), res("PASS", "", ["R.png"], 2.3)
m = qa_run.merge_retry(first, second)
t("merge_retry: 실패 → 통과는 불안정", (m["status"], m["reason"]), ("FLAKY", "1차 실패, 재실행 통과 - a"))
t("merge_retry: 시간 합산 · 1차 기록 · 스크린샷 순서",
  (m["duration"], m["try1"], m["shots"]),
  (3.5, {"status": "FAIL", "reason": "a", "shots": ["R_try1.png"]}, ["R_try1.png", "R.png"]))
t("merge_retry: 입력은 그대로", (first["status"], second["status"], second["shots"]), ("FAIL", "PASS", ["R.png"]))
m = qa_run.merge_retry(res("FAIL", "a"), res("FAIL", "b"))
t("merge_retry: 실패 → 실패", (m["status"], m["reason"], m.get("retried"), "flaky" in m), ("FAIL", "b", True, False))
m = qa_run.merge_retry(res("FAIL", "a"), res("ERROR", "시간 초과"))
t("merge_retry: 실패 → 점검 필요는 점검 필요 + 사유 앞 '1차 실패:'", (m["status"], m["reason"], m.get("retried")),
  ("ERROR", "1차 실패: a / 재실행: 시간 초과", True))
m = qa_run.merge_retry(res("ERROR", "a"), res("ERROR", "b"))
t("merge_retry: 점검 필요 → 점검 필요는 2차 사유 그대로", (m["status"], m["reason"]), ("ERROR", "b"))
m = qa_run.merge_retry(res("ERROR", "시간 초과"), res("PASS"))
t("merge_retry: 점검 필요 → 통과는 통과 + 재실행 통과 표시", (m["status"], m["reason"], m.get("flaky")),
  ("PASS", "", "시간 초과"))

# -- 경고 코드 묶음 (report) ----------------------------------------
from gates import W_NO_FIX, W_ABSENCE_NO_ANCHOR, W_PRE_NO_ANCHOR, W_RESIDUE, W_SELECTOR_UPDATE, W_QA_PREFIX  # noqa: E402
warns = [(W_NO_FIX, "A: 문장을 바꿔도"), (W_NO_FIX, "B: 묶임"), (W_ABSENCE_NO_ANCHOR, "A: x"),
         (W_PRE_NO_ANCHOR, "C: y"), (W_QA_PREFIX, "A 스텝1: 프리픽스 없음"), ("unknown_code", "새 경고")]
blk = report._warn_blocks(warns)
t("경고 묶음: 스펙 점검은 코드로 묶음 (문장 무관)", "<li>fix 없음 2건: A, B</li>" in blk, True)
t("경고 묶음: anchor 코드 둘은 한 줄", "<li>anchor 없는 판정 2건: A, C</li>" in blk, True)
t("경고 묶음: 스펙 점검 건수", "스펙 점검 4건" in blk, True)
t("경고 묶음: 모르는 코드 · 실행 경고는 주황", "확인할 경고 2건" in blk, True)
todo = report._todo_html([], [], [(W_SELECTOR_UPDATE, "아무 문장"), (W_RESIDUE, "아무 문장")], 0)
t("할 일: 셀렉터 갱신 · 잔여 데이터는 코드로", ("셀렉터 갱신 요청" in todo, "잔여 데이터 확인" in todo), (True, True))
todo = report._todo_html([], [], [(W_QA_PREFIX, "셀렉터 갱신 필요: 테스트 데이터가 남아")], 0)
t("할 일: 문장에 같은 말이 있어도 코드가 다르면 안 띄움",
  ("셀렉터 갱신 요청" in todo, "잔여 데이터 확인" in todo), (False, False))


# -- 차단 안내 (report) ---------------------------------------------
def gate(by):
    r = {"id": "B1", "status": "BLOCKED", "reason": "문장 무관", "blocked_by": by}
    return report._gate_block(r, {}, "s.json")


t("차단 안내: blocklist", "의도적으로 막아둔 것" in gate("blocklist"), True)
t("차단 안내: 선언 누락", "쓰기 승인 선언이 없습니다" in gate("undeclared"), True)
t("차단 안내: 잔여 점검", "읽기 스텝만" in gate("residue_write"), True)
t("차단 안내: cleanup 없음", "정리(cleanup) 절차가 없습니다" in gate("no_cleanup"), True)


# -- run_case 상태 전이 (브라우저 없이) ------------------------------
class StubPage:
    """expect_dialog · expect_no_dialog 의 실제 코드가 쓰는 만큼만."""
    url = "http://fake.local/x"

    def wait_for_timeout(self, ms):
        pass


class StubDialog:
    type = "confirm"
    page = None

    def __init__(self, message):
        self.message = message

    def dismiss(self):
        pass

    def accept(self):
        pass


class FakeRunner(qa_run.Runner):
    """스텝의 _raise 예외를 그대로 내는 러너. _block 이 있으면 그 스텝에서 요청을 막은 것으로 친다."""

    def __init__(self):
        super().__init__(None, {"meta": {"base_url": "http://fake.local"}}, Path("fake.json"),
                         Path("unused"), Path("unused"))
        self.page = StubPage()

    def run(self, step, case_id, idx):
        if step.get("action") in ("expect_dialog", "expect_no_dialog"):
            return qa_run.Runner.run(self, step, case_id, idx)     # 실제 판정 코드 (StubPage 필요)
        self.check_dismissed()          # 실제 run 처럼 앞 스텝의 취소 기록을 먼저 본다
        if step.get("action") in qa_run.CLEARS_DIALOGS:
            self.dialogs = []
        if step.get("_dialog"):
            # 이 스텝 도중 확인창이 떴다 (승인 안 된 스텝이면 취소된다)
            self._on_dialog(StubDialog(step["_dialog"]))
        if step.get("_block"):
            self.blocked_log.append(step["_block"])
            self.interfered.add(qa_run.INTERFERE_REQUEST)
        if step.get("_pending"):
            self.blocked.append(step["_pending"])
        if step.get("_raise") is not None:
            raise step["_raise"]

    def _shoot(self, *a, **k):
        pass

    def _shoot_failure(self, *a, **k):
        pass

    def _anchor_visible(self, step, timeout):
        return bool(step.get("_anchor_ok"))


def run_fake(steps, dry_run=True, runner=None, **case):
    c = dict({"id": "K", "title": "t", "steps": steps}, **case)
    return qa_run.run_case(runner or FakeRunner(), c, [], dry_run)


GOTO = {"action": "goto", "url": "/x"}
r = run_fake([GOTO, {"action": "expect_visible", "selector": "#a"}])
t("run_case: 전부 통과", (r["status"], r["reason"], [s["status"] for s in r["steps"]]), ("PASS", "", ["OK", "OK"]))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "_raise": qa_run.AssertFail("다름")}])
t("run_case: 판정 실패", (r["status"], r["reason"], r["steps"][-1]["status"]), ("FAIL", "다름", "FAIL"))
r = run_fake([GOTO, {"action": "expect_visible", "selector": "#late",
                     "_raise": qa_run.PWTimeout("Timeout 3000ms exceeded.")}])
t("run_case: 시간 초과는 점검 필요 + timeout · err_selector",
  (r["status"], r.get("timeout"), r.get("err_selector")), ("ERROR", True, "#late"))
r = run_fake([GOTO, {"action": "download", "selector": "#dl",
                     "_raise": qa_run.StepFailure("파일이 내려오지 않음 - 시간 초과 (1초)", timeout=True)}])
t("run_case: 다운로드 대기 초과도 timeout", (r["status"], r.get("timeout"), r.get("err_selector")),
  ("ERROR", True, "#dl"))
r = run_fake([GOTO, {"action": "expect_visible", "selector": "#a", "_raise": qa_run.StepFailure("설정 오류")}])
t("run_case: 시간 초과 아닌 점검 필요는 timeout 없음", (r["status"], "timeout" in r, "err_selector" in r),
  ("ERROR", False, False))
r = run_fake([GOTO, {"action": "click", "selector": "#tab", "write": False, "_block": "POST /api/x"},
              {"action": "expect_text", "selector": "#a", "contains": "x", "_raise": qa_run.AssertFail("다름")}])
t("run_case: 요청을 막은 뒤 판정 실패는 점검 필요",
  (r["status"], r["reason"]), ("ERROR", "판정 불가 - 요청 차단로 화면 흐름이 달라짐: 다름 / 막은 요청: POST /api/x"))
r = run_fake([GOTO, {"action": "click", "selector": "#tab", "write": False, "_pending": "POST /api/x"}])
t("run_case: 마지막 스텝 뒤 남은 차단은 점검 필요", (r["status"], r["reason"].startswith("서버 요청을 막음: POST /api/x")),
  ("ERROR", True))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "precondition": True, "anchor": "#b",
                     "_anchor_ok": True, "_raise": qa_run.AssertFail("없음")}])
t("run_case: precondition 불일치 + anchor 보임 = 조건 미충족", (r["status"], r["reason"]), ("NA", "조건 미충족 - 없음"))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "precondition": True,
                     "_raise": qa_run.PWTimeout("Timeout 3000ms exceeded.")}])
t("run_case: precondition 시간 초과 = 조건 확인 불가 + timeout",
  (r["status"], "조건 확인 불가 - 시간 초과" in r["reason"], r.get("timeout")), ("ERROR", True, True))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "soft": True,
                     "_raise": qa_run.AssertFail("하나")},
              {"action": "expect_text", "selector": "#b", "contains": "y", "soft": True,
               "_raise": qa_run.AssertFail("둘")}])
t("run_case: soft 실패는 모아서 한 번에", (r["status"], r["reason"], len(r["steps"])), ("FAIL", "하나 / 둘", 2 + 1))
r = run_fake([GOTO, {"action": "click", "selector": "#a", "write": False,
                     "_raise": qa_run.WriteBlocked("막음", "blocklist")}])
t("run_case: 화면 글자 차단", (r["status"], r["reason"], r.get("blocked_by")), ("BLOCKED", "막음", "blocklist"))
try:
    run_fake([GOTO, {"action": "expect_visible", "selector": "#a", "_raise": qa_run.SessionExpired("만료")}])
    t("run_case: 세션 만료는 전체 중단", "중단 안 됨", "SessionAbort")
except qa_run.SessionAbort as e:
    t("run_case: 세션 만료는 전체 중단", (e.result["status"], e.result["reason"], e.result.get("aborted")),
      ("ERROR", "세션 만료로 중단 - 만료", True))
# 게이트
r = qa_run.run_case(FakeRunner(), {"id": "K", "title": "t", "steps": [{"action": "click", "selector": "text=발송"}]},
                    ["text=발송"], True)
t("게이트: blocklist", (r["status"], r.get("blocked_by")), ("BLOCKED", "blocklist"))
r = run_fake([GOTO, {"action": "fill", "selector": "#q", "value": "x"}])
t("게이트: writes 선언 누락", (r["status"], r.get("blocked_by"), r["reason"]),
  ("BLOCKED", "undeclared", "writes 선언 누락 - 스텝 2 'fill #q (값 1자)' 이 쓰기 동작"))
r = run_fake([GOTO, {"action": "fill", "selector": "#q", "value": "x"}], writes=True)
t("게이트: dry-run 쓰기 케이스는 건너뜀", (r["status"], "blocked_by" in r), ("SKIPPED", False))
r = run_fake([GOTO, {"action": "fill", "selector": "#q", "value": "x"}], dry_run=False, writes=True, approved=True)
t("게이트: cleanup 없음", (r["status"], r.get("blocked_by")), ("BLOCKED", "no_cleanup"))
r = run_fake([{"action": "fill", "selector": "#q", "value": "x"}], dry_run=False, writes=True, approved=True,
             cleanup=[GOTO])
t("게이트: 승인 쓰기 첫 스텝 goto 아님", (r["status"], r.get("blocked_by")), ("BLOCKED", "first_goto"))

# 확인창 취소 4조합 (R3-01): 실제 expect_dialog · expect_no_dialog 코드로
LEAVE = "작성 중인 내용이 사라집니다. 이동할까요?"
CLICK = {"action": "click", "selector": "#tolist", "write": False, "_dialog": LEAVE}
r = run_fake([GOTO, CLICK, {"action": "expect_dialog", "contains": "사라집니다"},
              {"action": "expect_text", "selector": "#title", "equals": "목록", "_raise": qa_run.AssertFail("다름")}])
t("확인창: 취소 → expect_dialog 통과 → 판정 실패 = 점검 필요",
  (r["status"], r["reason"]), ("ERROR", "판정 불가 - 확인창 취소로 화면 흐름이 달라짐: 다름"))
r = run_fake([GOTO, CLICK, {"action": "expect_dialog", "contains": "다른 문구", "timeout": 30}])
t("확인창: expect_dialog 문구 불일치 = 실패", (r["status"], r["reason"].startswith("알럿 문구 불일치")), ("FAIL", True))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "_dialog": "삭제?",
                     "_raise": qa_run.AssertFail("다름")}])
t("확인창: 판정 스텝 도중 취소 + 판정 실패 = 점검 필요",
  (r["status"], r["reason"]), ("ERROR", "판정 불가 - 확인창 취소로 화면 흐름이 달라짐: 다름"))
r = run_fake([GOTO, dict(CLICK, _dialog="삭제?"), {"action": "expect_no_dialog", "ms": 0}])
t("확인창: confirm 뜬 뒤 expect_no_dialog = 실패", (r["status"], r["reason"]), ("FAIL", "알럿이 뜨면 안 되는데 뜸: 삭제?"))
r = run_fake([GOTO, CLICK, {"action": "expect_dialog", "contains": "사라집니다"}])
t("확인창: 문구만 확인하고 끝나면 통과", (r["status"], r["reason"]), ("PASS", ""))
r = run_fake([GOTO, CLICK, {"action": "expect_text", "selector": "#a", "contains": "x"}])
t("확인창: expect_dialog 없이 다음 스텝 = 점검 필요(취소 사유)",
  (r["status"], r["reason"].startswith("확인창 '" + LEAVE)), ("ERROR", True))
fr = FakeRunner()
fr.dialogs = ["앞 케이스 알럿"]
r = run_fake([{"action": "expect_no_dialog", "ms": 0}], runner=fr)
t("확인창: 앞 케이스 알럿 기록은 케이스 시작 때 비움", (r["status"], r["reason"]), ("PASS", ""))

# precondition 개입 (R3-01 · D-05): 안전망이 막았으면 조건 미충족이 아니라 점검 필요
r = run_fake([GOTO, {"action": "expect_count", "selector": "tr", "min": 1, "precondition": True, "anchor": "#t",
                     "_anchor_ok": True, "_block": "POST /api/list", "_raise": qa_run.AssertFail("0건")}])
t("precondition: 요청 차단 뒤 어긋남 = 점검 필요 (조건 미충족 아님)",
  (r["status"], r["reason"]),
  ("ERROR", "조건 확인 불가 - 안전망 개입(요청 차단)으로 화면 흐름이 실제와 다름: 0건 / 막은 요청: POST /api/list"))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "precondition": True, "anchor": "#b",
                     "_anchor_ok": True, "_dialog": "이동?", "_raise": qa_run.AssertFail("없음")}])
t("precondition: 확인창 취소 뒤 어긋남 = 점검 필요",
  (r["status"], r["reason"].startswith("조건 확인 불가 - 안전망 개입(확인창 취소)")), ("ERROR", True))


# -- 판정 못 한 건이 있을 때 전달 문구 (R3-03) -----------------------
NO_DEFECT = ("미반영 오류 없음", "어긋난 항목은 없습니다", "어긋난 항목이 없습니다", "전달할 내용 없음")


def rr(cid, status, **kw):
    return dict({"id": cid, "title": cid, "status": status, "reason": "", "kind": "defect", "steps": [],
                 "shots": [], "requirement": cid, "source": "SB-01", "area": "목록", "fix": cid + " 오류"}, **kw)


def deliver(results, n_cases=None, partial=False):
    """(웍스 줄 목록, md, 개발 탭 HTML)."""
    spec = {"meta": {"project": "P", "base_url": "http://x"},
            "cases": [{"id": "C{}".format(i)} for i in range(n_cases or len(results))]}
    run_meta = {"partial": partial}
    d = Path(tempfile.mkdtemp(prefix="qa_unit_rep_"))
    try:
        log = report.build_works_log(spec, results, d, run_meta).read_text(encoding="utf-8")
        md = report.build_dev_md(spec, results, d, run_meta).read_text(encoding="utf-8")
        dev = report._dev_block(spec, results, report._held_items(results), "개발전달.md", "웍스등록.txt", log,
                                run_meta)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return log.splitlines(), md, dev


def no_defect_words(*texts):
    return [w for w in NO_DEFECT for x in texts if w in x]


TODAY_MD = __import__("datetime").datetime.now().strftime("%m/%d")
log, md, dev = deliver([rr("Z1", "ERROR"), rr("Z2", "ERROR"), rr("Z3", "ERROR")])
t("전달 문구: 전부 점검 필요 - 웍스 첫 두 줄", log[:2],
  ["[{}] P 검수 · 판정한 0건 중 기획 미반영 오류 0건".format(TODAY_MD), "판정하지 못한 3건은 기획팀 확인 뒤 다시 전달합니다."])
t("전달 문구: 전부 점검 필요 - md 요약 줄", "- 판정 0건 중 **미반영 오류 0건** · 판정 못 함 3건(점검 필요 3)" in md, True)
t("전달 문구: 전부 점검 필요 - 개발 탭 전달 보류 + 기획팀 검토로",
  ("전달 보류 · 판정 못 한 건 3건" in dev, "기획팀 검토로" in dev, 'id="copybtn"' in dev), (True, True, False))
t("전달 문구: 전부 점검 필요 - '오류 없음' 류 0", no_defect_words("\n".join(log), md, dev), [])

log, md, dev = deliver([rr("F1", "FAIL"), rr("E1", "ERROR"), rr("E2", "BLOCKED")])
t("전달 문구: 실패 1 + 판정 못 함 2 - 웍스 첫 줄 그대로 · 둘째 줄 추가 전달", log[:2],
  ["[{}] P 검수 · 기획 미반영 오류 1건".format(TODAY_MD), "판정하지 못한 2건은 기획팀 확인 뒤 추가로 전달합니다."])
t("전달 문구: 실패 1 + 판정 못 함 2 - 목록 유지", "1. F1 오류 · F1" in log, True)
t("전달 문구: 실패 1 + 판정 못 함 2 - md",
  ("- 판정 1건 중 **미반영 오류 1건** · 판정 못 함 2건(점검 필요 1 · 차단 1)" in md,
   "판정하지 못한 2건은 기획팀 확인 뒤 추가로 전달합니다." in md), (True, True))
t("전달 문구: 실패 1 + 판정 못 함 2 - 개발 탭은 복사 버튼", 'id="copybtn"' in dev, True)
t("전달 문구: 실패 1 + 판정 못 함 2 - '오류 없음' 류 0", no_defect_words("\n".join(log), md, dev), [])

log, md, dev = deliver([rr("P1", "PASS"), rr("E1", "ERROR")], n_cases=5, partial=True)
t("전달 문구: 부분 실행 + 점검 필요 - 웍스",
  log[:3], ["[{}] P 검수 · 판정한 1건 중 기획 미반영 오류 0건".format(TODAY_MD),
            "일부 케이스만 실행 (전체 5건 중 2건)", "판정하지 못한 1건은 기획팀 확인 뒤 다시 전달합니다."])
t("전달 문구: 부분 실행 + 점검 필요 - md 머리", "- 일부 케이스만 실행 (전체 5건 중 2건)" in md, True)
t("전달 문구: 부분 실행 + 점검 필요 - '오류 없음' 류 0", no_defect_words("\n".join(log), md, dev), [])

log, md, dev = deliver([rr("W1", "SKIPPED"), rr("W2", "SKIPPED")])
t("전달 문구: 전부 승인 대기 - 웍스", log[:2],
  ["[{}] P 검수 · 판정한 0건 중 기획 미반영 오류 0건".format(TODAY_MD), "승인 대기 2건은 실행하지 않았습니다."])
t("전달 문구: 전부 승인 대기 - md · 개발 탭 '승인 대기 2건'",
  ("- 판정 0건 중 **미반영 오류 0건** · 승인 대기 2건" in md, "승인 대기 2건" in dev), (True, True))
t("전달 문구: 전부 승인 대기 - '오류 없음' 류 0", no_defect_words("\n".join(log), md, dev), [])

log, md, dev = deliver([rr("P1", "PASS"), rr("FL", "FLAKY"), rr("N1", "NA"),
                        rr("QA-잔여", "ERROR", kind="qa")])
t("전달 문구: 불안정 · 조건 미충족도 판정 못 함, 잔여 점검은 세지 않음",
  "- 판정 1건 중 **미반영 오류 0건** · 판정 못 함 2건(불안정 1 · 조건 미충족 1)" in md, True)
log, md, dev = deliver([rr("P1", "PASS"), rr("P2", "PASS")])
t("전달 문구: 전부 통과면 지금처럼 '오류 없음'",
  ("미반영 오류 없음." in log, "어긋난 항목은 없습니다" in md, "전달할 내용 없음" in dev), (True, True, True))


# -- results.json 을 리포트보다 먼저 (R3-21) --------------------------
d = Path(tempfile.mkdtemp(prefix="qa_unit_save_"))
real_build = qa_run.build_report
try:
    def boom(*a, **k):
        raise RuntimeError("리포트 조립 중 오류")
    qa_run.build_report = boom
    got = qa_run.save_outputs({"meta": {"project": "P", "base_url": "http://x"}, "cases": []},
                              [rr("W1", "ERROR", unclean=True, unclean_inputs=["[QA]x"])], [], d / "run", False,
                              Path("s.json"), None, {"partial": False, "n_dev": 0})
    saved = json.loads((d / "run" / "results.json").read_text(encoding="utf-8"))
    t("results.json: 리포트 생성 예외에도 남음 · unclean 유지",
      (got, saved["results"][0].get("unclean"), saved["meta"].get("n_dev")), (None, True, 0))
    t("results.json: 임시 파일이 남지 않음", (d / "run" / "results.json.tmp").exists(), False)
finally:
    qa_run.build_report = real_build
    shutil.rmtree(d, ignore_errors=True)


# -- 지난 실행 정리 ----------------------------------------------
def make_run(root, n, meta=None, results=None, extra=None, legacy=False, broken=False, dirs=()):
    d = root / "p_20260101_{:06d}".format(n)
    d.mkdir()
    if broken:
        (d / "results.json").write_text("{깨짐", encoding="utf-8")
    elif legacy:
        (d / "results.json").write_text(json.dumps(results or []), encoding="utf-8")
    else:
        m = {"runner": 5, "spec": "a.json", "dry_run": True}
        m.update(meta or {})
        (d / "results.json").write_text(json.dumps({"meta": m, "results": results or []}), encoding="utf-8")
    for x in dirs:
        (d / x).mkdir()
    if extra:
        (d / extra).write_text("x", encoding="utf-8")
    return d


tmp = Path(tempfile.mkdtemp(prefix="qa_unit_"))
try:
    root = tmp / "out"
    root.mkdir()
    make_run(root, 1, legacy=True, results=[{"id": "A1", "status": "FAIL"}])     # 예전 형식
    d2 = make_run(root, 2, meta={"n_dev": 1}, dirs=("downloads", "traces"))     # 개발 전달 근거
    make_run(root, 3, results=[{"id": "A1", "status": "PASS", "unclean": True}])  # 운영 데이터 미정리
    make_run(root, 4, extra="개발전달_캡처.pptx")                                # 사람이 넣은 파일
    make_run(root, 5)                                                          # 지울 것
    make_run(root, 6)                                                          # 지울 것
    make_run(root, 7, meta={"dry_run": False})                                 # 전체 실행 모드의 마지막 기준
    make_run(root, 8)                                                          # 지울 것
    make_run(root, 9, broken=True)                                             # 읽을 수 없음
    make_run(root, 10)
    make_run(root, 11)
    removed = qa_run.prune_outputs(root, "p", keep=2, protect=set())
    left = sorted(p.name[-2:] for p in root.iterdir())
    t("정리: 지울 수 있는 3개만 지움", sorted(x[-2:] for x in removed), ["05", "06", "08"])
    t("정리: 보존 대상은 남음", left, ["01", "02", "03", "04", "07", "09", "10", "11"])
    t("정리: 개발 전달 근거는 trace 를 남기고 내려받은 파일만 지움",
      ((d2 / "traces").exists(), (d2 / "downloads").exists()), (True, False))
    t("정리: 이름 바꾼 임시 폴더가 남지 않음", any(p.name.startswith("_trash_") for p in root.iterdir()), False)

    root2 = tmp / "out2"
    root2.mkdir()
    for i in range(1, 8):
        make_run(root2, i)
    t("정리: 한 번에 최대 3개", len(qa_run.prune_outputs(root2, "p", keep=2, protect=set())), 3)

    # -- trace 사본 --
    src = tmp / "T1.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("trace.trace", "\n".join(json.dumps(o) for o in [
            {"type": "context-options", "options": {"storageState": {
                "cookies": [{"name": "PHPSESSID", "value": "SECRET_COOKIE"}],
                "origins": [{"localStorage": [{"name": "access_token", "value": "SECRET_LS"}]}]}}},
            {"type": "before", "params": {"url": "https://a.co.kr/x?sid=SECRET_URL_SID"}},
            {"type": "frame-snapshot", "snapshot": {"html": ["META", {"name": "csrf-token",
                                                                       "content": "SECRET_DOM_META"}]}},
        ]))
        z.writestr("trace.network", json.dumps({"type": "resource-snapshot", "snapshot": {
            "request": {"url": "https://a.co.kr/api/me?access_token=SECRET_URL_QUERY",
                        "headers": [{"name": "X-Auth-Token", "value": "SECRET_CUSTOM_HEADER"},
                                    {"name": "Cookie", "value": "SECRET_COOKIE2"}],
                        "postData": {"_sha1": "req1.dat", "text": "pw=SECRET_PW"}},
            "response": {"headers": [{"name": "Set-Cookie", "value": "SECRET_SETCOOKIE"}],
                         "content": {"mimeType": "application/json", "_sha1": "resp1.json"}}}}))
        z.writestr("resources/resp1.json", "SECRET_RESP_BODY")
        z.writestr("resources/req1.dat", "SECRET_PW2")
        z.writestr("resources/shot.jpeg", "IMG")
    qa_run.strip_trace(src)
    dst = src.with_name("T1_전달용.zip")
    with zipfile.ZipFile(dst) as z:
        names = z.namelist()
        blob = b"".join(z.read(n) for n in names)
    gone = ["SECRET_COOKIE", "SECRET_LS", "SECRET_URL_SID", "SECRET_URL_QUERY", "SECRET_CUSTOM_HEADER",
            "SECRET_COOKIE2", "SECRET_PW", "SECRET_SETCOOKIE", "SECRET_RESP_BODY", "SECRET_PW2"]
    t("trace 사본: 인증값 · 쿼리 · 본문 지움", [s for s in gone if s.encode() in blob], [])
    t("trace 사본: 화면 스냅샷(DOM)은 남음 (안내문에 명시)", b"SECRET_DOM_META" in blob, True)
    t("trace 사본: 화면 캡처는 남음", "resources/shot.jpeg" in names, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fails = [c for c in CASES if not c[1]]
    for name, ok, got, want in CASES:
        if not ok:
            print("  [실패] {} - 실제 {!r} / 기대 {!r}".format(name, got, want))
    print("runner 단위 테스트 {}건 중 실패 {}건".format(len(CASES), len(fails)))
    sys.exit(1 if fails else 0)
