"""qa_run.py 의 브라우저 없는 부분 단위 테스트:  py qa_auto/tests/test_runner.py

지난 실행 정리(prune) · 비교 표시(mark_trend) · 비교 기준 · trace 사본 · 폴더 이름 ·
판정 변환(final_status) · 재실행 병합(merge_retry) · 경고 코드 묶음 · run_case 상태 전이(FakeRunner).
임시 폴더만 쓰고, 끝나면 그 폴더 하나만 지운다.
"""

import html
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
t("fix 없으면 영역 오류: '실제' 노출 (SB: 기대)", report._fix_line(r, with_id=True),
  "[치명] 목록 오류: 'b' 노출 (SB: 'a' 포함) · TC-1")
t("개발 탭은 [치명] 없이", report._fix_line(r, sev_prefix=False), "목록 오류: 'b' 노출 (SB: 'a' 포함)")


# fix 없는 웍스 줄 형식 (R3-35): 테스트 제목 · 셀렉터 · 스텝 이름 대신 영역(없으면 '화면')과 기대 · 실제
def nofix(msgs, area="", source="스모크", title="크기·위치 실패 (expect_box)"):
    steps = [{"phase": "test", "status": "FAIL", "action": a, "label": "{} {}".format(a, s), "msg": m}
             for a, s, m in msgs]
    return report._fix_line({"id": "TC-9", "title": title, "fix": "", "area": area, "source": source,
                             "steps": steps, "reason": ""}, with_id=True)


line = nofix([("expect_text", "#title", "문구 불일치 - 기대:'틀린문구1' 포함 / 실제:'샘플 상세관리'"),
              ("expect_count", "button.detail", "개수 불일치 - 기대:3 / 실제:2")], title="soft 검증 여러 개")
t("fix 없음: 기대 · 실제가 있으면 '실제' 노출 (SB: '기대'), area 없으면 '화면'", line,
  "화면 오류: '샘플 상세관리' 노출 (SB: '틀린문구1' 포함) · TC-9")
line = nofix([("expect_count", "tr", "개수 불일치 - 기대:3 / 실제:2")], area="목록")
t("fix 없음: 따옴표 없는 기대값은 감쌈", line, "목록 오류: '2' 노출 (SB: '3') · TC-9")
line = nofix([("expect_box", "#chat", "크기·위치 불일치 - #chat: 너비 200px (기대 300px 이상) / "
               "#banner 오른쪽에 있지 않음 (경계에서 500px 안쪽)")])
t("fix 없음: 크기 · 위치 '(기대 …)' 는 '실제' (SB: '기대'), 셀렉터 · 제목 · action 이름 없음",
  (line, "#chat" in line, "expect_box" in line, "크기·위치 실패" in line),
  ("화면 오류: '너비 200px' (SB: '300px 이상') · TC-9", False, False, False))
line = nofix([("expect_box", "#chat", "크기·위치 불일치 - #chat: #banner 오른쪽에 있지 않음 (경계에서 500px 안쪽) / "
               "#banner 과의 간격 40px (기대 0~16px)")], area="상담")
t("fix 없음: '(기대 …)' 가 둘째 항목이어도 그 항목", line, "상담 오류: '#banner 과의 간격 40px' (SB: '0~16px') · TC-9")
line = nofix([("expect_box", "#chat", "크기·위치 불일치 - #chat: #banner 오른쪽에 있지 않음 (경계에서 500px 안쪽) / "
               "높이 0px")])
t("fix 없음: 기대 · 실제 · '(기대 …)' 모두 없으면 현상 앞 40자… (SB: 근거)", line,
  "화면 오류: 크기·위치 불일치 - #banner 오른쪽에 있지 않음 (경계에서… (SB: 스모크) · TC-9")
line = nofix([("expect_visible", "#x", "안 보임")], area="상단")
t("fix 없음: 짧은 현상은 말줄임 없이", line, "상단 오류: 안 보임 (SB: 스모크) · TC-9")

t("주소의 토큰 가림", report._mask_url("https://a/x?sid=abc&page=2&access_token=zz"),
  "https://a/x?sid=***&page=2&access_token=***")
# 이름에 token · session · secret · passw 가 들어 있으면 가림, 짧은 이름은 전체 일치만 (오케스트레이터 결정)
t("주소 가림: 이름 일부 일치 (session_id · auth_token · id_token · client_secret · passwd · userPassword)",
  report._mask_url("https://a/cb?session_id=1&auth_token=2&id_token=3&client_secret=4&passwd=5&userPassword=6"),
  "https://a/cb?session_id=***&auth_token=***&id_token=***&client_secret=***&passwd=***&userPassword=***")
t("주소 가림: 짧은 이름은 전체 일치 (sid · jsessionid · phpsessid · auth · pw · csrf · apikey · key · code_verifier)",
  report._mask_url("https://a/x?SID=1&PHPSESSID=2&auth=3&pw=4&csrf=5&apikey=6&key=7&code_verifier=8#jsessionid=9"),
  "https://a/x?SID=***&PHPSESSID=***&auth=***&pw=***&csrf=***&apikey=***&key=***&code_verifier=***#jsessionid=***")
t("주소 가림: goods_code · keyword · page · author · code · monkey 는 남김",
  report._mask_url("https://a/x?goods_code=1&keyword=타이레놀&page=2&author=kim&code=9&monkey=3"),
  "https://a/x?goods_code=1&keyword=타이레놀&page=2&author=kim&code=9&monkey=3")
# 이름 전체가 같을 때만 가림. 재현에 쓰는 상품코드 · 검색어는 남기고 fragment 토큰은 가림 (R3-27)
t("주소 가림: 상품코드 · 검색어는 남김", report._mask_url("https://a/goods?goods_code=12345&keyword=타이레놀"),
  "https://a/goods?goods_code=12345&keyword=타이레놀")
t("주소 가림: fragment 의 access_token", report._mask_url("https://a/cb#access_token=x&state=1"),
  "https://a/cb#access_token=***&state=1")
t("주소 가림: ?sid=", report._mask_url("https://a/x?sid=x"), "https://a/x?sid=***")
t("주소 가림: 대소문자 · api_key · refresh_token",
  report._mask_url("https://a/x?API_KEY=k&mode=2&refresh_token=r"), "https://a/x?API_KEY=***&mode=2&refresh_token=***")
t("주소 가림: 경로의 ;jsessionid", report._mask_url("https://a/x.do;jsessionid=ab"), "https://a/x.do;jsessionid=***")
t("주소 가림: 단건 조회 code 는 남김", report._mask_url("https://a/x?code=12345&page=2"),
  "https://a/x?code=12345&page=2")


# -- 판정 변환 (final_status) 현행 동작 표 ----------------------
# (이름, 예외, interfered, dismissed, blocked_log, 기대 상태, 기대 사유)
FS = qa_run.final_status
REQ, DLG = qa_run.INTERFERE_REQUEST, qa_run.INTERFERE_DIALOG
FS_TABLE = [
    ("예외 없음 = 통과", None, set(), [], [], "PASS", ""),
    ("세션 만료", qa_run.SessionExpired("만료"), set(), [], [], "ERROR",
     "로그인이 풀려 중단됨 - login_qa.bat 으로 다시 로그인한 뒤 실행합니다. 만료"),
    ("쓰기 차단", qa_run.WriteBlocked("막음", "undeclared"), set(), [], [], "BLOCKED", "막음"),
    ("조건 미충족", qa_run.PreconditionUnmet("없음"), set(), [], [], "NA", "조건 미충족 - 없음"),
    ("스크립트 문제", qa_run.StepFailure("요소 없음"), set(), [], [], "ERROR", "요소 없음"),
    ("스크립트 문제는 개입 있어도 사유 그대로", qa_run.StepFailure("요소 없음"), {REQ}, [], ["POST /a"],
     "ERROR", "요소 없음"),
    ("시간 초과", qa_run.PWTimeout("Timeout 3000ms exceeded."), set(), [], [], "ERROR",
     "시간 초과 - 요소가 없거나 셀렉터가 바뀌었을 수 있음 (Timeout 3000ms exceeded.)"),
    ("판정 실패", qa_run.AssertFail("문구 불일치"), set(), [], [], "FAIL", "문구 불일치"),
    ("요청 차단 뒤 판정 실패", qa_run.AssertFail("문구 불일치"), {REQ}, [], ["POST /a", "POST /b", "POST /c"],
     "ERROR", "판정 불가 - 안전망 개입(요청 차단)으로 화면 흐름이 실제와 다름. "
              "막은 요청: POST /a / POST /b · 이후 결과: 문구 불일치"),
    ("확인창 취소 뒤 판정 실패", qa_run.AssertFail("문구 불일치"), {DLG}, ["삭제?"], [],
     "ERROR", "판정 불가 - 안전망 개입(확인창 취소)으로 화면 흐름이 실제와 다름. 이후 결과: 문구 불일치"),
    ("둘 다 개입", qa_run.AssertFail("x"), {DLG, REQ}, [], ["POST /a"],
     "ERROR", "판정 불가 - 안전망 개입(요청 차단 · 확인창 취소)으로 화면 흐름이 실제와 다름. "
              "막은 요청: POST /a · 이후 결과: x"),
    # 확인창 4조합 (R3-01). 취소 기록은 dismissed 에만 쌓이고, expect_dialog 가 통과하면 interfered 를 켠다
    ("취소 → expect_dialog 통과(interfered 켜짐) → 판정 실패 = 점검 필요", qa_run.AssertFail("x"), {DLG}, [], [],
     "ERROR", "판정 불가 - 안전망 개입(확인창 취소)으로 화면 흐름이 실제와 다름. 이후 결과: x"),
    ("expect_dialog 문구 불일치(취소 기록 소비) = 실패", qa_run.AssertFail("알럿 불일치"), set(), [], [],
     "FAIL", "알럿 불일치"),
    ("판정 스텝 도중 취소(dismissed 만 남음) + 판정 실패 = 점검 필요", qa_run.AssertFail("x"), set(), ["삭제?"], [],
     "ERROR", "판정 불가 - 안전망 개입(확인창 취소)으로 화면 흐름이 실제와 다름. 이후 결과: x"),
    ("확인창 뜬 뒤 expect_no_dialog 실패(취소 기록 소비, 개입 없음) = 실패", qa_run.AssertFail("알럿이 뜸"),
     set(), [], [], "FAIL", "알럿이 뜸"),
    ("같은 세션 팝업 요청 차단 뒤 판정 실패 = 점검 필요", qa_run.AssertFail("x"), {qa_run.INTERFERE_POPUP}, [],
     ["POST /p (다른 창)"], "ERROR", "판정 불가 - 안전망 개입(다른 창 요청 차단)으로 화면 흐름이 실제와 다름. "
                                     "막은 요청: POST /p (다른 창) · 이후 결과: x"),
]
for name, exc, itf, dis, blog, want_s, want_r in FS_TABLE:
    t("final_status: " + name, FS(exc, [], itf, dis, blog), (want_s, want_r))
# R3-17: "{what}로" 조사 오류가 어떤 개입 조합에서도 안 생김 (받침 있는 '차단' 뒤 '로')
for itf in ({REQ}, {qa_run.INTERFERE_POPUP}, {REQ, DLG}, {DLG}):
    t("final_status: 조사 '차단로' 없음 · " + " · ".join(sorted(itf)),
      "차단로" in FS(qa_run.AssertFail("x"), [], itf, [], ["POST /a"])[1], False)
t("판정 불가 · 조건 확인 불가 문장의 앞부분이 같음 (안전망 개입 … 실제와 다름. … 이후 결과:)",
  (qa_run.REASON_INTERFERED.split(" - ", 1)[1], qa_run.REASON_PRE_INTERFERED.split(" - ", 1)[1]),
  (qa_run.REASON_PRE_INTERFERED.split(" - ", 1)[1],) * 2)
# 기획자가 읽는 사유 · 경고 · 신호 문장에 스텝 이름 · 설정 키 없음 (write:false · blocklist · dry-run 은 쓰는 말)
INTERNAL = ("expect_", "approved", "writes", "cleanup", "residue", "precondition", "anchor", "allow_post",
            "goto", "ignore_requests", "login_check")
said = {k: getattr(qa_run, k) for k in dir(qa_run)
        if k.startswith(("REASON_", "WARN_", "SIGNAL_")) and isinstance(getattr(qa_run, k), str)}
t("사유 · 경고 · 신호 상수 {}개에 내부 용어 0".format(len(said)),
  {k: [w for w in INTERNAL if w in v] for k, v in said.items() if any(w in v for w in INTERNAL)}, {})

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
t("경고 묶음: 스펙 점검은 코드로 묶음 (문장 무관)", "<li>fix 없음 2건: A, B (" in blk, True)
t("경고 묶음: anchor 코드 둘은 한 줄", "<li>anchor 없는 판정 2건: A, C (" in blk, True)
t("경고 묶음: 스펙 점검 건수", "스펙 점검 4건" in blk, True)
t("경고 묶음: 모르는 코드 · 실행 경고는 주황", "확인할 경고 2건" in blk, True)
# 스펙 점검 블록 (R3-32): 공통 '판정에는 영향이 없습니다' 대신 줄마다 영향 한 마디
from gates import (W_NO_AREA, W_UNKNOWN_REQ, W_SOFT_POSITION, W_APPROVED_FIRST_GOTO, W_BROAD_ANCHOR,  # noqa: E402
                   W_SHORT_BLOCK, W_BLOCKLIST_OFF)
every = [(c, "K: x") for c in (W_NO_FIX, W_NO_AREA, W_ABSENCE_NO_ANCHOR, W_UNKNOWN_REQ, W_SOFT_POSITION,
                               W_APPROVED_FIRST_GOTO, W_BROAD_ANCHOR, W_SHORT_BLOCK, W_BLOCKLIST_OFF)]
blk = report._warn_blocks(every)
lint_lis = [x for x in blk.split('id="lint"', 1)[1].split("<li>")[1:]]
t("스펙 점검: 공통 안내 '판정에는 영향이 없습니다' 없음", "판정에는 영향이 없습니다" in blk, False)
t("스펙 점검: 줄마다 괄호 영향 문구 (종류 전부)",
  (len(lint_lis), all(li.split("</li>")[0].endswith(")") for li in lint_lis)), (len(report.LINT_EFFECT), True))
t("스펙 점검: 영향 문구가 모든 묶음 이름에 있음", set(report.LINT_KINDS.values()) <= set(report.LINT_EFFECT), True)
blk = html.unescape(blk)
t("스펙 점검: fix 없음 · anchor · 첫 스텝 영향",
  ("fix 없음 1건: K (웍스 줄이 '영역 오류: 현상' 으로 대신 나감)" in blk,
   "anchor 없는 판정 1건: K (요소가 없으면 점검 필요로 나옴)" in blk,
   "승인 쓰기 케이스 첫 스텝 goto 아님 1건: K (실행 안 됨)" in blk), (True, True, True))


def fail_res(cid, fix="", status="FAIL", **kw):
    return dict({"id": cid, "title": "t", "status": status, "kind": "defect", "fix": fix, "steps": [],
                 "reason": "r", "requirement": "", "source": ""}, **kw)


todo = report._todo_html([fail_res("A"), fail_res("B", fix="x"), fail_res("C"), fail_res("D", status="ERROR")],
                         [], [(W_NO_FIX, "A: x"), (W_NO_FIX, "C: x"), (W_NO_FIX, "D: x"), (W_NO_FIX, "E: x")], 3)
t("할 일: fix 없음은 이번 전달(실패) 중 건수 · 범위 표기", "이번 전달 중 fix 없음 2건" in todo, True)
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
  (r["status"], r["reason"]),
  ("ERROR", "판정 불가 - 안전망 개입(요청 차단)으로 화면 흐름이 실제와 다름. 막은 요청: POST /api/x · 이후 결과: 다름"))
r = run_fake([GOTO, {"action": "click", "selector": "#tab", "write": False, "_pending": "POST /api/x"}])
t("run_case: 마지막 스텝 뒤 남은 차단은 점검 필요 (사유에 설정 키 없음)",
  (r["status"], r["reason"], "meta." in r["reason"]),
  ("ERROR", qa_run.REASON_REQUEST_BLOCKED.format("POST /api/x"), False))
# 승인 쓰기 스텝 뒤 download 가 띄운 확인창은 수락하지 않음 (R3-23, D-06)
r = run_fake([GOTO, {"action": "click", "selector": "#save", "write": True},
              {"action": "download", "selector": "#xls", "_dialog": "전체 내려받을까요?"}],
             dry_run=False, writes=True, approved=True, cleanup=[GOTO])
t("run_case: 쓰기 스텝 뒤 download 의 확인창은 취소 (점검 필요)",
  (r["status"], r["reason"].startswith("확인창 '전체 내려받을까요?'")), ("ERROR", True))
r = run_fake([GOTO, {"action": "click", "selector": "#save", "write": True, "_dialog": "저장할까요?"}],
             dry_run=False, writes=True, approved=True, cleanup=[GOTO])
t("run_case: 승인 쓰기 스텝의 확인창은 수락 (대조군)", (r["status"], r["reason"]), ("PASS", ""))
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
      ("ERROR", qa_run.REASON_SESSION.format("만료"), True))


# 세션 만료 안내에 실행 중 최근 막은 요청 (R3-14, A-07)
class StubRoute:
    def abort(self, *a):
        pass


class LoginPage(StubPage):
    url = "http://fake.local/login"

    def inner_text(self, sel):
        return "로그인이 필요합니다"


fr = FakeRunner()
fr._block(StubRoute(), "POST", "http://fake.local/api/auth/refresh", "")
run_fake([GOTO, {"action": "expect_visible", "selector": "#a"}], runner=fr)      # 다음 케이스 시작
t("recent_blocked: 케이스 시작 때 비우지 않음 · 경로부터", fr.recent_blocked, ["POST /api/auth/refresh"])
for p in ("/a", "/b", "/c", "/a"):
    fr._block(StubRoute(), "POST", "http://fake.local" + p, "")
t("recent_blocked: 최근 3줄 · 같은 줄은 뒤로", fr.recent_blocked, ["POST /b", "POST /c", "POST /a"])
fr.recent_blocked = ["POST /api/auth/refresh"]
fr.page, fr.login_check = LoginPage(), {"body_contains": "로그인이 필요합니다"}
try:
    fr._check_session()
    t("세션 만료 사유에 직전에 막은 요청", "만료 안 됨", "SessionExpired")
except qa_run.SessionExpired as e:
    s, reason = FS(e, [], set(), [], [])
    t("세션 만료 사유에 직전에 막은 요청",
      (s, "직전에 막은 요청: POST /api/auth/refresh - 로그인 유지 요청이면 다시 로그인해도 같은 결과가 납니다." in reason,
       qa_run.REASON_SESSION_BLOCKED.format("POST /api/auth/refresh") in reason), ("ERROR", True, True))
fr2 = FakeRunner()
fr2.page, fr2.login_check = LoginPage(), {"body_contains": "로그인이 필요합니다"}
try:
    fr2._check_session()
except qa_run.SessionExpired as e:
    t("막은 요청이 없으면 세션 만료 사유 그대로", "직전에 막은 요청" in str(e), False)
t("분석 · 로그 요청(ignore)은 recent_blocked 에 안 넣음", (lambda r: (
    r._block(StubRoute(), "POST", "https://www.google-analytics.com/g/collect", ""), r.recent_blocked)[1])(
    FakeRunner()), [])
info = {"at": 2, "total": 2, "case": "K2", "why": "session", "note": fr.session_note()}
t("리포트 중단 알림에 막은 요청 경로", "POST /api/auth/refresh" in report._aborted_html({"aborted_info": info}, []),
  True)
t("write:false 전용 사유에 설정 키 없음 (조회 · 검증 누락 둘 다 열어 둠)",
  ("allow_post" in qa_run.REASON_WRITE_FALSE_SENT, "조회 요청인지" in qa_run.REASON_WRITE_FALSE_SENT),
  (False, True))
t("로그인 창 같은 사이트 판정은 gates.site_of 하나 (옛 _site 없음)", hasattr(qa_run, "_site"), False)

# 게이트
r = qa_run.run_case(FakeRunner(), {"id": "K", "title": "t", "steps": [{"action": "click", "selector": "text=발송"}]},
                    ["text=발송"], True)
t("게이트: blocklist", (r["status"], r.get("blocked_by")), ("BLOCKED", "blocklist"))
r = run_fake([GOTO, {"action": "fill", "selector": "#q", "value": "x"}])
t("게이트: 쓰기 선언 누락", (r["status"], r.get("blocked_by"), r["reason"]),
  ("BLOCKED", "undeclared", "쓰기 선언 누락 - 스텝 2 'fill #q (값 1자)' 이 쓰기 동작"))
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
  (r["status"], r["reason"]), ("ERROR", "판정 불가 - 안전망 개입(확인창 취소)으로 화면 흐름이 실제와 다름. 이후 결과: 다름"))
r = run_fake([GOTO, CLICK, {"action": "expect_dialog", "contains": "다른 문구", "timeout": 30}])
t("확인창: expect_dialog 문구 불일치 = 실패", (r["status"], r["reason"].startswith("알럿 문구 불일치")), ("FAIL", True))
r = run_fake([GOTO, {"action": "expect_text", "selector": "#a", "contains": "x", "_dialog": "삭제?",
                     "_raise": qa_run.AssertFail("다름")}])
t("확인창: 판정 스텝 도중 취소 + 판정 실패 = 점검 필요",
  (r["status"], r["reason"]), ("ERROR", "판정 불가 - 안전망 개입(확인창 취소)으로 화면 흐름이 실제와 다름. 이후 결과: 다름"))
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
  ("ERROR", "조건 확인 불가 - 안전망 개입(요청 차단)으로 화면 흐름이 실제와 다름. 막은 요청: POST /api/list · 이후 결과: 0건"))
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
    # 02 의 downloads 삭제도 3개 제한에 같이 센다 (R3-26): downloads 1 + 실행 폴더 2 = 3. 08 은 다음 실행 때
    t("정리: downloads 포함 3개만 지움", sorted(x[-2:] for x in removed), ["05", "06"])
    t("정리: 보존 대상은 남음", left, ["01", "02", "03", "04", "07", "08", "09", "10", "11"])
    t("정리: 개발 전달 근거는 trace 를 남기고 내려받은 파일만 지움",
      ((d2 / "traces").exists(), (d2 / "downloads").exists()), (True, False))
    t("정리: 이름 바꾼 임시 폴더가 남지 않음", any(p.name.startswith("_trash_") for p in root.iterdir()), False)
    removed = qa_run.prune_outputs(root, "p", keep=2, protect=set())
    t("정리: 다음 실행 때 남은 08 을 지움 (downloads 는 이미 없음)", [x[-2:] for x in removed], ["08"])

    root2 = tmp / "out2"
    root2.mkdir()
    for i in range(1, 8):
        make_run(root2, i)
    t("정리: 한 번에 최대 3개", len(qa_run.prune_outputs(root2, "p", keep=2, protect=set())), 3)

    # n_dev 실행 5개(downloads 있음) + 지울 실행 2개: 한 번에 지우는 폴더 합계 3개 이하 (R3-26)
    root3 = tmp / "out3"
    root3.mkdir()
    devs = [make_run(root3, i, meta={"n_dev": 1}, dirs=("downloads",)) for i in range(1, 6)]
    make_run(root3, 6)
    make_run(root3, 7)
    for i in range(8, 10):
        make_run(root3, i)
    removed = qa_run.prune_outputs(root3, "p", keep=2, protect=set())
    dl_gone = sum(1 for d in devs if not (d / "downloads").exists())
    t("정리: n_dev downloads + 실행 폴더 합계 3개 이하", (dl_gone + len(removed) <= 3, dl_gone, len(removed)),
      (True, 3, 0))
    while True:
        more = qa_run.prune_outputs(root3, "p", keep=2, protect=set())
        if not more:
            break
        removed += more
    t("정리: 여러 번에 나눠 결국 다 지움 (downloads 5 · 실행 06 · 07)",
      (sum(1 for d in devs if not (d / "downloads").exists()), [x[-2:] for x in removed]), (5, ["06", "07"]))

    # -- trace 사본 --
    src = tmp / "T1.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("trace.trace", "\n".join(json.dumps(o) for o in [
            {"type": "context-options", "options": {"storageState": {
                "cookies": [{"name": "PHPSESSID", "value": "SECRET_COOKIE"}],
                "origins": [{"localStorage": [{"name": "access_token", "value": "SECRET_LS"}]}]}}},
            {"type": "before", "params": {"url": "https://a.co.kr/x?sid=SECRET_URL_SID"}},
            # 실제 trace 모양 (3차 리뷰 D-01): 로그 문장 속 주소, fragment 토큰
            {"type": "log", "callId": "call@1",
             "message": "navigating to \"https://a.co.kr/x?sid=SECRET_LOG_SID\", waiting until \"load\""},
            {"type": "before", "params": {"url": "https://a.co.kr/cb#access_token=SECRET_FRAG&state=1"}},
            {"type": "frame-snapshot", "snapshot": {"html": ["META", {"name": "csrf-token",
                                                                       "content": "SECRET_DOM_META"}]}},
        ]))

        def net(req, mime, sha1):
            return json.dumps({"type": "resource-snapshot", "snapshot": {
                "request": req, "response": {"headers": [], "content": {"mimeType": mime, "_sha1": sha1}}}})
        z.writestr("trace.network", "\n".join([
            json.dumps({"type": "resource-snapshot", "snapshot": {
                "request": {"url": "https://a.co.kr/api/me?access_token=SECRET_URL_QUERY",
                            "headers": [{"name": "X-Auth-Token", "value": "SECRET_CUSTOM_HEADER"},
                                        {"name": "Cookie", "value": "SECRET_COOKIE2"},
                                        {"name": "Referer", "value": "https://a.co.kr/x?sid=SECRET_REFERER_SID"}],
                            # 이름이 sid · ticket 이어도 값은 전부 지운다
                            "queryString": [{"name": "sid", "value": "SECRET_QS_SID"},
                                            {"name": "ticket", "value": "SECRET_QS_TICKET"}],
                            "postData": {"_sha1": "req1.dat", "text": "pw=SECRET_PW"}},
                "response": {"headers": [{"name": "Set-Cookie", "value": "SECRET_SETCOOKIE"}],
                             "content": {"mimeType": "application/json", "_sha1": "resp1.json"}}}}),
            # HTML 조각을 돌려주는 비동기 요청 (jQuery .load · fetch). 요청 종류 키는 trace 에 없다
            net({"url": "https://a.co.kr/list.php", "headers": [{"name": "X-Requested-With",
                                                               "value": "XMLHttpRequest"}]}, "text/html", "xhr1.html"),
            net({"url": "https://a.co.kr/part.php", "headers": [{"name": "sec-fetch-dest", "value": "empty"}]},
                "text/html", "fetch1.html"),
            # 실제 trace 의 fetch 요청 헤더 모양 (Sec-Fetch-* 없음, Upgrade-Insecure-Requests 없음)
            net({"url": "https://a.co.kr/frag.php", "headers": [{"name": "Accept", "value": "*/*"}]},
                "text/html; charset=utf-8", "fetch2.html"),
            # 페이지 이동(문서) 요청의 HTML 은 남는다
            net({"url": "https://a.co.kr/x", "headers": [{"name": "Upgrade-Insecure-Requests", "value": "1"}]},
                "text/html", "doc1.html"),
        ]))
        z.writestr("resources/resp1.json", "SECRET_RESP_BODY")
        z.writestr("resources/req1.dat", "SECRET_PW2")
        z.writestr("resources/xhr1.html", "SECRET_XHR_HTML")
        z.writestr("resources/fetch1.html", "SECRET_FETCH_HTML")
        z.writestr("resources/fetch2.html", "SECRET_FETCH2_HTML")
        z.writestr("resources/doc1.html", "DOC_HTML")
        z.writestr("resources/shot.jpeg", "IMG")
    qa_run.strip_trace(src)
    dst = src.with_name("T1_전달용.zip")
    with zipfile.ZipFile(dst) as z:
        names = z.namelist()
        blob = b"".join(z.read(n) for n in names)
    gone = ["SECRET_COOKIE", "SECRET_LS", "SECRET_URL_SID", "SECRET_URL_QUERY", "SECRET_CUSTOM_HEADER",
            "SECRET_COOKIE2", "SECRET_PW", "SECRET_SETCOOKIE", "SECRET_RESP_BODY", "SECRET_PW2",
            "SECRET_LOG_SID", "SECRET_FRAG", "SECRET_REFERER_SID", "SECRET_QS_SID", "SECRET_QS_TICKET",
            "SECRET_XHR_HTML", "SECRET_FETCH_HTML", "SECRET_FETCH2_HTML"]
    t("trace 사본: 인증값 · 쿼리 · 본문 지움", [s for s in gone if s.encode() in blob], [])
    t("trace 사본: 화면 스냅샷(DOM)은 남음 (안내문에 명시)", b"SECRET_DOM_META" in blob, True)
    t("trace 사본: 화면 캡처 · 문서 HTML 은 남음", ("resources/shot.jpeg" in names, "resources/doc1.html" in names),
      (True, True))
    t("trace 사본: 주소의 경로 · 쿼리 이름은 남음 (값만 가림)",
      (b"navigating to \\\"https://a.co.kr/x?sid=***\\\"" in blob, b"#access_token=***&state=***" in blob,
       b'"name": "ticket", "value": "***"' in blob), (True, True, True))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# -- 콘솔 끝 요약 (R3-28) ----------------------------------------------
lines = qa_run.console_summary([
    {"id": "1", "status": "PASS"}, {"id": "2", "status": "PASS", "get_suspects": ["GET a"]},
    {"id": "3", "status": "FAIL"}, {"id": "4", "status": "ERROR"}, {"id": "5", "status": "FLAKY"},
    {"id": "6", "status": "NA", "get_suspects": ["GET b", "GET c"]}, {"id": "7", "status": "BLOCKED"},
    {"id": "8", "status": "SKIPPED"}, {"id": "9", "status": "SKIPPED"}])
t("콘솔 요약: 결과 줄에 차단 · 승인 대기 · 전체, GET 쓰기 의심 줄 (케이스 수)", lines, [
    "[*] 결과: 통과 2 / 실패 1 / 점검 필요 1 / 불안정 1 / 조건 미충족 1 / 차단 1 / 승인 대기 2 / 전체 9",
    "[!] GET 쓰기 의심 2건 - 운영 데이터가 바뀌었을 수 있습니다. 담당 기획자에게 알리고 이 스펙은 다시 돌리지 마세요."])
t("콘솔 요약: 의심 없으면 한 줄", len(qa_run.console_summary([{"id": "1", "status": "PASS"}])), 1)
note = qa_run.REASON_SESSION_BLOCKED.format("POST /api/auth/refresh")
msg = qa_run.SESSION_AT.format("http://x/login") + " / " + note
t("세션 중단 콘솔: 앞 줄에서 '직전에 막은 요청' 뺌 (끝 안내에 한 번만)",
  qa_run._without_note(msg, note), "(중단 시점 주소: http://x/login)")
t("세션 중단 콘솔: 막은 요청 없으면 그대로", qa_run._without_note("(중단 시점 주소: a)", ""), "(중단 시점 주소: a)")
t("긴 중단 사유는 … 로 자름", (qa_run._clip("가" * 130, 120), qa_run._clip("짧음", 120)), ("가" * 120 + "…", "짧음"))


# -- 리포트 색 · 할 일 · 탭 (R3-29 · R3-33 · R3-34) ---------------------
def card(status, **kw):
    r = dict({"id": "C1", "title": "t", "status": status, "kind": "defect", "fix": "", "steps": [],
              "reason": "", "requirement": "", "source": "", "duration": 1, "shots": []}, **kw)
    return report._case_block(r, {}, "s.json")


t("치명 태그 빨강은 실패일 때만",
  {s: "k-red" in card(s, severity="치명") for s in ("FAIL", "PASS", "ERROR", "FLAKY", "NA")},
  {"FAIL": True, "PASS": False, "ERROR": False, "FLAKY": False, "NA": False})
c = card("FAIL", unclean=True, unclean_inputs=["[QA]x"])
t("운영 데이터 미정리 칸은 주황(unclean), 빨강 reason 아님",
  ('<dd class="unclean">정리되지 않음' in c, '<dd class="reason">정리되지 않음' in c), (True, False))
todo = report._todo_html([fail_res("A", unclean=True)], [], [], 0)
t("할 일: 운영 데이터 미정리는 주황", ('class="todo red"' in todo, "운영 데이터 미정리 1건" in todo), (False, True))
c = card("PASS", get_suspects=["GET http://h/api/toggle?sid=s ('toggle')"])
t("GET 쓰기 의심: 카드 머리줄 주황 태그 · 안내 문구",
  ('<span class="ktag k-warn">GET 쓰기 의심</span>' in c.split('class="cbody"')[0], "GET 쓰기 의심 주소 막아줘" in c),
  (True, True))
sus_res = [fail_res("G1", status="PASS", get_suspects=["GET http://h/a?x=1 ('toggle')", "GET http://h/b ('save')"]),
           fail_res("G2", status="PASS"), fail_res("G3", status="NA", get_suspects=["GET http://h/c?token=t ('del')"])]
blk = html.unescape(report._warn_blocks([], sus_res))
warn_lis = blk.split('id="warn"', 1)[1].split("</details>")[0].count("<li>")
t("GET 쓰기 의심: #warn 목록 케이스마다 한 줄 (케이스 ID · 주소 · 외 N건 · 토큰 가림)",
  (warn_lis, "G1</a> · GET http://h/a?x=1 ('toggle') 외 1건" in blk, "token=***" in blk, "확인할 경고 2건" in blk),
  (2, True, True, True))
todo = report._todo_html(sus_res, [], [], 0)
t("할 일: GET 쓰기 의심은 #warn 으로", ('href="#warn"' in todo and "GET 쓰기 의심 2건" in todo), True)
t("할 일: 중단 실행 첫 항목 (세션 만료 = 다시 로그인 후 실행)",
  report._todo_html([fail_res("A", status="ERROR")], [], [], 0, True,
                    {"aborted_info": {"why": "session"}}).split("</b>", 1)[1].startswith(
      '<a class="todo" href="#abort">다시 로그인 후 실행</a>'), True)
t("할 일: 중단 실행 첫 항목 (그 밖 = 다시 실행 필요)",
  report._todo_html([], [], [], 0, True, {"aborted_info": {"why": "error"}}).split("</b>", 1)[1].startswith(
      '<a class="todo" href="#abort">다시 실행 필요</a>'), True)
t("중단 알림에 이동 대상 id · 120자 넘으면 …",
  (lambda h: ('id="abort"' in h, "가" * 120 + "…" in h))(report._aborted_html(
      {"aborted_info": {"at": 1, "total": 3, "case": "K", "why": "error", "msg": "가" * 130}}, [])), (True, True))
t("세션 만료 알림 · 카드 사유가 같은 안내 문장",
  ("login_qa.bat 으로 다시 로그인한 뒤 실행합니다" in report._aborted_html(
      {"aborted_info": {"at": 1, "total": 3, "case": "K", "why": "session"}}, []),
   "login_qa.bat 으로 다시 로그인한 뒤 실행합니다" in qa_run.REASON_SESSION), (True, True))
tmp2 = Path(tempfile.mkdtemp(prefix="qa_rep_"))
try:
    spec = {"meta": {"project": "p", "base_url": "http://h"}, "cases": [{"id": "A"}, {"id": "B"}]}
    rs = [dict(fail_res("A", status="ERROR", reason=qa_run.REASON_SESSION.format("(중단 시점 주소: http://h/login)")),
               duration=1, shots=[])]
    html_ = report.build_report(spec, rs, [], tmp2, False, run_meta={
        "aborted": True, "aborted_info": {"at": 1, "total": 2, "case": "A", "why": "session", "msg": "x"}}).read_text(
        encoding="utf-8")
    tabs = html_.split('<nav class="tabs"', 1)[1].split("</nav>")[0]
    t("중단 실행: 개발 탭 숫자 '보류' (0 아님)", ('개발 전달용<span class="cnt">보류</span>' in tabs,
                                        '개발 전달용<span class="cnt">0</span>' in tabs), (True, False))
    t("중단 실행: 할 일 첫 항목 '다시 로그인 후 실행'",
      html_.split('<div class="todoline"><b>할 일</b>', 1)[1].startswith('<a class="todo" href="#abort">다시 로그인 후 실행'),
      True)
    t("리포트 어디에도 '요청 차단로' 없음 · .step-skip 규칙 없음", ("요청 차단로" in html_, ".step-skip" in html_),
      (False, False))
finally:
    shutil.rmtree(tmp2, ignore_errors=True)


if __name__ == "__main__":
    fails = [c for c in CASES if not c[1]]
    for name, ok, got, want in CASES:
        if not ok:
            print("  [실패] {} - 실제 {!r} / 기대 {!r}".format(name, got, want))
    print("runner 단위 테스트 {}건 중 실패 {}건".format(len(CASES), len(fails)))
    sys.exit(1 if fails else 0)
