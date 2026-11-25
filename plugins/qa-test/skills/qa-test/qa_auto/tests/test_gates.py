"""gates.py 단위 테스트. 브라우저 없이 돈다:  py qa_auto/tests/test_gates.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gates import (is_write_step, static_block_hit, runtime_block_hit, runtime_write_hint,  # noqa: E402
                   selector_texts, text_mismatch, count_mismatch, is_absence_check,
                   post_allowed, site_of, spec_errors, spec_warnings, path_list_errors,
                   url_block_hit, effective_blocklist, ignored_request, get_write_suspect,
                   W_UNKNOWN_REQ, W_NO_FIX, W_NO_AREA, W_APPROVED_FIRST_GOTO, W_SOFT_POSITION,
                   W_ABSENCE_NO_ANCHOR, W_PRE_NO_ANCHOR)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CASES = []


def t(name, got, want):
    CASES.append((name, got == want, got, want))


# 쓰기 판정
t("fill 은 쓰기", is_write_step({"action": "fill", "selector": "#q"}), True)
t("write:false 가 우선", is_write_step({"action": "fill", "selector": "#q", "write": False}), False)
t("저장 버튼 클릭은 쓰기", is_write_step({"action": "click", "selector": "text=저장"}), True)
t("label 힌트", is_write_step({"action": "click", "selector": "#a", "label": "승인"}), True)
t("힌트 없는 클릭은 읽기", is_write_step({"action": "click", "selector": "#tab2"}), False)
t("Enter 는 쓰기", is_write_step({"action": "press", "key": "Enter"}), True)
t("Escape 는 읽기", is_write_step({"action": "press", "key": "Escape"}), False)
t("goto 는 읽기", is_write_step({"action": "goto", "url": "x"}), False)

# 셀렉터 글자
t("text= 글자", selector_texts("text=알림톡 발송"), ["알림톡 발송"])
t("has-text 글자", selector_texts('button:has-text("저장")'), ["저장"])
t("role name 글자", selector_texts('role=button[name="삭제"]'), ["삭제"])

# blocklist (스펙 문자열)
t("text= 는 완전일치만", static_block_hit({"action": "click", "selector": "text=발송 내역"}, ["text=발송"]), None)
t("text= 완전일치는 차단", static_block_hit({"action": "click", "selector": "text=발송"}, ["text=발송"]), "text=발송")
t("text*= 는 부분일치", static_block_hit({"action": "click", "selector": "#s", "label": "알림톡 발송"}, ["text*=발송"]), "text*=발송")
t("URL 패턴은 goto 주소도 봄", static_block_hit({"action": "goto", "url": "x?action=Withdraw"}, ["withdraw"]), "withdraw")
t("URL 패턴은 js 도 봄", static_block_hit({"action": "expect_js", "js": "sendSms()"}, ["sendsms"]), "sendsms")

# blocklist (화면 글자)
t("화면 글자 완전일치", runtime_block_hit(["승인", "", "", ""], ["text=승인"]), "text=승인")
t("화면 글자 부분은 text= 로 안 막음", runtime_block_hit(["승인 내역"], ["text=승인"]), None)
t("화면 글자 부분은 text*= 로 막음", runtime_block_hit(["SMS 발송하기"], ["text*=발송"]), "text*=발송")
t("공백 정규화", runtime_block_hit(["  저장\n"], ["text=저장"]), "text=저장")
t("화면 글자 쓰기 힌트", runtime_write_hint(["선택 항목 처리"]), "처리")
t("화면 글자 힌트 없음", runtime_write_hint(["상세보기"]), None)

# 네트워크
t("같은 사이트", site_of("https://admin.bluepharm.co.kr/x"), site_of("https://www.bluepharm.co.kr"))
t("다른 사이트", site_of("https://www.google-analytics.com/g") == site_of("https://www.bluepharm.co.kr"), False)
t("IP 는 주소 전체로 비교", site_of("http://127.0.0.1:8765/x"), "127.0.0.1")
t("localhost 와 127.0.0.1 은 다른 곳", site_of("http://localhost:1/") == site_of("http://127.0.0.1:1/"), False)
t("허용 POST (경로 전체일치)", post_allowed("POST", "https://a.co.kr/admin/list.do?x", ["/admin/list.do"]), True)
t("허용 안 된 POST", post_allowed("POST", "https://a.co.kr/admin/save.do", ["/admin/list.do"]), False)
t("allow_post 는 PUT 에 적용 안 됨", post_allowed("PUT", "https://a.co.kr/api/goods", ["/api/goods"]), False)
t("allow_post 는 DELETE 에 적용 안 됨", post_allowed("DELETE", "https://a.co.kr/api/goods", ["/api/goods"]), False)
t("allow_post 부분일치 안 함", post_allowed("POST", "https://a.co.kr/api/goods/7", ["/api/goods"]), False)
t("allow_post 는 쿼리를 안 봄", post_allowed("POST", "https://a.co.kr/api/write?ret=/api/search", ["/api/search"]), False)
t("allow_post 정규식 전체일치", post_allowed("POST", "https://a.co.kr/api/goods/search", ["re:/api/goods/search(/\\d+)?"]), True)
t("allow_post '/' 단독 거부", len(path_list_errors("allow_post", ["/"])), 1)
t("allow_post 쿼리 거부", len(path_list_errors("allow_post", ["?pn="])), 1)
t("allow_post 넓은 정규식 거부", len(path_list_errors("allow_post", ["re:.*"])), 1)
t("allow_post 정상 값", path_list_errors("allow_post", ["/admin/list.do", "re:/api/goods/search"]), [])
t("blocklist 주소 패턴은 요청 주소도 봄", url_block_hit("https://a/api/getwrite?what=Withdraw", ["withdraw"]), "withdraw")
t("blocklist 글자 패턴은 요청 주소에 안 씀", url_block_hit("https://a/send", ["text*=send"]), None)
t("승인 케이스는 내장 blocklist 추가", "text*=발송" in effective_blocklist([], True), True)
t("내장 blocklist 항목 해제", "text*=결제" in effective_blocklist([], True, ["text*=결제"]), False)
t("읽기 케이스는 내장 blocklist 없음", effective_blocklist(["x"], False), ["x"])
t("분석 도구 요청은 판정 무관", ignored_request("https://www.google-analytics.com/g/collect?v=2"), True)
t("ignore_requests 경로", ignored_request("https://a.co.kr/log/view.do", ["/log/view.do"]), True)
t("일반 요청은 판정 대상", ignored_request("https://a.co.kr/api/save"), False)
t("GET 쓰기 의심", get_write_suspect("https://a.co.kr/api/getwrite?what=toggle") is not None, True)
t("목록 이동은 의심 아님", get_write_suspect("https://a.co.kr/goods/list.do?page=2"), None)

# 문구 판정
t("contains 정규화", text_mismatch("총  12\n건", {"contains": "총 12 건"}), None)
t("equals 다름", text_mismatch("저장되었습니다.", {"equals": "저장되었습니다"}) is not None, True)
t("matches", text_mismatch("2026-11-24", {"matches": r"^\d{4}-\d{2}-\d{2}$"}), None)
t("not_contains", text_mismatch("반품 불가", {"not_contains": "반품"}) is not None, True)
t("count max", count_mismatch(3, {"max": 2}) is not None, True)
t("부재 판정 hidden", is_absence_check({"action": "expect_hidden"}), True)
t("부재 판정 count:0", is_absence_check({"action": "expect_count", "count": 0}), True)
t("부재 판정 아님", is_absence_check({"action": "expect_count", "count": 3}), False)
t("부재 판정 not_contains", is_absence_check({"action": "expect_text", "not_contains": "x"}), True)

# 스펙 검증
bad = {"cases": [{"id": "A", "kind": "improvement", "severity": "치명적", "source": "", "steps": []}]}
t("kind 오타 · severity 오타 · source 빈 결함", len(spec_errors(bad)), 2)
bad2 = {"cases": [{"id": "B", "source": "", "steps": []}]}
t("source 빈 defect", len(spec_errors(bad2)), 1)
good = {"cases": [{"id": "C", "kind": "improve", "source": "", "steps": []}]}
t("개선 요청은 source 없어도 됨", spec_errors(good), [])
w = spec_warnings({"cases": [{"id": "D", "source": "x", "fix": "f", "area": "a", "steps": [
    {"action": "expect_hidden", "selector": "#x"}]}]})
t("부재 판정 anchor 경고", [c for c, _ in w], [W_ABSENCE_NO_ANCHOR])
t("넓은 allow_post 는 실행 전 멈춤", len(spec_errors({"meta": {"allow_post": ["/a"]}, "cases": []})), 1)
t("없는 blocklist_off 항목", len(spec_errors({"meta": {"blocklist_off": ["text*=삭제"]}, "cases": []})), 1)
w2 = spec_warnings({"cases": [{"id": "E", "source": "x", "writes": True, "approved": True,
                               "steps": [{"action": "click", "selector": "#a"}]}]})
t("승인 쓰기 케이스 첫 스텝 goto 경고", W_APPROVED_FIRST_GOTO in [c for c, _ in w2], True)

# 경고 코드: 종류마다 코드 하나, 문장은 케이스 ID 로 시작 (리포트가 ID 를 떼어 묶는다)
w3 = spec_warnings({"requirements": [{"id": "R1"}], "cases": [
    {"id": "F", "source": "x", "req": ["R9"], "steps": [
        {"action": "click", "selector": "#a", "soft": True},
        {"action": "expect_text", "selector": "#b", "contains": "y", "precondition": True}]}]})
t("경고 코드 전부", sorted(c for c, _ in w3),
  sorted([W_UNKNOWN_REQ, W_NO_FIX, W_NO_AREA, W_SOFT_POSITION, W_PRE_NO_ANCHOR]))
t("경고 문장은 케이스 ID 로 시작", all(x.startswith("F: ") for _, x in w3), True)
t("경고 없음", spec_warnings({"cases": [{"id": "G", "kind": "improve", "steps": [
    {"action": "expect_hidden", "selector": "#x", "anchor": "#list"}]}]}), [])


if __name__ == "__main__":
    fails = [c for c in CASES if not c[1]]
    for name, ok, got, want in CASES:
        if not ok:
            print("  [실패] {} - 실제 {!r} / 기대 {!r}".format(name, got, want))
    print("gates 단위 테스트 {}건 중 실패 {}건".format(len(CASES), len(fails)))
    sys.exit(1 if fails else 0)
