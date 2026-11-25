"""gates.py 단위 테스트. 브라우저 없이 돈다:  py qa_auto/tests/test_gates.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gates import (is_write_step, static_block_hit, runtime_block_hit, runtime_write_hint,  # noqa: E402
                   selector_texts, text_mismatch, count_mismatch, is_absence_check,
                   post_allowed, site_of, spec_errors, spec_warnings, path_list_errors,
                   url_block_hit, effective_blocklist, ignored_request, get_write_suspect,
                   W_UNKNOWN_REQ, W_NO_FIX, W_NO_AREA, W_APPROVED_FIRST_GOTO, W_SOFT_POSITION,
                   W_ABSENCE_NO_ANCHOR, W_PRE_NO_ANCHOR, W_BROAD_ANCHOR, W_SHORT_BLOCK, W_BLOCKLIST_OFF)

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

# allow_post · ignore_requests 정규식: 넓으면 저장 · 삭제 주소까지 열림 (R3-10)
for rx in ["re:/api/.*", "re:/admin/.+", "re:/admin/goods/.*\\.do", "re:/[a-z/]+", "re:/admin/[^/]+"]:
    t("allow_post 넓은 정규식 거부 " + rx, len(path_list_errors("allow_post", [rx])), 1)
    t("ignore_requests 도 같은 규칙 " + rx, len(path_list_errors("ignore_requests", [rx])), 1)
for rx in ["re:/api/goods/search(/\\d+)?", "re:/admin/goods/(list|search)\\.do"]:
    t("allow_post 좁은 정규식 허용 " + rx, path_list_errors("allow_post", [rx]), [])

# 무시 목록 · 호스트 형식 (R3-11)
BS = site_of("https://www.x.co.kr")
t("channel.io 는 무시 아님 (화면을 그리는 위젯)", ignored_request("https://api.channel.io/x"), False)
t("facebook.com/tr 는 무시", ignored_request("https://www.facebook.com/tr/?id=1&ev=PageView"), True)
t("facebook.com/tr (슬래시 없음) 도 무시", ignored_request("https://www.facebook.com/tr?id=1"), True)
t("facebook.com/plugins 는 무시 아님", ignored_request("https://www.facebook.com/plugins/like.php"), False)
t("호스트 형식 일치", ignored_request("https://collect.ext.com/v1/e", ["//collect.ext.com/v1/e"], BS), True)
t("호스트 형식 다른 호스트 불일치", ignored_request("https://other.com/v1/e", ["//collect.ext.com/v1/e"], BS), False)
t("호스트 형식 같은 호스트 다른 경로 불일치",
  ignored_request("https://collect.ext.com/v2/e", ["//collect.ext.com/v1/e"], BS), False)
t("호스트 정규식 형식", ignored_request("https://collect.ext.com/v1/a7", ["re://collect.ext.com/v1/a\\d"], BS), True)
t("호스트 없는 항목은 검수 사이트에만",
  ignored_request("https://collect.ext.com/log/view.do", ["/log/view.do"], BS), False)
t("호스트 없는 항목은 검수 사이트 하위 도메인 포함",
  ignored_request("https://admin.x.co.kr/log/view.do", ["/log/view.do"], BS), True)
EXT = ["//ext.api.com/front/boot"]
t("allow_post 호스트 형식은 그 호스트 POST 만", post_allowed("POST", "https://ext.api.com/front/boot", EXT, BS), True)
t("allow_post 호스트 형식이 검수 사이트 같은 경로를 열지 않음",
  post_allowed("POST", "https://www.x.co.kr/front/boot", EXT, BS), False)
t("allow_post 호스트 없는 항목이 외부 같은 경로를 열지 않음",
  post_allowed("POST", "https://ext.api.com/front/boot", ["/front/boot"], BS), False)
t("allow_post 호스트 형식 경로 규칙 그대로 ('/' 거부)", len(path_list_errors("allow_post", ["//ext.api.com/"], BS)), 1)
t("ignore_requests 외부 호스트는 '/' 허용", path_list_errors("ignore_requests", ["//collect.ext.com/"], BS), [])
t("ignore_requests 검수 사이트 호스트면 '/' 거부",
  len(path_list_errors("ignore_requests", ["//www.x.co.kr/"], BS)), 1)
t("ignore_requests 외부 호스트도 넓은 정규식 거부",
  len(path_list_errors("ignore_requests", ["re://collect.ext.com/.*"], BS)), 1)
t("호스트를 못 읽는 항목 거부", len(path_list_errors("allow_post", ["///front/boot"], BS)), 1)
t("spec_errors 는 base_url 로 외부 호스트를 가림",
  spec_errors({"meta": {"base_url": "https://www.x.co.kr", "ignore_requests": ["//collect.ext.com/"]}, "cases": []}), [])

# 글자 아닌 blocklist 는 스크립트 · 스타일 · 글꼴 요청에 안 씀 (R3-19)
t("blocklist 'send' 가 스크립트 sendbird.min.js 통과",
  url_block_hit("https://x.co.kr/js/sendbird.min.js", ["send"], "script"), None)
t("blocklist 'send' 가 스타일 통과", url_block_hit("https://x.co.kr/css/send.css", ["send"], "stylesheet"), None)
t("blocklist 'del' 이 /admin/del.do GET(문서) 차단", url_block_hit("https://x.co.kr/admin/del.do", ["del"], "document"),
  "del")
t("blocklist 'del' 이 xhr 차단", url_block_hit("https://x.co.kr/admin/del.do?id=1", ["del"], "xhr"), "del")
w5 = spec_warnings({"blocklist": ["send", "withdraw", "로그아웃", "text=발송", "text*=결"], "cases": []})
t("글자 아닌 blocklist 5글자 미만 경고 (text= 류 제외)", [x.split(":", 1)[0] for c, x in w5 if c == W_SHORT_BLOCK],
  ["blocklist 'send'", "blocklist '로그아웃'"])

# 넓은 anchor (R3-13)
w6 = spec_warnings({"cases": [{"id": "H", "kind": "improve", "anchor": "body", "steps": [
    {"action": "expect_visible", "selector": "#x"}]}]})
t("케이스 anchor body 경고 1건", [c for c, _ in w6], [W_BROAD_ANCHOR])
w7 = spec_warnings({"cases": [{"id": "I", "kind": "improve", "steps": [
    {"action": "expect_hidden", "selector": "#x", "anchor": "#wrap"},
    {"action": "expect_hidden", "selector": "#y", "anchor": "#wrap"}]}]})
t("스텝 anchor #wrap 경고 (같은 값은 한 번)", [c for c, _ in w7], [W_BROAD_ANCHOR])
t("표 헤더 anchor 는 경고 없음", spec_warnings({"cases": [{"id": "J", "kind": "improve", "steps": [
    {"action": "expect_hidden", "selector": "#x", "anchor": "table.goods thead"}]}]}), [])

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
# blocklist_off 오타는 멈추지 않고 경고, 그 항목은 무시(계속 막음) (R3-20)
OFF_BAD = {"meta": {"blocklist_off": ["text*=삭제"]}, "cases": []}
t("blocklist_off 오타는 errors 0", spec_errors(OFF_BAD), [])
t("blocklist_off 오타는 경고 1건", [c for c, _ in spec_warnings(OFF_BAD)], [W_BLOCKLIST_OFF])
t("blocklist_off 오타 항목은 무시 (내장 목록 그대로)", effective_blocklist([], True, ["text*=삭제"]),
  effective_blocklist([], True))
t("blocklist_off 접두어 없는 '발주' 는 text*=발주 로 받음",
  ("text*=발주" in effective_blocklist([], True, ["발주"]), spec_warnings({"meta": {"blocklist_off": ["발주"]}})),
  (False, []))
t("blocklist_off 일괄삭제 해제 시 띄어쓴 항목도 같이",
  [p for p in effective_blocklist([], True, ["text*=일괄삭제"]) if "일괄" in p], [])
t("blocklist_off 띄어쓴 일괄 삭제 해제 시 붙여 쓴 항목도 같이",
  [p for p in effective_blocklist([], True, ["일괄 삭제"]) if "일괄" in p], [])
t("blocklist_off 다른 항목은 그대로", "text*=발송" in effective_blocklist([], True, ["text*=일괄삭제"]), True)
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
