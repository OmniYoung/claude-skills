"""
회귀 스펙 생성기. 리뷰어들이 재현에 쓴 스펙을 옮겨 왔고, 기대 결과는 run_regress.py 에 있다.
run_regress.py 가 실행할 때마다 임시 폴더에 새로 만든다(서버 포트가 실행마다 달라서).
저장소에는 스펙 JSON 을 두지 않는다.
"""

import json
from pathlib import Path

B = "http://127.0.0.1:8765"     # build() 가 실제 주소로 바꾼다
F = "{QA_ROOT}/fixtures"


def meta(project, base=B, **kw):
    m = {"project": project, "base_url": base, "qa_prefix": "[QA]", "sources": ["회귀 테스트"]}
    m.update(kw)
    return m


def case(cid, title, steps, **kw):
    c = {"id": cid, "title": title, "requirement": title, "source": "회귀", "writes": False,
         "approved": False, "steps": steps}
    c.update(kw)
    return c


def G(u="dev.html"):
    return {"action": "goto", "url": u}


SPECS = {}

# 개발자 리뷰 D2: dry-run(bat 1번)에서 서버 쓰기가 0이어야 한다
SPECS["dry"] = {"meta": meta("rg_dry"), "blocklist": ["withdraw", "text=발송"], "cases": [
    case("S1", "글자 없는 셀렉터(#approve)의 승인 버튼",
         [G(), {"action": "click", "selector": "#approve"},
          {"action": "expect_text", "selector": "#marker", "contains": "approved"}]),
    case("S2", "확인창 자동 수락으로 삭제",
         [G(), {"action": "click", "selector": "#del"},
          {"action": "expect_text", "selector": "#marker", "contains": "deleted"}]),
    case("S3", "expect_js 우회 cli+ck",
         [G(), {"action": "expect_js", "js": "()=>{document.querySelector('#save')['cli'+'ck']();return true}"},
          {"action": "expect_text", "selector": "#marker", "contains": "saved"}]),
    case("S4", "expect_js requestSubmit",
         [G(), {"action": "expect_js", "js": "()=>{document.getElementById('f').requestSubmit();return true}"}]),
    case("S6", "blocklist 가 goto 주소를 봄",
         [G("dev.html?action=withdraw"), {"action": "expect_text", "selector": "#marker", "contains": "withdrawn"}]),
    case("S7", "write:false 로 내린 저장 클릭",
         [G(), {"action": "click", "selector": "text=저장", "write": False},
          {"action": "expect_text", "selector": "#marker", "contains": "saved"}]),
    case("S8", "chains 진단이 쓰기를 다시 하지 않음",
         [G(), {"action": "click", "selector": "text=저장"},
          {"action": "expect_text", "selector": "#marker", "contains": "nonexistent"}], chains=True),
    case("S8b", "앞 케이스들이 쓰기를 남기지 않음 (goto 없이 이어서)",
         [{"action": "expect_text", "selector": "#marker", "not_contains": "saved", "anchor": "#title"}]),
    case("S9", "wait 가 알럿 기록을 비우지 않음",
         [G(), {"action": "click", "selector": "#alertbtn"}, {"action": "wait", "ms": 300},
          {"action": "expect_dialog", "contains": "완료"}]),
    case("S10", "낡은 셀렉터 expect_visible 은 점검 필요",
         [G(), {"action": "expect_visible", "selector": "#typo-not-exist", "timeout": 1500}]),
    case("S11", "낡은 셀렉터 expect_hidden 은 통과가 아님",
         [G(), {"action": "expect_hidden", "selector": "#typo-not-exist"}]),
    case("S12", "늦게 그려지는 요소 expect_visible",
         [G(), {"action": "click", "selector": "#lazy", "write": False},
          {"action": "expect_visible", "selector": "#result"}]),
]}

# 개발자 리뷰 D1: 전체 실행에서도 선언 없는 쓰기는 막힌다
SPECS["live"] = {"meta": meta("rg_live"), "cases": [
    case("N1", "writes 선언 없는 저장 클릭",
         [G(), {"action": "click", "selector": "text=저장"},
          {"action": "expect_text", "selector": "#marker", "contains": "saved"}]),
    case("N2", "글자 없는 셀렉터의 저장 클릭",
         [G(), {"action": "click", "selector": "#save"},
          {"action": "expect_text", "selector": "#marker", "contains": "never"}]),
    case("N3", "서버 쓰기 횟수 0",
         [G(), {"action": "expect_text", "selector": "#ls", "equals": "writes=0"}]),
]}

SPECS["block"] = {"meta": meta("rg_block"), "blocklist": ["text=승인", "text=저장"], "cases": [
    case("B1", "blocklist text=승인 인데 #approve 로 클릭",
         [G(), {"action": "click", "selector": "#approve", "write": True}],
         writes=True, approved=True, cleanup=[G()]),
    case("B2", "blocklist text=저장 인데 위치 셀렉터",
         [G(), {"action": "click", "selector": "button >> nth=1", "write": True}],
         writes=True, approved=True, cleanup=[G()]),
]}

SPECS["trace"] = {"meta": meta("rg_trace", cookies="cookies.json"), "cases": [
    case("T1", "제목 문구",
         [G(), {"action": "expect_text", "selector": "#title", "contains": "없는문구"}],
         area="머리말", fix="제목 오류: '{actual}' 로 노출 중 (SB: 없는문구)"),
    case("T2", "영역 없는 실패",
         [G(), {"action": "expect_text", "selector": "#marker", "contains": "없는문구"}]),
]}

SPECS["trend"] = {"meta": meta("rg_trend"), "cases": [
    case("P1", "P1", [G(), {"action": "expect_text", "selector": "#title", "contains": "없는문구"}]),
    case("P2", "P2", [G(), {"action": "expect_text", "selector": "#title", "contains": "리뷰"}]),
]}

SPECS["flaky"] = {"meta": meta("rg_flaky"), "requirements": [{"id": "REQ-1", "text": "완료 문구"}], "cases": [
    case("F1", "1차 실패 재실행 통과",
         [G("flaky.html"), {"action": "expect_text", "selector": "#t", "equals": "완료", "settle": 500}],
         req=["REQ-1"]),
]}

SPECS["allowpost_off"] = {"meta": meta("rg_post_off"), "cases": [
    case("A1", "POST 조회 - 허용 목록 없음",
         [G("search.html"), {"action": "click", "selector": "#search", "write": False},
          {"action": "expect_text", "selector": "#out", "contains": "결과"}]),
]}
SPECS["allowpost_on"] = {"meta": meta("rg_post_on", allow_post=["/api/search"]), "cases": [
    case("A2", "POST 조회 - 허용 목록 있음",
         [G("search.html"), {"action": "click", "selector": "#search", "write": False},
          {"action": "expect_text", "selector": "#out", "contains": "결과 3건"}]),
]}

SPECS["confirm"] = {"meta": meta("rg_confirm"), "cases": [
    case("C1", "삭제 확인창 문구만 확인 (취소)",
         [G(), {"action": "click", "selector": "#del", "write": False},
          {"action": "expect_dialog", "contains": "삭제하시겠습니까"}]),
    case("C2", "확인 없이 뜬 확인창은 점검 필요",
         [G(), {"action": "click", "selector": "#del", "write": False},
          {"action": "expect_text", "selector": "#marker", "contains": "none"}]),
]}

SPECS["approved"] = {"meta": meta("rg_approved"), "cases": [
    case("W1", "승인된 쓰기는 서버에 한 번 쓴다",
         [G(), {"action": "click", "selector": "#save", "write": True},
          {"action": "expect_text", "selector": "#marker", "contains": "saved"}],
         writes=True, approved=True, cleanup=[G()]),
    case("W2", "승인된 케이스여도 expect_js 쓰기는 막힘",
         [G(), {"action": "expect_js", "js": "()=>{document.querySelector('#save')['cli'+'ck']();return true}"}],
         writes=True, approved=True, cleanup=[G()]),
    case("W3", "승인된 쓰기 확인창 수락",
         [G(), {"action": "click", "selector": "#del", "write": True},
          {"action": "expect_text", "selector": "#marker", "contains": "deleted"}],
         writes=True, approved=True, cleanup=[G()]),
]}

# 자동화 기획자 리뷰 B1 · B4 (스킬 기본 픽스처)
SPECS["gate"] = {"meta": meta("rg_gate", base=F), "blocklist": ["text=발송"], "cases": [
    case("TC-101", "writes 표시 누락된 저장 클릭",
         [G("_smoke.html"), {"action": "click", "selector": "#save", "label": "저장"},
          {"action": "expect_dialog", "contains": "이미지 사이즈"}]),
    case("TC-102", "선언 없는 체크",
         [G("_smoke.html"), {"action": "check", "selector": "#use-y"},
          {"action": "expect_visible", "selector": "#popup-form"}]),
]}
SPECS["sess"] = {"meta": meta("rg_sess", base=F, login_check={"body_contains": "오류 버튼"}), "cases": [
    case("TC-201", "a", [G("_smoke.html")]),
    case("TC-202", "b", [G("_smoke.html")]),
]}

# 전문 QA 리뷰 A1 · A2 · A6 · A7
SPECS["probe"] = {"meta": meta("rg_probe", base=F), "blocklist": ["text=발송"], "cases": [
    case("P-1", "writes:false 인데 저장 클릭",
         [G("_smoke.html"), {"action": "click", "selector": "#save", "label": "저장"},
          {"action": "expect_dialog", "contains": "이미지"}]),
    case("P-2", "오타 셀렉터로 부재 확인",
         [G("_smoke.html"), {"action": "expect_hidden", "selector": "#chatt"},
          {"action": "expect_count", "selector": ".no-such-col", "count": 0}]),
    case("P-3", "숨은 입력칸 글자는 화면 글자가 아님",
         [G("_smoke.html"), {"action": "expect_text", "selector": "#popup-area", "not_contains": "연결 URL"}]),
]}
SPECS["probe_kind"] = {"meta": meta("rg_probe_kind", base=F), "cases": [
    case("P-4", "kind 오타", [G("_smoke.html")], kind="improvement", severity="치명적", source=""),
]}
SPECS["probe2"] = {"meta": meta("rg_probe2", login_check={"body_contains": "로그인이 필요합니다"},
                                residue_check=[G("p.html"), {"action": "expect_visible", "selector": "#t"}]),
                   "cases": [
    case("Q-1", "늦게 그려지는 컬럼 count:0 (anchor 없음)",
         [G("p.html"), {"action": "expect_count", "selector": ".col-owner", "count": 0}]),
    case("Q-1b", "늦게 그려지는 컬럼 count:0 (anchor 있음)",
         [G("p.html"), {"action": "expect_count", "selector": ".col-owner", "count": 0, "anchor": "#grid td"}]),
    case("Q-2", "숨은 문구 not_contains",
         [G("p.html"), {"action": "expect_text", "selector": "#notice", "not_contains": "반품 불가"}]),
    case("Q-3", "늦게 뜨는 저장 알럿",
         [G("p.html"), {"action": "click", "selector": "#save", "label": "저장"},
          {"action": "expect_dialog", "contains": "저장되었습니다"}],
         writes=True, approved=True, cleanup=[{"action": "wait", "ms": 10}]),
    case("Q-5", "쓰기 중 세션 만료",
         [G("p.html"), {"action": "click", "selector": "#save", "label": "저장"},
          {"action": "click", "selector": "#go", "write": False},
          {"action": "expect_visible", "selector": "#t"}],
         writes=True, approved=True, cleanup=[G("p.html")]),
    case("Q-6", "중단 뒤 케이스", [G("p.html")]),
]}

# -- 2차 리뷰 재현 (QA · 개발자) ---------------------------------
BP = "bypass.html"
CLEAN = [G(BP)]

# allow_post 는 POST 에만, 경로 전체일치로 (QA N1)
SPECS["ap_method"] = {"meta": meta("rg_ap_method", allow_post=["/api/goods/search", "/api/search"]), "cases": [
    case("R1a", "허용 POST 조회 뒤 모달 확인(PUT)",
         [G(BP), {"action": "click", "selector": "#search", "write": False},
          {"action": "expect_text", "selector": "#out", "contains": "결과 3건"},
          {"action": "click", "selector": "#modal-ok", "write": False}]),
    case("R1b", "아이콘 휴지통(DELETE)", [G(BP), {"action": "click", "selector": "#trash"}]),
    case("R1c", "쿼리에 허용 경로가 든 쓰기 POST", [G(BP), {"action": "click", "selector": "#ap"}]),
]}

# GET 쓰기: blocklist 주소는 막고, 나머지는 의심 신호 (QA N2 · 개발자 D-N7)
SPECS["getw"] = {"meta": meta("rg_getw"), "blocklist": ["withdraw"], "cases": [
    case("G1", "아이콘 링크 GET 쓰기", [G(BP), {"action": "click", "selector": "#toggle"}]),
    case("G2", "blocklist 주소의 링크", [G(BP), {"action": "click", "selector": "#out-link"}]),
    case("G3", "이미지 비컨 GET 쓰기", [G(BP), {"action": "click", "selector": "#legacy-ok", "write": False},
                                  {"action": "wait", "ms": 300}]),
]}

# 막은 뒤의 실패는 개발 전달이 아님 (개발자 D-N1 · QA N4)
SPECS["late"] = {"meta": meta("rg_late"), "cases": [
    case("L1", "늦게 나가는 조회 POST", [G(BP), {"action": "click", "selector": "#late"},
                                    {"action": "expect_text", "selector": "#out", "contains": "결과 3건",
                                     "settle": 2500}], area="점검", fix="L1 오류"),
    case("L2", "늦게 뜨는 확인창", [G(BP), {"action": "click", "selector": "#lateconfirm"},
                               {"action": "expect_text", "selector": "#out", "contains": "진행됨",
                                "settle": 2500}], area="점검", fix="L2 오류"),
    case("X1", "다른 사이트 조회 POST", [G(BP), {"action": "click", "selector": "#xsearch"},
                                   {"action": "expect_text", "selector": "#xout", "contains": "결과 3건",
                                    "anchor": "#title", "settle": 2000}], area="점검", fix="X1 오류"),
]}

# 승인 쓰기: 늦게 뜬 확인창은 다음 조작 전까지 수락 (개발자 D-N1)
SPECS["lateok"] = {"meta": meta("rg_lateok"), "cases": [
    case("D1", "늦게 뜨는 삭제 확인창", [G(BP), {"action": "click", "selector": "#del"},
                                   {"action": "expect_dialog", "contains": "삭제하시겠습니까"},
                                   {"action": "expect_text", "selector": "#marker", "contains": "deleted"}],
         writes=True, approved=True, cleanup=CLEAN),
]}

# 다른 창 · 앞 케이스의 요청 (개발자 D-N2)
SPECS["popup"] = {"meta": meta("rg_popup"), "cases": [
    case("PP1", "팝업 열기", [G(BP), {"action": "click", "selector": "#pop"}, {"action": "wait", "ms": 300}]),
    case("PP2", "팝업이 남아 있어도", [G(BP), {"action": "wait", "ms": 1600},
                                 {"action": "expect_visible", "selector": "#title"}]),
    case("PP3", "승인 쓰기 동안 팝업 쓰기 없음", [G(BP), {"action": "click", "selector": "#save"},
                                         {"action": "wait", "ms": 1600}],
         writes=True, approved=True, cleanup=CLEAN),
    case("K1", "뒤늦은 쓰기를 남기는 읽기", [G(BP), {"action": "click", "selector": "#bg"}]),
    case("K2", "바로 뒤 승인 쓰기", [G(BP), {"action": "click", "selector": "#save"}, {"action": "wait", "ms": 1500}],
         writes=True, approved=True, cleanup=CLEAN),
    case("K3", "goto 없이 시작하는 승인 쓰기", [{"action": "click", "selector": "#save"}],
         writes=True, approved=True, cleanup=CLEAN),
]}

# 글자 확인과 클릭 사이 경쟁 + 내장 blocklist (개발자 D-N3 · QA N7)
SPECS["race"] = {"meta": meta("rg_race"), "cases": [
    case("R1", "준비 중 -> 발송 버튼", [G(BP), {"action": "click", "selector": "#act", "write": True},
                                 {"action": "wait", "ms": 300}],
         writes=True, approved=True, cleanup=CLEAN),
]}
SPECS["race_off"] = {"meta": meta("rg_race_off", blocklist_off=["text*=발송"]), "cases": [
    case("R2", "내장 blocklist 해제 시 실행", [G(BP), {"action": "click", "selector": "#act", "write": True},
                                       {"action": "wait", "ms": 300}],
         writes=True, approved=True, cleanup=CLEAN),
]}

# 부재 판정은 관찰 구간 동안 (QA N3 · N5)
SPECS["absence"] = {"meta": meta("rg_absence"), "cases": [
    case("R3a", "늦게 그려지는 금지 문구 (anchor 없음)",
         [G(BP), {"action": "expect_text", "selector": "#notice", "not_contains": "반품 불가"}]),
    case("R3b", "늦게 그려지는 금지 문구 (anchor 있음)",
         [G(BP), {"action": "expect_text", "selector": "#notice", "not_contains": "반품 불가",
                  "anchor": "#tbl thead"}]),
    case("R4", "필터 뒤 늦게 빠지는 행", [G(BP), {"action": "click", "selector": "#filter", "write": False},
                                    {"action": "expect_count", "selector": "tr.soldout", "count": 0,
                                     "anchor": "#tbl thead"}]),
]}

# 같은 project 의 다른 스펙이 비교 기준을 가로채지 않음 (개발자 D-N6)
SPECS["m1"] = {"meta": meta("rg_shared"), "cases": [
    case("M1", "M1", [G(BP), {"action": "expect_text", "selector": "#title", "contains": "없는문구", "settle": 300}]),
]}
SPECS["m2"] = {"meta": meta("rg_shared"), "cases": [
    case("M2", "M2", [G(BP), {"action": "expect_visible", "selector": "#title"}]),
]}


def build(base):
    """서버 주소를 넣은 스펙들."""
    text = json.dumps(SPECS, ensure_ascii=False).replace(B, base)
    return json.loads(text)


def write(out_dir, base):
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = build(base)
    for k, v in specs.items():
        (out_dir / (k + ".json")).write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "cookies.json").write_text(json.dumps(
        {"domain": "127.0.0.1", "cookies": [{"name": "ADMIN_SESSION", "value": "SECRET123"}]}),
        encoding="utf-8")
    return specs


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / "specs"
    print("스펙 {}개 생성: {}".format(len(write(target, B)), target))
