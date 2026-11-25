"""qa_run.py 의 브라우저 없는 부분 단위 테스트:  py qa_auto/tests/test_runner.py

지난 실행 정리(prune) · 비교 표시(mark_trend) · 비교 기준 · trace 사본 · 폴더 이름.
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
