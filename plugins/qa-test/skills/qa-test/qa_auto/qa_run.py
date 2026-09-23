"""
QA 자동화 러너 - spec.json을 읽어 실제 사이트에서 테스트를 실행하고 리포트를 생성한다.

사용법:
  py <skill>/qa_auto/qa_run.py --init .            작업 폴더(qa_auto/) 생성
  py <skill>/qa_auto/qa_run.py qa_auto/specs/xxx.json
  py <skill>/qa_auto/qa_run.py ... --dry-run       쓰기 스텝 전부 건너뜀 (셀렉터 검증용)
  py <skill>/qa_auto/qa_run.py ... --headed        브라우저 띄워서 눈으로 확인
  py <skill>/qa_auto/qa_run.py ... --case TC-003   특정 케이스만

산출물 위치:
  이 스크립트는 스킬 폴더에 아무것도 쓰지 않는다. 스펙 파일 위치로 작업 폴더를 정한다.
  <작업폴더>/specs/xxx.json 이면 <작업폴더> 아래 output/ 과 fixtures/ 를 쓴다.
  덕분에 팀원 각자가 자기 프로젝트에서 같은 스킬을 공유해 쓸 수 있다.

안전장치:
  - 쓰기 스텝은 케이스에 approved:true 가 있어야만 실행된다 (없으면 SKIP)
  - blocklist 매칭 시 승인 여부와 무관하게 케이스 중단
  - 쓰기 케이스는 cleanup 스텝이 없으면 실행 거부
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).parent))
from report import build_report

# SKILL_DIR: 스킬에 번들된 스크립트·기본 픽스처가 있는 곳. 여기에는 아무것도 쓰지 않는다.
SKILL_DIR = Path(__file__).parent


def resolve_workspace(spec_path):
    """스펙 위치로 작업 폴더를 정한다. <ws>/specs/x.json 이면 <ws>, 아니면 스펙이 있는 폴더."""
    return spec_path.parent.parent if spec_path.parent.name == "specs" else spec_path.parent


def find_fixture(workspace, name):
    """작업 폴더의 fixtures 를 먼저 보고, 없으면 스킬 번들 기본 픽스처로 넘어간다."""
    for base in (workspace / "fixtures", SKILL_DIR / "fixtures"):
        f = (base / name).resolve()
        if f.exists():
            return f
    raise FileNotFoundError(
        "업로드 파일 없음: {} (찾은 곳: {}, {})".format(
            name, workspace / "fixtures", SKILL_DIR / "fixtures"))

# -- 쓰기 판정 --------------------------------------------------
WRITE_ACTIONS = {"fill", "select", "check", "uncheck", "upload"}
WRITE_HINTS = ["저장", "확인", "등록", "추가", "삭제", "수정", "전송", "발송", "적용", "완료"]
READ_ACTIONS = {
    "goto", "wait", "screenshot", "hover",
    "expect_visible", "expect_hidden", "expect_text",
    "expect_count", "expect_attr", "expect_dialog", "expect_no_dialog",
    "expect_js",
}

# expect_js 는 읽기 전용이어야 한다. 아래 패턴이 보이면 실행을 거부해
# 임의 JS로 쓰기 승인 게이트를 우회하는 길을 막는다.
JS_MUTATION_PATTERNS = [
    ".click(", ".submit(", ".focus(", "fetch(", "XMLHttpRequest", "navigator.send",
    ".value=", ".value =", ".innerHTML=", ".innerHTML =", ".remove(", ".setAttribute(",
    "localStorage", "sessionStorage", "document.cookie", "location=", "location.href",
    "dispatchEvent", "eval(",
]
ALL_ACTIONS = WRITE_ACTIONS | READ_ACTIONS | {"click"}

DEFAULT_VIEWPORT = {"width": 1600, "height": 900}


def is_write_step(step):
    """스텝이 서버에 쓰기를 하는지 판정. spec_schema.md의 '쓰기 판정 규칙' 구현."""
    action = step.get("action")
    if "write" in step:                      # 규칙 3,4 - 명시값이 최우선
        return bool(step["write"])
    if action in WRITE_ACTIONS:              # 규칙 1
        return True
    if action == "click":                    # 규칙 2 - 텍스트 힌트로 자동 승격
        target = "{} {}".format(step.get("selector", ""), step.get("label", ""))
        return any(hint in target for hint in WRITE_HINTS)
    return False


def hits_blocklist(step, blocklist):
    """blocklist 패턴에 걸리는 스텝인지. 걸리면 승인 여부 무관하게 차단."""
    target = "{} {} {}".format(
        step.get("selector", ""), step.get("label", ""), step.get("value", "")
    )
    for pattern in blocklist:
        needle = pattern.split("=", 1)[1] if pattern.startswith("text=") else pattern
        if needle and needle in target:
            return pattern
    return None


# -- 쿠키 ------------------------------------------------------

class SessionExpired(Exception):
    pass


def load_cookies(spec_path, rel, workspace):
    """{"domain": "...", "cookies": [{"name":..., "value":...}]} 포맷.

    경로는 스펙 파일 기준으로 먼저 찾고, 없으면 작업 폴더 기준으로 찾는다.
    덕분에 스펙에 "cookies.json" 한 줄만 써도 <작업폴더>/cookies.json 을 집어온다.
    """
    for base in (spec_path.parent, workspace):
        path = (base / rel).resolve()
        if path.exists():
            break
    else:
        path = (spec_path.parent / rel).resolve()
    if not path.exists():
        raise FileNotFoundError(
            "쿠키 파일 없음: {} (찾은 곳: {}, {})".format(rel, spec_path.parent, workspace))

    text = path.read_text(encoding="utf-8-sig").strip()
    if text.startswith("{"):
        return _cookies_from_json(text)
    return _cookies_from_devtools(text, path)


def _cookies_from_json(text):
    """{"domain": "...", "cookies": [{"name":..., "value":...}]} 포맷."""
    data = json.loads(text)
    return [
        {"name": c["name"], "value": c["value"], "domain": data["domain"], "path": "/"}
        for c in data["cookies"] if c.get("value")
    ]


def _cookies_from_devtools(text, path):
    """크롬 개발자도구 > Application > Cookies 의 표를 그대로 붙여넣은 탭 구분 텍스트.

    컬럼 순서: Name / Value / Domain / Path / Expires / Size / ... 뒤는 무시한다.
    값이 비었거나 컬럼이 모자란 줄은 건너뛴다. 헤더 줄도 자동으로 건너뛴다.
    """
    out, skipped = [], 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue               # 빈 줄과 안내 주석
        col = line.split("\t")
        if len(col) < 4:
            skipped += 1
            continue
        name, value, domain, cpath = (c.strip() for c in col[:4])
        if name.lower() == "name" and value.lower() == "value":
            continue                      # 헤더 줄
        if not name or not value or not domain:
            skipped += 1
            continue
        out.append({"name": name, "value": value,
                    "domain": domain, "path": cpath or "/"})
    if not out:
        raise ValueError(
            "쿠키를 한 건도 못 읽음: {}\n"
            "크롬 F12 > Application > Cookies 에서 표 전체를 선택해 복사한 뒤 "
            "그대로 붙여넣어야 한다 (탭 구분).".format(path))
    if skipped:
        print("[*] 쿠키 {}건 로드 (형식이 안 맞는 {}줄 건너뜀)".format(len(out), skipped))
    else:
        print("[*] 쿠키 {}건 로드".format(len(out)))
    return out


# -- 스텝 실행 --------------------------------------------------

class StepFailure(Exception):
    pass


class Runner:
    def __init__(self, page, spec, spec_path, shot_dir, workspace):
        self.page = page
        self.spec = spec
        self.spec_path = spec_path
        self.shot_dir = shot_dir
        self.workspace = workspace
        # {QA_ROOT} = 번들된 스크립트 폴더의 file:// URI. 자체 점검용 로컬 픽스처를 가리킬 때 쓴다.
        self.base_url = spec["meta"]["base_url"].replace(
            "{QA_ROOT}", SKILL_DIR.as_uri()).rstrip("/")
        self.qa_prefix = spec["meta"].get("qa_prefix", "")
        self.dialogs = []          # 직전 액션이 띄운 알럿 버퍼
        self.warnings = []
        self.shots = []
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.accept()

    def _check_session(self):
        """로그인이 풀렸는지 본다.

        세션이 만료되면 모든 케이스가 엉뚱한 이유로 줄줄이 실패해서 원인 파악이 오래 걸린다.
        첫 페이지에서 바로 잡아 중단시키는 편이 낫다.
        """
        chk = self.spec["meta"].get("login_check")
        if not chk:
            return
        url_hit = chk.get("url_contains") and chk["url_contains"] in self.page.url
        body_hit = False
        if chk.get("body_contains"):
            try:
                body_hit = chk["body_contains"] in (self.page.inner_text("body") or "")
            except Exception:
                body_hit = False
        if url_hit or body_hit:
            raise SessionExpired(
                "로그인 세션이 만료된 것으로 보인다 (현재 URL: {}). "
                "인증 파일을 갱신한 뒤 다시 실행할 것.".format(self.page.url))

    def _url(self, url):
        return url if url.startswith("http") else self.base_url + "/" + url.lstrip("/")

    def _shoot(self, name):
        safe = re.sub(r'[\\/:*?"<>|]', "_", name)
        path = self.shot_dir / (safe + ".png")
        self.page.screenshot(path=str(path), full_page=True)
        self.shots.append(path.name)
        return path.name

    def run(self, step, case_id, idx):
        action = step.get("action")
        if action not in ALL_ACTIONS:
            raise StepFailure("정의되지 않은 action: {}".format(action))

        sel = step.get("selector")
        # expect_dialog 는 직전 버퍼를 봐야 하므로 여기서 비우면 안 된다
        if action not in ("expect_dialog", "expect_no_dialog"):
            self.dialogs = []

        if action == "goto":
            self.page.goto(self._url(step["url"]), wait_until="domcontentloaded")
            self.page.wait_for_timeout(600)
            self._check_session()

        elif action == "wait":
            if sel:
                self.page.wait_for_selector(sel, timeout=step.get("timeout", 10000))
            else:
                self.page.wait_for_timeout(step.get("ms", 1000))

        elif action == "screenshot":
            return self._shoot("{}_{:02d}_{}".format(case_id, idx, step.get("name", "shot")))

        elif action == "click":
            self.page.click(sel, timeout=step.get("timeout", 10000))
            self.page.wait_for_timeout(500)

        elif action == "hover":
            # locator.hover()는 셀 안 인라인 span에서 hit-test를 오판해 타임아웃이 난다.
            # 요소 중심 좌표로 직접 마우스를 옮기는 방식이 안정적이다.
            loc = self.page.locator(sel).first
            loc.scroll_into_view_if_needed(timeout=step.get("timeout", 10000))
            box = loc.bounding_box()
            if not box:
                raise StepFailure("호버 대상의 위치를 잡을 수 없음: {}".format(sel))
            self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            self.page.wait_for_timeout(step.get("ms", 800))

        elif action == "fill":
            value = step.get("value", "")
            if (self.qa_prefix and len(value) >= 2 and self.qa_prefix not in value
                    and not value.replace(".", "").isdigit()):
                self.warnings.append(
                    "{} 스텝{}: 입력값 '{}'에 QA 프리픽스({})가 없음 - 운영 데이터 오염 주의".format(
                        case_id, idx, value, self.qa_prefix)
                )
            self.page.fill(sel, value)

        elif action == "select":
            self.page.select_option(sel, step["value"])

        elif action == "check":
            self.page.check(sel)

        elif action == "uncheck":
            self.page.uncheck(sel)

        elif action == "upload":
            try:
                f = find_fixture(self.workspace, step["file"])
            except FileNotFoundError as e:
                raise StepFailure(str(e))
            self.page.set_input_files(sel, str(f))
            self.page.wait_for_timeout(800)

        elif action == "expect_visible":
            if not self.page.is_visible(sel):
                raise StepFailure("보여야 할 요소가 안 보임: {}".format(sel))

        elif action == "expect_hidden":
            if self.page.is_visible(sel):
                raise StepFailure("숨겨져야 할 요소가 보임: {}".format(sel))

        elif action == "expect_text":
            actual = (self.page.text_content(sel, timeout=step.get("timeout", 10000)) or "").strip()
            if "contains" in step and step["contains"] not in actual:
                raise StepFailure("문구 불일치 - 기대:'{}' / 실제:'{}'".format(
                    step["contains"], actual[:120]))
            if "not_contains" in step and step["not_contains"] in actual:
                raise StepFailure("있으면 안 되는 문구가 있음 - '{}' / 실제:'{}'".format(
                    step["not_contains"], actual[:150]))

        elif action == "expect_count":
            n = self.page.locator(sel).count()
            if "count" in step and n != step["count"]:
                raise StepFailure("개수 불일치 - 기대:{} / 실제:{}".format(step["count"], n))
            if "min" in step and n < step["min"]:
                raise StepFailure("최소 개수 미달 - 기대:{}개 이상 / 실제:{}".format(step["min"], n))

        elif action == "expect_attr":
            val = self.page.get_attribute(sel, step["attr"]) or ""
            if "equals" in step and val != step["equals"]:
                raise StepFailure("{} 불일치 - 기대:'{}' / 실제:'{}'".format(
                    step["attr"], step["equals"], val))
            if "contains" in step and step["contains"] not in val:
                raise StepFailure("{}에 '{}' 없음 - 실제:'{}'".format(
                    step["attr"], step["contains"], val))

        elif action == "expect_dialog":
            if not self.dialogs:
                raise StepFailure("알럿이 떠야 하는데 안 뜸 - 기대 문구:'{}'".format(step["contains"]))
            joined = " | ".join(self.dialogs)
            if step["contains"] not in joined:
                raise StepFailure("알럿 문구 불일치 - 기대:'{}' / 실제:'{}'".format(
                    step["contains"], joined))

        elif action == "expect_no_dialog":
            if self.dialogs:
                raise StepFailure("알럿이 뜨면 안 되는데 뜸: {}".format(" | ".join(self.dialogs)))

        elif action == "expect_js":
            # CSS로 표현 안 되는 판정(노출 순서·계산값 비교)에만 쓴다. 읽기 전용 강제.
            js = step["js"]
            flat = js.replace(" ", "")
            for bad in JS_MUTATION_PATTERNS:
                if bad.replace(" ", "") in flat:
                    raise StepFailure(
                        "expect_js 는 읽기 전용만 허용 - 금지 패턴 '{}' 발견".format(bad))
            value = self.page.evaluate(js)
            expected = step.get("equals", True)
            if value != expected:
                raise StepFailure("{} - 기대:{} / 실제:{}".format(
                    step.get("desc", "expect_js 불일치"), expected, value))

        return None


# -- 케이스 실행 ------------------------------------------------

def run_case(runner, case, blocklist, dry_run):
    """케이스 하나 실행. 결과 dict 반환."""
    cid = case["id"]
    result = {
        "id": cid,
        "title": case["title"],
        "requirement": case.get("requirement", ""),
        "source": case.get("source", ""),
        "writes": case.get("writes", False),
        "approved": case.get("approved", False),
        "status": "PASS",
        "reason": "",
        "steps": [],
        "shots": [],
        "duration": 0,
    }
    t0 = time.time()

    # -- 게이트 1: 쓰기 승인 --
    if case.get("writes"):
        if dry_run:
            result.update(status="SKIPPED", reason="dry-run 모드 - 쓰기 케이스 건너뜀")
            return result
        if not case.get("approved"):
            result.update(status="SKIPPED", reason="쓰기 미승인 - 케이스에 approved:true 필요")
            return result
        if not case.get("cleanup"):
            result.update(status="BLOCKED", reason="쓰기 케이스인데 cleanup 스텝이 없음 - 실행 거부")
            return result

    # -- 게이트 2: blocklist --
    for step in case["steps"] + case.get("cleanup", []):
        hit = hits_blocklist(step, blocklist)
        if hit:
            result.update(status="BLOCKED",
                          reason="blocklist 패턴 '{}' 매칭 - 실행 거부".format(hit))
            return result

    shots_before = len(runner.shots)

    def exec_steps(steps, phase):
        for i, step in enumerate(steps, 1):
            label = "{} {}".format(
                step.get("action"),
                step.get("selector", step.get("url", step.get("name", "")))
            )
            write = is_write_step(step)
            # 승인된 케이스여도 dry-run이면 쓰기 스텝은 건너뜀
            if write and dry_run:
                result["steps"].append({"phase": phase, "n": i, "label": label,
                                        "status": "SKIP", "write": True, "msg": "dry-run"})
                continue
            try:
                runner.run(step, cid, i)
                result["steps"].append({"phase": phase, "n": i, "label": label,
                                        "status": "OK", "write": write, "msg": ""})
            except Exception as e:
                msg = str(e).split("\n")[0][:300]
                result["steps"].append({"phase": phase, "n": i, "label": label,
                                        "status": "FAIL", "write": write, "msg": msg})
                raise

    try:
        exec_steps(case["steps"], "test")
    except SessionExpired:
        raise                      # 개별 케이스 실패로 묻지 않고 전체 실행을 멈춘다
    except Exception as e:
        result.update(status="FAIL", reason=str(e).split("\n")[0][:300])
        try:
            runner._shoot("{}_FAIL".format(cid))
        except Exception:
            pass

    # -- 정리(cleanup)는 본 테스트 실패와 무관하게 항상 시도 --
    if case.get("cleanup") and not dry_run and case.get("approved"):
        try:
            exec_steps(case["cleanup"], "cleanup")
        except Exception as e:
            result["reason"] += " / [!] cleanup 실패 - 생성 데이터 수동 확인 필요: {}".format(str(e)[:150])
            if result["status"] == "PASS":
                result["status"] = "FAIL"

    result["duration"] = round(time.time() - t0, 1)
    result["shots"] = runner.shots[shots_before:]
    return result


# -- 작업 폴더 초기화 -------------------------------------------

BAT_TEMPLATE = """@echo off
setlocal enabledelayedexpansion
set PYTHONIOENCODING=cp949:replace
cd /d "%~dp0"

echo.
echo ===============================================
echo   QA 자동화 실행
echo ===============================================
echo.

set RUNNER="{runner}"

set N=0
for %%F in (specs\\*.json) do (
    echo %%~nF | findstr /b "_" > nul
    if errorlevel 1 (
        set /a N+=1
        echo   !N!. %%~nF
    )
)

if %N%==0 (
    echo   실행할 스펙이 없습니다.
    echo   Claude에게 qa-test 스킬로 스펙을 만들어달라고 요청하세요.
    echo.
    pause
    exit /b 1
)

echo.
set "PICK="
set /p "PICK=실행할 번호 입력: "
if not defined PICK goto :badpick

rem Re-scan and match by index. Array vars (!SPEC[n]!) break when the
rem typed value carries stray characters, so compare as strings instead.
rem Keep comments ASCII - Korean bytes here can break the cmd parser.
set "TARGET="
set I=0
for %%F in (specs\\*.json) do (
    echo %%~nF | findstr /b "_" > nul
    if errorlevel 1 (
        set /a I+=1
        if "!I!"=="!PICK!" set "TARGET=%%F"
    )
)
if not defined TARGET goto :badpick
goto :picked

:badpick
echo.
echo   잘못된 번호입니다.
pause
exit /b 1

:picked
echo.
echo   1. 읽기만 실행  (안전 - 쓰기 케이스는 건너뜀)
echo   2. 전체 실행    (승인된 쓰기 케이스까지 실행)
echo.
set "MODE="
set /p "MODE=모드 번호 입력 [기본 1]: "
if not defined MODE set "MODE=1"

set "OPT=--dry-run"
if not "%MODE%"=="2" goto :run

set "OPT="
echo.
echo   [!] 전체 실행은 운영 데이터에 쓰기가 발생합니다.
set "OK="
set /p "OK=계속하려면 Y 입력: "
if /i not "%OK%"=="Y" (
    echo   취소되었습니다.
    pause
    exit /b 0
)

:run
echo.
py %RUNNER% "!TARGET!" %OPT%
set RESULT=%errorlevel%

echo.
for /f "delims=" %%D in ('dir /b /ad /o-d output 2^>nul') do (
    start chrome "%cd%\\output\\%%D\\리포트.html"
    goto :opened
)
:opened

echo.
pause
exit /b %RESULT%
"""


COOKIES_TEMPLATE = """# 크롬 F12 > Application > Cookies 에서 표 전체를 선택해 복사한 뒤
# 이 줄들 아래에 그대로 붙여넣으세요. JSON 으로 고칠 필요 없습니다.
# 컬럼 순서: Name / Value / Domain / Path / ... (탭 구분)
#
# 스펙에는  "cookies": "cookies.txt"  를 넣습니다.
# 가능하면 이 방식보다  --login  을 쓰세요 (만료 시 다시 로그인만 하면 됩니다).
"""


def do_login(url, ws):
    """브라우저를 띄워 사용자가 직접 로그인하게 하고, 그 세션을 auth.json 으로 저장한다.

    쿠키를 손으로 복사해오는 것보다 낫다 — localStorage 까지 함께 저장되고,
    만료되면 이 명령만 다시 돌리면 된다.
    """
    ws.mkdir(parents=True, exist_ok=True)
    auth = ws / "auth.json"
    print("[*] 브라우저를 엽니다: {}".format(url))
    print("    창에서 로그인을 끝낸 뒤, 이 콘솔로 돌아와 Enter 를 누르세요.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        try:
            input()
        except EOFError:
            print("[!] 콘솔 입력을 받을 수 없습니다. 터미널에서 직접 실행해 주세요.")
            browser.close()
            return 1
        ctx.storage_state(path=str(auth))
        n = len(json.loads(auth.read_text(encoding="utf-8")).get("cookies", []))
        browser.close()
    print("[*] 저장 완료: {} (쿠키 {}건)".format(auth, n))
    print('    스펙의 meta 에 "auth": "auth.json" 을 넣으면 이 세션으로 실행됩니다.')
    return 0


def init_workspace(target):
    """사용자 프로젝트에 qa_auto/ 작업 폴더를 만든다. 스킬 폴더는 건드리지 않는다."""
    ws = target / "qa_auto"
    for sub in ("specs", "cases", "fixtures", "output"):
        (ws / sub).mkdir(parents=True, exist_ok=True)

    bat = ws / "run_qa.bat"
    # 한국어 윈도우 콘솔(cp949) 기준으로 배치 파일을 쓴다.
    # UTF-8 로 쓰면 cmd 가 배치 본문을 cp949 로 읽어 메뉴 한글이 깨지고,
    # BOM 을 붙이면 첫 줄(@echo off)까지 깨진다. 배치는 cp949, 파이썬 출력은
    # PYTHONIOENCODING 으로 맞추는 조합이 가장 안정적이다.
    bat.write_text(BAT_TEMPLATE.format(runner=SKILL_DIR / "qa_run.py"),
                   encoding="cp949", errors="replace")

    ck = ws / "cookies.txt"
    if not ck.exists():
        ck.write_text(COOKIES_TEMPLATE, encoding="utf-8")

    # 로그인 세션과 운영 화면 스크린샷이 실수로 커밋되는 사고를 막는다.
    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "# 실제 로그인 세션 - 절대 커밋 금지\n"
            "auth.json\n"
            "cookies.txt\n"
            "cookies.json\n"
            "\n"
            "# 운영 화면 스크린샷과 리포트\n"
            "output/\n"
            "\n"
            "# 케이스와 스펙은 공유 대상이므로 제외하지 않는다\n"
            "!specs/\n"
            "!cases/\n",
            encoding="utf-8")

    print("[*] 작업 폴더 생성: {}".format(ws))
    print("    specs/    실행 스펙 JSON")
    print("    cases/    테스트 케이스 MD")
    print("    fixtures/ 업로드 테스트용 파일")
    print("    output/   리포트 + 스크린샷")
    print("    run_qa.bat   더블클릭 실행")
    print("    cookies.txt  (선택) 개발자도구 쿠키 표 붙여넣기용")
    print("")
    print("[*] 로그인이 필요한 화면이면:")
    print('    py "{}" --login <URL> --ws "{}"'.format(SKILL_DIR / "qa_run.py", ws))
    return 0


# -- 메인 ------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", help="spec.json 경로")
    ap.add_argument("--init", metavar="DIR",
                    help="해당 폴더에 작업 폴더(qa_auto/)와 실행 배치파일을 만든다")
    ap.add_argument("--login", metavar="URL",
                    help="브라우저를 띄워 직접 로그인하고 세션을 auth.json 으로 저장한다")
    ap.add_argument("--ws", metavar="DIR", default="qa_auto",
                    help="--login 이 저장할 작업 폴더 (기본: ./qa_auto)")
    ap.add_argument("--dry-run", action="store_true", help="쓰기 스텝 전부 건너뜀")
    ap.add_argument("--headed", action="store_true", help="브라우저 표시")
    ap.add_argument("--case", help="특정 케이스 ID만 실행")
    args = ap.parse_args()

    if args.init:
        return init_workspace(Path(args.init).resolve())
    if args.login:
        return do_login(args.login, Path(args.ws).resolve())
    if not args.spec:
        ap.error("spec 경로가 필요하다 (또는 --init DIR / --login URL)")

    spec_path = Path(args.spec).resolve()
    if not spec_path.exists():
        spec_path = (SKILL_DIR / args.spec).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    workspace = resolve_workspace(spec_path)
    meta = spec["meta"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = workspace / "output" / "{}_{}".format(meta["project"], stamp)
    shot_dir = out_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    cases = spec["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print("[!] 케이스 없음: {}".format(args.case))
            sys.exit(1)

    print("[*] 대상: {}".format(meta["base_url"].replace("{QA_ROOT}", SKILL_DIR.as_uri())))
    print("[*] 작업폴더: {}".format(workspace))
    print("[*] 케이스: {}건{}".format(
        len(cases), "  (DRY-RUN - 쓰기 없음)" if args.dry_run else ""))

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx_args = {"viewport": meta.get("viewport", DEFAULT_VIEWPORT)}

        # auth: --login 으로 저장한 세션(쿠키 + localStorage). 쿠키 복붙보다 이쪽을 권한다.
        if meta.get("auth"):
            auth_path = None
            for base in (spec_path.parent, workspace):
                cand = (base / meta["auth"]).resolve()
                if cand.exists():
                    auth_path = cand
                    break
            if auth_path is None:
                print("[!] 인증 파일 없음: {} — `--login <URL>` 로 먼저 로그인하세요.".format(meta["auth"]))
                sys.exit(1)
            ctx_args["storage_state"] = str(auth_path)
            print("[*] 세션 사용: {}".format(auth_path.name))

        ctx = browser.new_context(**ctx_args)
        if meta.get("cookies"):
            ctx.add_cookies(load_cookies(spec_path, meta["cookies"], workspace))
        page = ctx.new_page()
        runner = Runner(page, spec, spec_path, shot_dir, workspace)

        try:
            for case in cases:
                r = run_case(runner, case, spec.get("blocklist", []), args.dry_run)
                results.append(r)
                icon = {"PASS": "O", "FAIL": "X", "SKIPPED": "-", "BLOCKED": "!"}[r["status"]]
                print("  [{}] {} {}  {}".format(icon, r["id"], r["title"][:50], r["reason"][:60]))
        except SessionExpired as e:
            browser.close()
            print("\n[!] 실행 중단 - {}".format(e))
            print("    py qa_run.py --login <URL> --ws <작업폴더>  로 세션을 갱신하세요.")
            return 2

        browser.close()

    report_path = build_report(spec, results, runner.warnings, out_dir, args.dry_run)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    print("\n[*] 결과: 통과 {} / 실패 {} / 전체 {}".format(passed, failed, len(results)))
    print("[*] 리포트: {}".format(report_path))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
