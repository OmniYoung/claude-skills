"""
QA 자동화 러너 - spec.json을 읽어 실제 사이트에서 테스트를 실행하고 리포트를 생성한다.

사용법:
  py <skill>/qa_auto/qa_run.py --init .            작업 폴더(qa_auto/) 생성
  py <skill>/qa_auto/qa_run.py qa_auto/specs/xxx.json
  py <skill>/qa_auto/qa_run.py ... --dry-run       쓰기 케이스 전부 건너뜀 (셀렉터 검증용)
  py <skill>/qa_auto/qa_run.py ... --headed        브라우저 띄워서 눈으로 확인
  py <skill>/qa_auto/qa_run.py ... --case TC-003,TC-007   특정 케이스만 (쉼표로 여러 개)
  py <skill>/qa_auto/qa_run.py ... --rerun-failed latest   지난 실행에서 실패·점검 필요였던 케이스만
  py <skill>/qa_auto/qa_run.py ... --compare <결과폴더>     비교 기준 실행 지정 (기본: 직전 실행)
  py <skill>/qa_auto/qa_run.py ... --no-retry      실패 케이스 1회 재실행 끄기
  py <skill>/qa_auto/qa_run.py ... --no-trace      실패 케이스 동작 기록(trace) 끄기
  py <skill>/qa_auto/qa_run.py --strip-trace <trace.zip>  쿠키 · 인증 헤더 지운 사본 (개발팀 요청 시)
  py <skill>/qa_auto/qa_run.py --login URL [URL2 ...] --ws qa_auto
                                                   로그인 창. 로그인하면 저장 후 저절로 닫힘

산출물 위치:
  이 스크립트는 스킬 폴더에 아무것도 쓰지 않는다. 스펙 파일 위치로 작업 폴더를 정한다.
  <작업폴더>/specs/xxx.json 이면 <작업폴더> 아래 output/ 과 fixtures/ 를 쓴다.
  덕분에 팀원 각자가 자기 프로젝트에서 같은 스킬을 공유해 쓸 수 있다.

안전장치:
  - 쓰기 스텝이 있으면 케이스에 writes:true · approved:true 가 있어야 실행된다
    (선언 누락은 BLOCKED, 미승인 · dry-run 은 SKIPPED)
  - 누르기 직전 버튼 글자로 한 번 더 판정한다 (blocklist · 쓰기 힌트). 읽은 요소 그대로 누른다
  - 승인된 쓰기 케이스 밖에서는 서버로 가는 쓰기 요청(POST · PUT · DELETE 등)을 아예 막는다.
    승인 케이스도 첫 goto 이후, 지금 페이지에서 나간 요청만 통과시킨다
  - blocklist 의 주소 패턴은 메서드 · 승인과 무관하게 네트워크 단에서 막는다
  - 확인창은 승인된 쓰기 스텝에서만 수락하고, 나머지는 취소한다
  - 쓰기 케이스는 cleanup 스텝이 없으면 실행 거부
  - 막지 못하는 것: blocklist 에 없는 GET 쓰기 · 웹소켓 (리포트에 의심 신호로 남긴다)
"""

import argparse
import json
import pathlib
import re
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# 한국어 윈도우 콘솔은 기본이 cp949 라, 케이스 제목이나 실패 사유에 들어간
# "—" "·" "※" 같은 문자에서 출력 단계가 죽는다. 리포트(UTF-8)는 멀쩡한데
# 콘솔 print 하나 때문에 실행 전체가 중단되므로 진입점에서 맞춰둔다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
from report import build_report, write_results, _dev_items

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
# 판정 규칙은 gates.py 에 모아두고 단위 테스트한다 (tests/test_gates.py).
from gates import (ALL_ACTIONS, SAFE_METHODS, is_write_step, static_block_hit,
                   runtime_block_hit, runtime_write_hint, site_of, post_allowed,
                   text_mismatch, count_mismatch, is_absence_check, norm,
                   spec_errors, spec_warnings, effective_blocklist, url_block_hit,
                   ignored_request, get_write_suspect, NO_ANCHORS, _text_pattern,
                   W_QA_PREFIX, W_RESIDUE, W_SELECTOR_UPDATE, W_NA_STREAK,
                   BLOCK_BLOCKLIST, BLOCK_UNDECLARED, BLOCK_NO_CLEANUP, BLOCK_FIRST_GOTO,
                   BLOCK_RESIDUE_WRITE)

# 스펙이 요구하는 러너 기능 수준. 스펙 meta.runner_min 이 이보다 크면 실행 전에 멈춘다.
# 새 키 · 새 action 을 넣을 때만 올린다. run_qa.bat 도 이 줄을 읽어 낮으면 멈춘다
# (형식을 바꾸지 말 것: "RUNNER_VERSION = 숫자").
RUNNER_VERSION = 5

# 종료 코드. bat 이 3 일 때만 로그인 안내를 띄운다 (argparse 오류가 2 를 쓴다)
EXIT_ABORT = 3

# expect_js 는 읽기 전용이어야 한다. 아래 패턴이 보이면 실행을 거부한다.
# 단어 목록은 우회가 쉬워서 1차 거름망일 뿐이고, 실제 보호는 네트워크 안전망이 한다.
JS_MUTATION_PATTERNS = [
    ".click(", ".submit(", ".focus(", "fetch(", "XMLHttpRequest", "navigator.send",
    ".value=", ".value =", ".innerHTML=", ".innerHTML =", ".remove(", ".setAttribute(",
    "localStorage", "sessionStorage", "document.cookie", "location=", "location.href",
    "dispatchEvent", "eval(",
]

DEFAULT_VIEWPORT = {"width": 1600, "height": 900}

# 세션 하나를 이루는 meta 키. meta 에 직접 쓰면 기본 세션, meta.sessions.<이름> 에 쓰면 이름 붙인 세션.
SESSION_KEYS = ("auth", "cookies", "user_agent", "login_check", "viewport")

# 실행일 기준 날짜. "{TODAY}", "{TODAY+30}", "{TODAY-1}" -> YYYY-MM-DD.
# 날짜 입력 · 달력 화면은 고정 날짜를 쓰면 시간이 지나 과거가 되거나 "오늘" 칸을 못 집는다.
DATE_TOKEN = re.compile(r"\{TODAY(?:([+-])(\d+))?\}")
TOKEN_FIELDS = ("selector", "value", "text", "url", "js", "contains", "not_contains",
                "equals", "matches", "anchor")

# 실패로 보는 상태. 종료 코드 · 지난 실행 비교가 이 묶음을 쓴다.
BAD = ("FAIL", "ERROR")
# 재검수(--rerun-failed)로 다시 돌릴 상태. 불안정도 다시 본다.
RERUN = ("FAIL", "ERROR", "FLAKY")

# 이 조작 뒤에만 알럿 기록을 비운다. 판정 · 대기 스텝에서 비우면 직전 조작의 알럿을 놓친다.
CLEARS_DIALOGS = {"goto", "click", "dblclick", "press", "fill", "type", "select",
                  "check", "uncheck", "upload", "hover", "download"}

# 요소 하나의 화면 글자 (러너 _guard_click · _guard_press 와 탐색 헬퍼가 같이 쓴다).
# innerText · value · aria-label · title · alt. 버튼류이거나 글자가 빈 요소는 안에 든 이미지 alt · 아이콘의
# aria-label · title · svg title 도 읽는다 (최대 5개). 행 · 카드처럼 글자가 있는 요소는 안의 아이콘을 읽지 않는다
LABEL_JS = """x => {
  const out = [x.innerText || '', x.value || '', x.getAttribute('aria-label') || '', x.getAttribute('title') || '',
               x.getAttribute('alt') || ''];
  if (x.matches('button,a,[role=button],input[type=submit],input[type=button],input[type=image]')
      || !(x.innerText || '').trim()) {
    [...x.querySelectorAll('img[alt],[aria-label],[title],svg title')].slice(0, 5).forEach(c => out.push(
      c.tagName.toLowerCase() === 'title' ? c.textContent || ''
        : c.getAttribute('alt') || c.getAttribute('aria-label') || c.getAttribute('title') || ''));
  }
  return out;
}"""
# 누르기 직전 읽는 화면 글자. own = 요소 자신, up = 그 요소를 감싼 버튼 · 링크(+ withForm 이면 속한 폼의
# 제출 버튼). up 은 blocklist 대조에만 쓴다. body · html 은 글자를 읽지 않는다 (포커스가 없을 때의 Enter)
GUARD_TEXTS_JS = """(e, withForm) => {
  if (e === document.body || e === document.documentElement) return {own: [], up: []};
  const r = """ + LABEL_JS + """;
  const up = [];
  const b = e.closest('button,a,[role=button],input[type=submit],input[type=button],input[type=image],area,label,[onclick]');
  if (b && b !== e) up.push(...r(b));
  if (withForm && e.form) {
    e.form.querySelectorAll('button:not([type]),button[type=submit],input[type=submit],input[type=image]')
      .forEach(x => up.push(...r(x)));
  }
  return {own: r(e), up: up};
}"""
# 요소를 누르는 효과가 있는 키 (press). 이 키만 blocklist 를 본다
ACTIVATE_KEYS = ("enter", "numpadenter", "space", " ", "\n", "\r")


def _expand_str(s, qa_prefix, run_id):
    def rep(m):
        d = date.today()
        if m.group(1):
            d += timedelta(days=int(m.group(2)) * (1 if m.group(1) == "+" else -1))
        return d.isoformat()
    if "{QA_PREFIX}" in s:
        s = s.replace("{QA_PREFIX}", qa_prefix)
    if "{RUN_ID}" in s:
        s = s.replace("{RUN_ID}", run_id)
    if "{TODAY" in s:
        s = DATE_TOKEN.sub(rep, s)
    return s


def expand_tokens(step, qa_prefix="", run_id=""):
    """{TODAY±N} 은 실행일, {QA_PREFIX} 는 meta.qa_prefix, {RUN_ID} 는 실행 번호로 바꾼다.

    {RUN_ID} 는 실행마다 달라서, 지난 실행이 남긴 데이터와 이번 데이터를 섞지 않고 찾을 수 있다.
    qa_prefix 안에 든 {TODAY} · {RUN_ID} 도 펼친다.
    """
    prefix = _expand_str(qa_prefix or "", "", run_id)
    out = dict(step)
    for k in TOKEN_FIELDS:
        if isinstance(out.get(k), str):
            out[k] = _expand_str(out[k], prefix, run_id)
    return out


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


# -- 다운로드 파일 읽기 -----------------------------------------

def read_table(path):
    """다운로드한 표 파일을 [[셀,...], ...] 로 읽는다.

    .xlsx 는 openpyxl, csv/tsv 는 구분자 분리, .xls 는 서버가 HTML 표를 그 확장자로
    내보내는 경우가 흔해서 태그를 벗겨 읽는다.
    """
    raw = path.read_bytes()
    suffix = path.suffix.lower()

    if raw[:2] == b"PK" and suffix in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise StepFailure(
                "xlsx 를 읽으려면 openpyxl 이 필요하다: pip install openpyxl")
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [["" if c is None else str(c).strip() for c in r]
                for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows

    text = raw.decode("utf-8-sig", errors="replace")
    if "<table" in text.lower():                      # HTML 표를 .xls 로 내보낸 경우
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
            cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
            if cells:
                rows.append(cells)
        return rows

    sep = "\t" if "\t" in text.split("\n")[0] else ","
    return [[c.strip().strip('"') for c in line.split(sep)]
            for line in text.splitlines() if line.strip()]


# -- 스텝 실행 --------------------------------------------------

class StepFailure(Exception):
    """검수 스크립트 쪽 문제 (설정 오류 · 파일 없음 등). 케이스는 ERROR(점검 필요).

    timeout=True 면 기다리다 끝난 것 (파일 다운로드 대기 등). 결과에 timeout 필드로 남는다.
    """
    def __init__(self, msg="", timeout=False):
        super().__init__(msg)
        self.timeout = timeout


class AssertFail(StepFailure):
    """화면이 기대와 다르다는 판정. 케이스는 FAIL 이고, defect 면 개발 전달 대상이다.

    셀렉터를 못 찾거나 시간 초과가 난 것과 구분해야 한다. 둘을 같은 FAIL 로 두면
    셀렉터가 낡아서 난 실패까지 결함으로 개발팀에 넘어간다.
    """
    pass


class PreconditionUnmet(Exception):
    """precondition 스텝이 안 맞음. 운영 데이터가 바뀐 것이지 결함이 아니므로 NA."""
    pass


class WriteBlocked(Exception):
    """실행 직전에 쓰기 동작으로 판정됐는데 승인된 쓰기 케이스가 아님. 케이스는 BLOCKED.

    blocked_by 는 gates.BLOCK_* 코드. 리포트가 사유 문장 대신 이 값으로 안내문을 고른다.
    """
    def __init__(self, msg, blocked_by):
        super().__init__(msg)
        self.blocked_by = blocked_by


class SessionAbort(Exception):
    """세션 만료로 전체 실행을 멈춘다. 중단 시점 케이스의 결과를 같이 싣는다."""
    def __init__(self, msg, result):
        super().__init__(msg)
        self.result = result


# -- 사유 · 경고 문장 ---------------------------------------------
# 결과 사유와 경고 문장은 여기 한 곳에 둔다. 회귀 테스트(tests/regress)는 이 상수를 import 해
# 기대값을 맞추고, 리포트는 문장이 아니라 코드 · 필드(timeout, blocked_by, 경고 코드)로 읽는다.
# 문장을 고칠 때는 SKILL.md · spec_schema.md 의 인용도 같이 고친다.

# 게이트 (실행 전)
REASON_BLOCKLIST = "blocklist 패턴 '{}' 매칭 - 실행 거부"
REASON_UNDECLARED = "쓰기 선언 누락 - {phase}스텝 {n} '{label}' 이 쓰기 동작"
REASON_UNDECLARED_CLEANUP = "정리 "          # REASON_UNDECLARED 의 {phase} (정리 스텝일 때)
REASON_DRY_RUN = "dry-run 모드 - 쓰기 케이스 건너뜀"
REASON_UNAPPROVED = "쓰기 미승인 - 실행하려면 이 케이스의 쓰기 승인이 필요"
REASON_NO_CLEANUP = "쓰기 케이스인데 정리 스텝(만든 데이터 지우기)이 없음 - 실행 거부"
REASON_FIRST_GOTO = "승인 쓰기 케이스는 첫 스텝이 페이지 열기여야 함 - 실행 거부"
REASON_RESIDUE_WRITE = "잔여 데이터 점검 스텝에 쓰기 동작이 있음 - 읽기만 가능"
# 실행 중 (화면 글자로 막음)
REASON_RUNTIME_BLOCKLIST = "blocklist 패턴 '{}' 와 같은 버튼 - 실행 거부"
REASON_RUNTIME_WRITE = ("버튼 글자 '{shown}' 에 '{hint}' 이 있어 쓰기 동작으로 판정 - 쓰기 선언 · 승인이 필요합니다. "
                        "조회 버튼이면 스텝에 write:false")
REASON_TYPE_TAB = ("입력값에 탭 글자가 있어 실행 거부 - 탭은 포커스를 다음 버튼으로 옮겨 누를 수 있습니다. "
                   "Claude 에게 \"입력 스텝 나눠줘\" 라고 요청합니다.")
# 안전망
INTERFERE_REQUEST = "요청 차단"     # runner.interfered 에 넣는 값. REASON_INTERFERED 의 {what} 에 들어간다
INTERFERE_DIALOG = "확인창 취소"
INTERFERE_POPUP = "다른 창 요청 차단"   # 같은 세션에서 열린 팝업의 쓰기 요청을 막음
# 막힌 요청 사유에는 설정 키를 적지 않는다. 키를 보고 쓰기 주소를 allow_post 에 넣으면 dry-run 이 운영에 쓴다.
# 분류(조회 · 로그 · 실제 쓰기)는 SKILL 2단계 절차로 한다. {} 는 "POST /경로" (검수 주소 앞부분은 뺌)
REASON_REQUEST_BLOCKED = "안전망이 서버 요청을 막음: {}. 조회 · 로그 · 실제 쓰기 중 무엇인지 분류가 필요합니다."
# write:false 로 둔 클릭 직후 서버 요청을 막았을 때. 조회 POST 버튼일 수도, 빈 폼 제출 검증 케이스의
# 검증 누락일 수도 있다. 어느 쪽인지는 SKILL 2단계 분류 절차로 가린다. 상태는 점검 필요(ERROR)
REASON_WRITE_FALSE_SENT = ("write:false 로 둔 버튼이 서버 요청을 보냄: {} - 조회 요청인지, "
                           "입력 검증 누락(결함 후보)인지 확인이 필요합니다.")
REASON_GUARD_ERROR = "안전망 처리 오류로 막음: {} {}"
REASON_DIALOG_DISMISSED = ("확인창 '{}' 을 취소함 - 쓰기 승인된 스텝이 아님. 확인창 문구만 보는 케이스면 "
                           "Claude 에게 \"확인창 문구 확인 스텝 넣어줘\" 라고 요청합니다.")
# 화면 확인 스크립트(expect_js)가 보낸 서버 요청은 승인 케이스여도 막는다
REASON_JS_BLOCKED = "화면 확인 스크립트가 서버 요청을 보내려 해서 막음: {}"
# {what} 은 INTERFERE_* 를 " · " 로 이은 것. "개입으로" 로 받아 {what} 끝 글자 받침과 상관없이 조사가 맞는다
REASON_INTERFERED = "판정 불가 - 안전망 개입({what})으로 화면 흐름이 실제와 다름. {blocked}이후 결과: {reason}"
REASON_INTERFERED_BLOCKED = "막은 요청: {} · "   # REASON_INTERFERED 의 {blocked} (막은 요청이 있을 때)
# 판정 결과
REASON_TIMEOUT = "시간 초과 - 요소가 없거나 셀렉터가 바뀌었을 수 있음 ({})"
REASON_DOWNLOAD_TIMEOUT = "파일이 내려오지 않음 - 시간 초과 ({}초)"
REASON_NOT_FOUND = "요소를 찾지 못함: {} - 셀렉터가 바뀌었거나 화면이 덜 그려졌을 수 있음"
REASON_ANCHOR_HIDDEN = "기준 요소가 안 보임: {} - 화면이 덜 그려졌거나 셀렉터가 바뀌었을 수 있음"
REASON_ANCHOR_GONE = "관찰 중에 기준 요소가 사라짐: {} - 화면이 바뀌어 판정 불가"
# 리포트 중단 알림과 같은 문장. {} 는 SessionExpired 문장 (중단 시점 주소, 직전에 막은 요청)
REASON_SESSION = "로그인이 풀려 중단됨 - login_qa.bat 으로 다시 로그인한 뒤 실행합니다. {}"
SESSION_AT = "(중단 시점 주소: {})"      # SessionExpired 문장 앞부분
# 세션 만료 사유 · 중단 알림 · 콘솔 끝에 붙인다. 토큰 갱신 POST 를 안전망이 막아 앱이 스스로 로그아웃한 경우
# 다시 로그인해도 같은 결과라서. {} 는 실행 중 최근에 막은 요청 (Runner.recent_blocked, "POST /경로")
REASON_SESSION_BLOCKED = ("직전에 막은 요청: {} - 로그인 유지 요청이면 다시 로그인해도 같은 결과가 납니다. "
                          "Claude 에게 \"막힌 요청 분류해줘\" 라고 요청합니다.")
REASON_NA = "조건 미충족 - {}"
REASON_PRE_UNKNOWN = "조건 확인 불가 - {} (기준 요소가 없거나 안 보임)"
REASON_PRE_INTERFERED = "조건 확인 불가 - 안전망 개입({what})으로 화면 흐름이 실제와 다름. {blocked}이후 결과: {reason}"
REASON_PRE_WRITE = "조건 확인 스텝은 읽기만 가능: {}"
REASON_ABSENCE_UNKNOWN = "부재 판정 불가 - {} 가 0개인데 기준 요소가 없어 셀렉터가 맞는지 알 수 없음"
REASON_CLEANUP_FAIL = " / [!] 정리 실패 - 생성 데이터 수동 확인 필요: {}"
REASON_FLAKY = "1차 실패, 재실행 통과 - {}"
REASON_RETRY_ERROR = "1차 실패: {first} / 재실행: {second}"   # 1차 FAIL · 재실행 ERROR (상태는 ERROR)
# 경고 (코드는 gates.W_*)
WARN_QA_PREFIX = "{} 스텝{}: 입력값 '{}'에 QA 프리픽스({})가 없음 - 운영 데이터 오염 주의"
WARN_RESIDUE = "테스트 데이터가 남아 있을 수 있음 - 수동 확인 필요: {}"
WARN_SELECTOR_UPDATE = ("셀렉터 갱신 필요: {} 가 {}건에서 시간 초과 - 화면 구조가 바뀐 것으로 보임. "
                        "Claude 에게 '{} 셀렉터 갱신해줘' 라고 요청")
WARN_NA_STREAK = ("{}: {}회 연속 조건 미충족 - 조건 확인 스텝이 실제 결함을 가리고 있지 않은지 "
                  "다른 경로로 확인 필요")
# 판정에 영향 없는 신호 (results.json signals)
SIGNAL_POPUP = "새 창이 열림 - 그 창의 요청은 막음 (팝업 화면은 검수 대상 아님)"
SIGNAL_WEBSOCKET = "웹소켓 연결 (안전망 밖 - 이 연결로 보내는 쓰기는 막지 못함): {}"
# 콘솔 끝 요약 (bat 창). bat 사용자는 콘솔만 보고 닫는 경우가 많아 위험 신호도 여기 싣는다
CONSOLE_RESULT = ("[*] 결과: 통과 {PASS} / 실패 {FAIL} / 점검 필요 {ERROR} / 불안정 {FLAKY} / 조건 미충족 {NA} / "
                  "차단 {BLOCKED} / 승인 대기 {SKIPPED} / 전체 {total}")
CONSOLE_GET_SUSPECT = ("[!] GET 쓰기 의심 {}건 - 운영 데이터가 바뀌었을 수 있습니다. "
                       "담당 기획자에게 알리고 이 스펙은 다시 돌리지 마세요.")


def console_summary(results):
    """콘솔 끝 요약 줄. 결과 건수 한 줄, GET 쓰기 의심이 있으면 한 줄 더 (리포트 할 일과 같은 건수)."""
    n = {k: sum(1 for r in results if r["status"] == k)
         for k in ("PASS", "FAIL", "ERROR", "FLAKY", "NA", "BLOCKED", "SKIPPED")}
    lines = [CONSOLE_RESULT.format(total=len(results), **n)]
    sus = sum(1 for r in results if r.get("get_suspects"))
    if sus:
        lines.append(CONSOLE_GET_SUSPECT.format(sus))
    return lines


def _clip(s, n):
    """n 자에서 자르고 잘렸으면 … 를 붙인다 (경로 중간에서 끊긴 걸 알아보게)."""
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _without_note(msg, note):
    """세션 중단 문장에서 '직전에 막은 요청' 부분을 뺀다 (콘솔 끝 안내에 따로 찍으므로)."""
    return msg.replace(" / " + note, "") if note else msg


def _reason(e):
    """예외를 사유 한 줄로. 시간 초과는 왜 점검이 필요한지 같이 적는다."""
    msg = str(e).split("\n")[0][:300]
    if isinstance(e, PWTimeout) or ("Timeout" in msg and "exceeded" in msg):
        return REASON_TIMEOUT.format(msg[:200])
    return msg


def is_timeout(e):
    """기다리다 끝난 예외인지. 사유 문장이 아니라 예외 종류 · 표시로 가린다 (결과의 timeout 필드)."""
    if getattr(e, "timeout", False) or isinstance(e, PWTimeout):
        return True
    msg = str(e).split("\n")[0][:300]
    return "Timeout" in msg and "exceeded" in msg


REL_KO = {"below": "아래", "above": "위", "right_of": "오른쪽", "left_of": "왼쪽", "inside": "안"}
ALIGN_KO = {"left": "왼쪽", "right": "오른쪽", "top": "위쪽", "bottom": "아래쪽",
            "center_x": "가로 가운데", "center_y": "세로 가운데"}


def _range(want, tol):
    """숫자면 ±tol, {"min":..,"max":..} 면 그 범위."""
    if isinstance(want, dict):
        return want.get("min", float("-inf")), want.get("max", float("inf"))
    return want - tol, want + tol


def _range_text(want):
    if isinstance(want, dict):
        lo, hi = want.get("min"), want.get("max")
        if lo is not None and hi is not None:
            return "{}~{}px".format(lo, hi)
        return "{}px 이상".format(lo) if lo is not None else "{}px 이하".format(hi)
    return "{}px".format(want)


def _edge(b, name):
    return {"left": b["x"], "right": b["x"] + b["width"], "top": b["y"],
            "bottom": b["y"] + b["height"], "center_x": b["x"] + b["width"] / 2,
            "center_y": b["y"] + b["height"] / 2}[name]


class Runner:
    def __init__(self, page, spec, spec_path, shot_dir, workspace, run_id=""):
        self.page = page
        self.spec = spec
        self.spec_path = spec_path
        self.shot_dir = shot_dir
        self.workspace = workspace
        self.run_id = run_id
        meta = spec["meta"]
        # {QA_ROOT} = 번들된 스크립트 폴더의 file:// URI. 자체 점검용 로컬 픽스처를 가리킬 때 쓴다.
        self.base_url = meta["base_url"].replace("{QA_ROOT}", SKILL_DIR.as_uri()).rstrip("/")
        self.base_site = site_of(self.base_url)
        self.qa_prefix = _expand_str(meta.get("qa_prefix", ""), "", run_id)
        self.login_check = meta.get("login_check")
        self.spec_blocklist = spec.get("blocklist", [])
        self.blocklist = list(self.spec_blocklist)   # 케이스마다 effective_blocklist 로 바꾼다
        self.allow_post = meta.get("allow_post", [])
        self.ignore_requests = meta.get("ignore_requests", [])
        self.dialogs = []          # 직전 조작이 띄운 알럿 기록
        self.dismissed = []        # 승인 없이 떠서 취소한 확인창
        self.warnings = []         # [(gates.W_* 코드, 문장)]
        self.shots = []
        self.files = {}   # download 로 받은 파일
        self.signals = []  # 케이스 하나 동안 난 JS 오류 · 콘솔 오류 · 실패한 요청 (참고용)
        self.get_suspects = []     # 조작 뒤 나간 GET 쓰기 의심 주소 (막지 못함, 확인 필요)
        # 네트워크 안전망 상태. 승인된 쓰기 케이스의 첫 goto 뒤부터 cleanup 까지만 allow_writes 가 켜진다
        self.allow_writes = False
        self.js_guard = False      # expect_js 평가 중에는 승인 케이스여도 서버 쓰기를 막는다
        self.step_write = False    # 확인창을 수락할 수 있는 구간인지 (쓰기 스텝부터 다음 조작 전까지)
        # GET 쓰기 의심 신호를 모으는 구간. 조작 스텝(클릭 · 입력 · 선택 · 다운로드 등)이 시작될 때 켜고
        # 다음 goto 시작 · 케이스 시작 때만 끈다 (지연 · 디바운스 GET 도 잡히게)
        self.watch_get = False
        self.blocked = []          # 막은 요청 (판정에 영향). 스텝 판정 때 비운다
        self.blocked_log = []      # 이 케이스에서 막은 요청 전부 (사유에 싣는다)
        # 실행 전체에서 최근에 막은 요청 3줄. 케이스 시작 때 비우지 않는다. 앞 케이스에서 막은 토큰 갱신 POST 때문에
        # 다음 케이스가 로그인 화면으로 가면 세션 만료 안내에 같이 싣는다
        self.recent_blocked = []
        self.interfered = set()    # 이 케이스에서 막은 것 ("요청 차단" · "확인창 취소")
        self.anchor = None         # 케이스 기본 anchor
        self.fail_url = ""         # 실패 시점 주소
        if page is not None:
            self.attach(page)

    def guard_context(self, ctx):
        """컨텍스트의 모든 요청을 거쳐 가게 한다.

        버튼 글자나 셀렉터로 쓰기를 가려내는 규칙은 언제든 빠져나갈 길이 있다. 서버에
        무언가 보내는 요청(POST · PUT · DELETE 등)을 승인된 쓰기 케이스 밖에서는 아예
        보내지 않는 것이 마지막 안전장치다.
        """
        ctx.route("**/*", self._route)

    def _from_current_page(self, request):
        """요청이 지금 케이스의 페이지에서 나왔는지. 알 수 없으면(서비스워커 등) None."""
        try:
            pg = request.frame.page
        except Exception:
            return None
        return pg is self.page

    def _same_session(self, request):
        """요청이 지금 페이지와 같은 컨텍스트(세션)의 다른 창에서 나왔는지."""
        try:
            return request.frame.page.context is self.page.context
        except Exception:
            return False

    def _route(self, route, request):
        try:
            method = request.method.upper()
            url = request.url
            if url.startswith(("data:", "blob:", "file:")):
                return route.continue_()
            # 기획자가 적어 둔 주소 패턴은 메서드 · 승인과 무관하게 막는다 (GET 쓰기 대비).
            # 스크립트 · 스타일 · 글꼴 파일은 대조하지 않는다 (짧은 단어가 파일 이름에 걸려 화면이 깨지는 것 방지)
            hit = url_block_hit(url, self.blocklist, request.resource_type)
            if hit:
                return self._block(route, method, url, "blocklist '{}'".format(hit))
            own = self._from_current_page(request)
            if own is False:
                # 팝업 · 앞 케이스가 연 창 · 다른 세션 탭의 요청은 지금 케이스 것이 아니다
                route.abort("blockedbyclient")
                self._signal("다른 창의 요청 차단: {} {}".format(method, url[:120]))
                # 같은 세션에서 연 팝업(선택 팝업 등)의 쓰기 요청을 막으면 부모 화면 흐름도 달라진다.
                # 막은 것 자체로 실패시키지는 않고, 이후 판정 실패를 개발 전달로 보내지 않게만 한다
                if (method not in SAFE_METHODS and self._same_session(request)
                        and not ignored_request(url, self.ignore_requests, self.base_site)):
                    line = "{} {} (다른 창)".format(method, self._short(url))
                    if line not in self.blocked_log:
                        self.blocked_log.append(line)
                    self.interfered.add(INTERFERE_POPUP)
                return
            if method in SAFE_METHODS:
                # 다른 사이트(클라우드 API 게이트웨이 등)로 가는 GET 쓰기도 신호로 남긴다. 분석 도구는 뺀다
                if (self.watch_get and not self.allow_writes
                        and not ignored_request(url, self.ignore_requests, self.base_site)):
                    word = get_write_suspect(url)
                    if word and request.resource_type in ("document", "xhr", "fetch", "image",
                                                          "ping", "other"):
                        line = "GET {} ('{}')".format(url[:160], word)
                        if line not in self.get_suspects and len(self.get_suspects) < 10:
                            self.get_suspects.append(line)
                return route.continue_()
            if self.allow_writes and not self.js_guard and own:
                return route.continue_()
            if not self.js_guard and post_allowed(method, url, self.allow_post, self.base_site):
                return route.continue_()
            self._block(route, method, url, "")
        except Exception:
            # 안전망 쪽 오류는 막는 쪽으로 처리하고, 조용히 통과로 끝나지 않게 기록한다
            try:
                route.abort("blockedbyclient")
            except Exception:
                pass
            try:
                self.blocked.append(REASON_GUARD_ERROR.format(request.method, request.url[:120]))
                self.interfered.add(INTERFERE_REQUEST)
            except Exception:
                pass

    def _block(self, route, method, url, why):
        """요청을 막고 기록한다. 분석 · 로그 요청(ignore)은 막기만 하고 판정에는 쓰지 않는다."""
        route.abort("blockedbyclient")
        line = "{} {}{}".format(method, self._short(url), " ({})".format(why) if why else "")
        if ignored_request(url, self.ignore_requests, self.base_site):
            self._signal("분석 · 로그 요청 차단 (판정 무관): {}".format(line))
            return
        if line not in self.blocked:
            self.blocked.append(line)
        if line not in self.blocked_log:
            self.blocked_log.append(line)
        if line in self.recent_blocked:
            self.recent_blocked.remove(line)
        self.recent_blocked = (self.recent_blocked + [line])[-3:]
        self.interfered.add(INTERFERE_REQUEST)

    def session_note(self):
        """세션 만료 안내에 붙일 한 줄. 실행 중 막은 요청이 없으면 빈 문자열."""
        if not self.recent_blocked:
            return ""
        return REASON_SESSION_BLOCKED.format(" / ".join(self.recent_blocked))

    def _short(self, url):
        """사유에 싣는 주소. 검수 주소와 같은 출처면 경로부터(allow_post 에 적는 형식), 다른 곳이면 주소 전체."""
        m = re.match(r"^[a-z][a-z0-9+.-]*://[^/?#]+", self.base_url or "", re.I)
        if m and url.startswith(m.group(0) + "/"):
            url = url[len(m.group(0)):]
        return url[:160]

    def check_blocked(self):
        """막은 요청이 있으면 스텝을 점검 필요로 끝낸다."""
        if self.blocked:
            lines, self.blocked = self.blocked, []
            raise StepFailure(REASON_REQUEST_BLOCKED.format(" / ".join(lines[:3])))

    def check_dismissed(self):
        """취소한 확인창이 있으면 스텝을 점검 필요로 끝낸다. 이후 판정도 개입으로 친다."""
        if self.dismissed:
            msgs, self.dismissed = self.dismissed, []
            self.interfered.add(INTERFERE_DIALOG)
            raise StepFailure(REASON_DIALOG_DISMISSED.format(" | ".join(msgs)[:150]))

    def attach(self, page):
        """페이지에 알럿 처리와 콘솔 · 네트워크 오류 수집을 붙인다.

        화면은 멀쩡한데 JS 오류나 API 500 이 나는 경우가 있다. 기획서 근거가 아니므로
        판정에는 쓰지 않고, 리포트에 참고로만 싣는다.
        """
        page.on("dialog", self._on_dialog)
        page.on("pageerror", lambda e: self._signal(
            "JS 오류: {}".format(str(e).split("\n")[0][:200])))
        page.on("console", self._on_console)
        page.on("response", self._on_response)
        # 웹소켓으로 보내는 데이터는 안전망이 막지 못한다. 연결이 있었다는 것만 남긴다
        page.on("websocket", lambda ws: self._signal(SIGNAL_WEBSOCKET.format(ws.url[:120])))

    opening = False    # 러너가 직접 새 페이지를 여는 중 (진단용)

    def on_new_page(self, pg):
        """새 창(팝업)이 열리면 신호만 남긴다. 그 창의 요청은 _route 가 막는다."""
        if not self.opening and pg is not self.page:
            self._signal(SIGNAL_POPUP)

    def _signal(self, s):
        if s not in self.signals and len(self.signals) < 20:
            self.signals.append(s)

    def _on_console(self, m):
        try:
            # 이미지 404 같은 리소스 로드 실패는 소음이 커서 뺀다. 중요한 요청은 _on_response 가 잡는다.
            if m.type == "error" and not m.text.startswith("Failed to load resource"):
                self._signal("콘솔 오류: {}".format(m.text.split("\n")[0][:200]))
        except Exception:
            pass

    def _on_response(self, resp):
        try:
            if resp.status >= 400 and resp.request.resource_type in ("xhr", "fetch", "document"):
                self._signal("HTTP {} {}".format(resp.status, resp.url[:160]))
        except Exception:
            pass

    def use_page(self, page, login_check):
        """케이스가 다른 세션이면 그 세션의 페이지로 갈아탄다 (meta.sessions 참고).

        세션마다 브라우저 컨텍스트가 따로라 쿠키가 섞이지 않는다. 같은 세션의 케이스끼리는
        페이지를 이어 쓰므로 지금까지처럼 앞 케이스의 상태가 남는다.
        """
        if page is not self.page:
            self.page = page
            self.dialogs = []
        self.login_check = login_check

    def _on_dialog(self, dialog):
        """알럿은 닫고, 확인창(confirm · prompt)은 승인된 쓰기 스텝에서만 수락한다.

        삭제 버튼 → "삭제하시겠습니까?" 를 자동 수락하면 dry-run 이어도 지워진다.
        승인 없이 뜬 확인창은 취소하고 dismissed 에만 기록해 둔다. 다음 스텝(check_dismissed) ·
        expect_dialog · expect_no_dialog · 판정 변환(final_status)이 이 기록을 소비한다.
        """
        try:
            src = dialog.page
        except Exception:
            src = None
        if src is not None and src is not self.page:
            # 다른 세션 탭 · 팝업에서 뜬 창은 지금 케이스의 알럿이 아니다
            self._signal("다른 창의 알럿 · 확인창 취소: {}".format(dialog.message[:80]))
            try:
                dialog.dismiss()
            except Exception:
                pass
            return
        self.dialogs.append(dialog.message)
        try:
            if dialog.type in ("confirm", "prompt"):
                if self.allow_writes and self.step_write and not self.js_guard:
                    dialog.accept()
                else:
                    self.dismissed.append(dialog.message)
                    dialog.dismiss()
            else:
                dialog.accept()
        except Exception:
            pass

    def _check_session(self):
        """로그인이 풀렸는지 본다.

        세션이 만료되면 모든 케이스가 엉뚱한 이유로 줄줄이 실패해서 원인 파악이 오래 걸린다.
        첫 페이지에서 바로 잡아 중단시키는 편이 낫다.

        login_check 는 조건 하나 또는 목록이다. 조건에 when_url 이 있으면 현재 URL 에 그 문자열이
        들어 있을 때만 본다. 한 세션으로 프론트와 어드민을 오가면 한쪽의 "비로그인 문구"가
        다른 쪽 화면(예: 어드민 메뉴의 '회원가입 이벤트페이지')에도 있어 오탐이 나기 때문이다.
        """
        chk = self.login_check
        if not chk:
            return
        for rule in (chk if isinstance(chk, list) else [chk]):
            if rule.get("when_url") and rule["when_url"] not in self.page.url:
                continue
            url_hit = rule.get("url_contains") and rule["url_contains"] in self.page.url
            body_hit = False
            if rule.get("body_contains"):
                try:
                    body_hit = rule["body_contains"] in (self.page.inner_text("body") or "")
                except Exception:
                    body_hit = False
            if url_hit or body_hit:
                note = self.session_note()
                raise SessionExpired(SESSION_AT.format(self.page.url) + (" / " + note if note else ""))

    def _warn_prefix(self, value, case_id, idx):
        """테스트로 만든 데이터인지 눈으로 구분되게 QA 프리픽스를 권한다.

        검색어 입력처럼 프리픽스가 없어야 정상인 경우도 있어 차단은 하지 않고 경고만 남긴다.
        """
        if (self.qa_prefix and len(value) >= 2 and self.qa_prefix not in value
                and not value.replace(".", "").isdigit()):
            self.warnings.append((W_QA_PREFIX, WARN_QA_PREFIX.format(case_id, idx, value, self.qa_prefix)))

    def _url(self, url):
        return url if url.startswith("http") else self.base_url + "/" + url.lstrip("/")

    def _shoot(self, name, step=None):
        """스크린샷. step 에 selector 가 있으면 그 지점만 잘라 찍는다.

        전체 화면만 찍으면 수백 행짜리 표에서 문제 행을 찾을 수가 없어 결함 증빙이 안 된다.
        clip 으로 잘라내므로 마우스를 움직이지 않는다 - 호버로 띄운 툴팁이 살아 있다.
        """
        safe = re.sub(r'[\\/:*?"<>|]', "_", name)
        path = self.shot_dir / (safe + ".png")
        clip = self._clip_for(step) if step else None
        if clip:
            self.page.screenshot(path=str(path), clip=clip)
        else:
            self.page.screenshot(path=str(path), full_page=True)
        self.shots.append(path.name)
        return path.name

    def _clip_for(self, step):
        """selector + 주변 맥락(context/include_header)을 감싸는 사각형을 구한다."""
        sel = step.get("selector")
        if not sel:
            return None
        target = self.page.locator(sel).first
        try:
            if target.count() == 0:
                raise LookupError(sel)     # 없는 요소를 3초 기다리지 않는다 (결과는 같음: 기준 요소 주변만)
            target.scroll_into_view_if_needed(timeout=3000)
            self.page.wait_for_timeout(120)
            b = target.bounding_box()
        except Exception:
            b = None
        boxes = [b] if b else []
        # 기준 요소(위치 판정의 기준 · anchor)도 같이 담는다. 대상이 없으면 기준 요소 주변만이라도
        for ex in step.get("extra") or []:
            try:
                eb = self.page.locator(ex).first.bounding_box(timeout=1000)
                if eb:
                    boxes.append(eb)
            except Exception:
                pass
        if not boxes:
            return None

        # 위아래 정상 행을 같이 담아야 "이 행만 다르다"가 보인다
        ctx = step.get("context")
        if ctx:
            sib = ctx.get("selector", sel)
            try:
                pos, total = self.page.evaluate(
                    """([sel, sib]) => {
                        const all = Array.from(document.querySelectorAll(sib));
                        const t = document.querySelector(sel);
                        return [all.indexOf(t), all.length];
                    }""", [sel, sib])
            except Exception:
                pos, total = -1, 0
            if pos >= 0:
                lo = max(0, pos - int(ctx.get("above", 0)))
                hi = min(total - 1, pos + int(ctx.get("below", 0)))
                for i in (lo, hi):
                    try:
                        nb = self.page.locator(sib).nth(i).bounding_box()
                        if nb:
                            boxes.append(nb)
                    except Exception:
                        pass

        if step.get("include_header"):
            try:
                hb = self.page.locator(step["include_header"]).first.bounding_box()
                if hb:
                    boxes.append(hb)
            except Exception:
                pass

        pad = int(step.get("padding", 8))
        x0 = min(v["x"] for v in boxes) - pad
        y0 = min(v["y"] for v in boxes) - pad
        x1 = max(v["x"] + v["width"] for v in boxes) + pad
        y1 = max(v["y"] + v["height"] for v in boxes) + pad
        vw = self.page.viewport_size or {"width": 1600, "height": 900}
        return {"x": max(0, x0), "y": max(0, y0),
                "width": min(x1 - x0, vw["width"]), "height": y1 - y0}

    def _shoot_failure(self, cid, step):
        """실패 증빙. 실패한 스텝의 요소 주변만 잘라 원본 그대로 찍는다.

        1600px 화면 전체를 썸네일로 줄이면 글자를 읽을 수 없다. 요소가 없으면 화면 전체.
        크기 · 위치 판정(expect_box)만 표시 없이는 읽을 수 없어 테두리 표시본을 한 장 더 남긴다.
        """
        try:
            self.fail_url = self.page.url
        except Exception:
            self.fail_url = ""
        st = None
        refs = []
        if step and step.get("selector"):
            st = expand_tokens(step, self.spec["meta"].get("qa_prefix", ""), self.run_id)
            refs = [st.get(k) for k in ("below", "above", "left_of", "right_of", "inside", "align_to")
                    if st.get(k)]
            anchor = self._anchor_of(st)
            st = {"selector": st["selector"], "padding": step.get("padding", 60),
                  "extra": refs + ([anchor] if anchor else [])}
        self._shoot("{}_FAIL".format(cid), st)
        if step and step.get("action") == "expect_box":
            sels = [st["selector"]] + refs
            js = """(sels) => sels.forEach((s, i) => { const e = document.querySelector(s);
                if (e) { e.dataset.qaOutline = e.style.outline || '';
                e.style.outline = (i ? '2px dashed #6b7280' : '2px solid #c0362c'); } })"""
            undo = """(sels) => sels.forEach((s) => { const e = document.querySelector(s);
                if (e && 'qaOutline' in e.dataset) { e.style.outline = e.dataset.qaOutline;
                delete e.dataset.qaOutline; } })"""
            try:
                self.page.evaluate(js, sels)
                # 표시본도 대상과 기준 요소를 합친 영역만. 화면 전체는 대부분 흰 여백이다
                self._shoot("{}_FAIL_표시".format(cid), {"selector": st["selector"], "padding": 40,
                                                       "extra": refs})
            finally:
                self.page.evaluate(undo, sels)

    def _box(self, sel, timeout):
        loc = self.page.locator(sel).first
        loc.wait_for(state="visible", timeout=timeout)
        b = loc.bounding_box()
        if not b:
            raise StepFailure("위치를 잡을 수 없음: {}".format(sel))
        return b

    def _expect_box(self, step):
        """크기와 기준 요소와의 상대 위치를 본다.

        px 고정값이나 "A 아래에 B" 같은 위치 요건을 매번 expect_js 로 짜면 좌표 계산을
        잘못 적어 거짓 통과가 나기 쉽다. 위치는 절대 좌표가 아니라 기준 요소와의 관계로 판정한다.
        """
        sel, tol = step["selector"], step.get("tol", 1)
        timeout = step.get("timeout", 10000)
        b = self._box(sel, timeout)
        errs = []

        for key, ko in (("width", "너비"), ("height", "높이")):
            if key in step:
                lo, hi = _range(step[key], tol)
                if not lo <= b[key] <= hi:
                    errs.append("{} {:.0f}px (기대 {})".format(ko, b[key], _range_text(step[key])))

        first_ref = None
        for rel, ko in REL_KO.items():
            ref = step.get(rel)
            if not ref:
                continue
            rb = self._box(ref, timeout)
            first_ref = first_ref or (ref, rb)
            if rel == "inside":
                ok = (b["x"] >= rb["x"] - tol and b["y"] >= rb["y"] - tol
                      and b["x"] + b["width"] <= rb["x"] + rb["width"] + tol
                      and b["y"] + b["height"] <= rb["y"] + rb["height"] + tol)
                if not ok:
                    errs.append("{} 밖으로 벗어남".format(ref))
                continue
            gap = {"below": b["y"] - (rb["y"] + rb["height"]),
                   "above": rb["y"] - (b["y"] + b["height"]),
                   "right_of": b["x"] - (rb["x"] + rb["width"]),
                   "left_of": rb["x"] - (b["x"] + b["width"])}[rel]
            if gap < -tol:
                errs.append("{} {}에 있지 않음 (경계에서 {:.0f}px 안쪽)".format(ref, ko, -gap))
            elif "gap" in step:
                lo, hi = _range(step["gap"], tol)
                if not lo <= gap <= hi:
                    errs.append("{} 과의 간격 {:.0f}px (기대 {})".format(
                        ref, gap, _range_text(step["gap"])))

        if step.get("align"):
            ref = step.get("align_to")
            if ref:
                rb = self._box(ref, timeout)
            elif first_ref:
                ref, rb = first_ref
            else:
                raise StepFailure("align 은 align_to 또는 below·above 등 기준 요소가 필요하다")
            for a in (step["align"] if isinstance(step["align"], list) else [step["align"]]):
                d = _edge(b, a) - _edge(rb, a)
                if abs(d) > tol:
                    errs.append("{} 과 {} 정렬 어긋남 ({:+.0f}px)".format(ref, ALIGN_KO[a], d))

        if errs:
            raise AssertFail("크기·위치 불일치 - {}: {}".format(sel, " / ".join(errs)))

    # -- 기다림 · 기준 요소 ------------------------------------

    def _until(self, check, timeout):
        """check() 가 None 을 돌려줄 때까지 기다린다. 끝내 안 되면 마지막 사유를 돌려준다.

        화면이 늦게 그려지는 경우("0건" 뒤에 "12건")에 바로 판정하면 거짓 실패가 난다.
        """
        end = time.time() + timeout / 1000.0
        while True:
            err = check()
            if err is None or time.time() >= end:
                return err
            self.page.wait_for_timeout(150)

    def _hold(self, violation, step):
        """'없어야 함' 판정. 없어질 때까지 기다린 뒤(settle) 관찰 구간(hold) 내내 없어야 통과.

        한 번 보고 통과시키면 늦게 그려지는 문구를 놓치고, 목록을 다시 그리는 순간 잠깐 0개가
        된 것을 통과로 본다. 처음 본 것으로 바로 실패시키면 필터 적용 뒤 늦게 빠지는 행이
        거짓 실패가 된다. 관찰이 끝나면 기준 요소(anchor)가 아직 있는지 다시 본다.
        """
        err = self._until(violation, step.get("settle", 5000))
        if err:
            return err
        end = time.time() + step.get("hold", 1500) / 1000.0
        while time.time() < end:
            self.page.wait_for_timeout(150)
            err = violation()
            if err:
                return err
        anchor = self._anchor_of(step)
        if anchor:
            try:
                still = self.page.locator(anchor).first.is_visible()
            except Exception:
                still = False
            if not still:
                raise StepFailure(REASON_ANCHOR_GONE.format(anchor))
        return None

    def _anchor_of(self, step):
        """스텝 anchor, 없으면 케이스 anchor. body · html · :root 는 늘 보여 기준이 못 되므로 anchor 없음으로 친다
        (셀렉터가 낡은 '요소 없음' 이 미노출 결함 FAIL 로 바뀌지 않게)."""
        a = step.get("anchor") or self.anchor
        if a and str(a).strip().lower() in NO_ANCHORS:
            return None
        return a

    def _anchor_visible(self, step, timeout):
        """anchor 가 보일 때까지 기다린다. 없으면 False."""
        anchor = self._anchor_of(step)
        if not anchor:
            return False
        try:
            self.page.locator(anchor).first.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    def _require_anchor(self, step, timeout):
        """'없어야 함' 판정 전에 그 영역이 다 그려졌는지 anchor 로 확인한다.

        anchor 가 있는데 안 보이면 화면이 덜 그려졌거나 셀렉터가 낡은 것이라 판정하지 않는다.
        """
        anchor = self._anchor_of(step)
        if anchor and not self._anchor_visible(step, timeout):
            raise StepFailure(REASON_ANCHOR_HIDDEN.format(anchor))
        return bool(anchor)

    def _missing(self, sel, step, what):
        """요소가 0개일 때. anchor 가 보이면 '페이지는 정상인데 이 요소만 없다' 이므로 FAIL."""
        anchor = self._anchor_of(step)
        if anchor and self._anchor_visible(step, 1000):
            raise AssertFail("{} 없음: {} (기준 요소 {} 는 보임)".format(what, sel, anchor))
        raise StepFailure(REASON_NOT_FOUND.format(sel))

    # -- 쓰기 판정 (실행 직전) ---------------------------------

    def _guard_click(self, step, sel, timeout):
        """누르기 직전에 화면에 그려진 글자로 blocklist · 쓰기 여부를 다시 보고, 그 요소를 돌려준다.

        스펙의 셀렉터가 #approve 처럼 글자를 안 담고 있으면 스펙만으로는 쓰기 버튼인지 모른다.
        누를 수 있게 될 때까지 기다린 뒤(trial) 글자를 읽고, 읽은 요소 그대로 누른다.
        셀렉터로 다시 찾아 누르면 "준비 중" 이던 버튼이 그 사이 "발송" 으로 바뀌어도 눌린다.
        """
        loc = self.page.locator(sel).first
        loc.click(trial=True, timeout=timeout)
        h = loc.element_handle(timeout=timeout)
        try:
            got = h.evaluate(GUARD_TEXTS_JS, False)
            texts, up = got["own"], got["up"]
        except Exception:
            texts, up = [], []
        # blocklist 는 그 요소를 감싼 버튼 · 링크 글자까지 본다 ("#send .ico" 처럼 버튼 안 아이콘을 누르는 셀렉터).
        # 쓰기 힌트는 요소 자신의 글자만 본다 (행 · 카드 클릭이 안에 든 글자로 쓰기 판정되지 않게)
        hit = runtime_block_hit(texts + up, self.blocklist)
        if hit:
            raise WriteBlocked(REASON_RUNTIME_BLOCKLIST.format(hit), BLOCK_BLOCKLIST)
        if "write" in step:
            return h
        hint = runtime_write_hint(texts)
        if hint:
            self.step_write = True
        if hint and not self.allow_writes:
            shown = norm(" ".join(t for t in texts if t))[:40]
            raise WriteBlocked(REASON_RUNTIME_WRITE.format(shown=shown, hint=hint), BLOCK_UNDECLARED)
        return h

    def _guard_press(self, sel, key, timeout):
        """Enter · Space 를 누르기 전에 blocklist 를 본다. 누를 요소 핸들을 돌려준다 (selector 없으면 None).

        입력칸에서 Enter 를 치면 폼이 제출되고, 버튼에 포커스가 있으면 Enter · Space 가 그 버튼을 누른다.
        그래서 대상(selector 가 없으면 지금 포커스된 요소)과 그것을 감싼 버튼 · 링크, 속한 폼의 제출 버튼
        글자를 blocklist 에 대조한다. 걸리면 누르지 않고 BLOCKED. 읽은 요소 그대로 누른다.
        """
        h = self.page.locator(sel).first.element_handle(timeout=timeout) if sel else None
        if key.rsplit("+", 1)[-1].lower() not in ACTIVATE_KEYS:
            return h
        target = h
        if target is None:
            try:
                target = self.page.evaluate_handle("document.activeElement").as_element()
            except Exception:
                target = None
        texts = []
        if target is not None:
            try:
                got = target.evaluate(GUARD_TEXTS_JS, True)
                texts = got["own"] + got["up"]
            except Exception:
                texts = []
        hit = runtime_block_hit(texts, self.blocklist)
        if hit:
            raise WriteBlocked(REASON_RUNTIME_BLOCKLIST.format(hit), BLOCK_BLOCKLIST)
        return h

    def _after_nav(self, before):
        """조작으로 주소가 바뀌었으면 로그인이 풀렸는지 본다."""
        try:
            if self.page.url != before:
                self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                self._check_session()
        except SessionExpired:
            raise
        except Exception:
            pass

    def run(self, step, case_id, idx):
        step = expand_tokens(step, self.spec["meta"].get("qa_prefix", ""), self.run_id)
        action = step.get("action")
        if action not in ALL_ACTIONS:
            raise StepFailure("정의되지 않은 action: {}".format(action))

        sel = step.get("selector")
        timeout = step.get("timeout", 10000)
        # 확인창 취소 기록은 바로 다음 expect_dialog · expect_no_dialog 만 쓸 수 있다 (둘이 직접 소비)
        if action not in ("expect_dialog", "expect_no_dialog"):
            self.check_dismissed()
        self.check_blocked()
        if action in CLEARS_DIALOGS:
            self.dialogs = []
        # GET 쓰기 의심 신호: 조작이 시작되면 켜고 다음 goto 까지 둔다 (늦게 · 다른 사이트로 나가는 GET 포함)
        if action == "goto":
            self.watch_get = False
        elif action in INTERACTION_ACTIONS or action == "download":
            self.watch_get = True
        before = self.page.url

        if action == "goto":
            self.page.goto(self._url(step["url"]), wait_until="domcontentloaded")
            self.page.wait_for_timeout(600)
            self._check_session()

        elif action == "wait":
            if sel:
                self.page.wait_for_selector(sel, timeout=timeout)
            else:
                self.page.wait_for_timeout(step.get("ms", 1000))

        elif action == "download":
            # 엑셀 내보내기는 관리자 화면 단골 요건인데 "자동화 불가"로 빠지기 쉽다.
            # 받아서 파싱하면 행수·컬럼·순서까지 화면과 대조된다.
            h = self._guard_click(step, sel, step.get("click_timeout", 10000))
            dl_dir = self.shot_dir.parent / "downloads"
            dl_dir.mkdir(parents=True, exist_ok=True)
            # expect_download 로 기다리면 내보내기 요청이 막혔을 때도 120초를 다 기다린다.
            # 받은 파일과 막힌 요청을 같이 보면서 기다린다
            got = []

            def on_dl(d):
                got.append(d)
            self.page.on("download", on_dl)
            try:
                h.click(timeout=step.get("click_timeout", 10000))
                end = time.time() + step.get("timeout", 120000) / 1000.0
                while not got:
                    if self.blocked:
                        self.check_blocked()
                    if time.time() >= end:
                        raise StepFailure(REASON_DOWNLOAD_TIMEOUT.format(
                            step.get("timeout", 120000) // 1000), timeout=True)
                    self.page.wait_for_timeout(500)
            finally:
                self.page.remove_listener("download", on_dl)
            dl = got[0]
            name = step.get("save_as") or dl.suggested_filename or "download"
            if "." not in name:
                name += pathlib.PurePath(dl.suggested_filename or "x.bin").suffix
            target = dl_dir / re.sub(r'[\\/:*?"<>|]', "_", name)
            dl.save_as(str(target))
            self.files[step.get("save_as") or name] = target

        elif action == "expect_file":
            key = step["file"]
            path = self.files.get(key)
            if path is None or not path.exists():
                raise StepFailure("받아둔 파일이 없음: {} (download 스텝이 먼저 와야 한다)".format(key))
            rows = read_table(path)
            if not rows:
                raise AssertFail("파일에서 행을 못 읽음: {}".format(path.name))

            if step.get("headers"):
                got = [c.replace(" ", "") for c in rows[0]]
                want = [c.replace(" ", "") for c in step["headers"]]
                if got[:len(want)] != want:
                    raise AssertFail("헤더 불일치 - 기대:{} / 실제:{}".format(want, got[:len(want)]))

            body = len(rows) - (1 if step.get("headers") else 0)
            if "min_rows" in step and body < step["min_rows"]:
                raise AssertFail("행 수 미달 - 기대:{}행 이상 / 실제:{}행".format(step["min_rows"], body))
            if "row_count_js" in step:
                # 운영 데이터는 계속 바뀌므로 기대값을 화면에서 읽어 비교한다
                expected = self._eval_readonly(step["row_count_js"])
                if body != expected:
                    raise AssertFail(
                        "행 수 불일치 - 화면 {}행 / 파일 {}행".format(expected, body))
            # 운영 데이터 원본(거래처 · 재고)이 기획 폴더에 남지 않게 판정이 끝나면 지운다.
            # 실패하면 위에서 예외로 빠지므로 증빙으로 남는다.
            try:
                path.unlink()
                self.files.pop(key, None)
            except Exception:
                pass

        elif action == "screenshot":
            return self._shoot("{}_{:02d}_{}".format(case_id, idx, step.get("name", "shot")), step)

        elif action in ("click", "dblclick"):
            h = self._guard_click(step, sel, timeout)
            if action == "click":
                h.click(timeout=timeout)
                self.page.wait_for_timeout(500)
            else:
                # 인라인 편집은 보통 더블클릭으로 input 이 생긴다
                h.dblclick(timeout=timeout)
                self.page.wait_for_timeout(400)
            # 조회용이라 write:false 로 둔 버튼이 쓰기 요청을 보냈다. 빈 폼 제출 검증 케이스면 검증 누락 후보다.
            # 요청 분류(allow_post)로 풀 문제가 아니므로 일반 차단 사유와 나눠 적는다
            if step.get("write") is False and self.blocked:
                lines, self.blocked = self.blocked, []
                raise StepFailure(REASON_WRITE_FALSE_SENT.format(" / ".join(lines[:3])))
            self._after_nav(before)

        elif action == "press":
            key = step["key"]
            h = self._guard_press(sel, key, timeout)
            if h is not None:
                h.press(key, timeout=timeout)
            else:
                self.page.keyboard.press(key)
            self.page.wait_for_timeout(400)
            self._after_nav(before)

        elif action == "type":
            # fill 과 달리 기존 값을 지우지 않고 키 입력을 흉내낸다.
            # 글자수 제한처럼 입력 중 동작하는 검증에는 이쪽이 맞다.
            value = step.get("text", "")
            # 탭은 포커스를 다음 요소로 옮겨 뒤 글자(공백)가 그 버튼을 누를 수 있다. 쓸 일이 없어 거부한다
            if "\t" in value:
                raise StepFailure(REASON_TYPE_TAB)
            self._warn_prefix(value, case_id, idx)
            if step.get("clear"):
                self.page.fill(sel, "")
            # 줄바꿈은 Enter 로 눌려 폼을 제출한다. press Enter 처럼 폼 제출 버튼 글자를 blocklist 에 먼저 대조한다
            if "\n" in value or "\r" in value:
                h = self._guard_press(sel, "Enter", timeout)
                h.type(value, delay=step.get("delay", 20))
            else:
                self.page.type(sel, value, delay=step.get("delay", 20))

        elif action == "hover":
            # locator.hover()는 셀 안 인라인 span에서 hit-test를 오판해 타임아웃이 난다.
            # 요소 중심 좌표로 직접 마우스를 옮기는 방식이 안정적이다.
            loc = self.page.locator(sel).first
            # scroll_into_view_if_needed 는 "보이기만 하면" 멈춘다. 요소가 뷰포트
            # 하단에 걸쳐 있으면 아래로 열리는 툴팁이 렌더 영역 밖이라 거짓 실패가 난다.
            loc.scroll_into_view_if_needed(timeout=timeout)
            loc.evaluate("e => e.scrollIntoView({block:'center', inline:'center'})")
            self.page.wait_for_timeout(150)
            box = loc.bounding_box()
            if not box:
                raise StepFailure("호버 대상의 위치를 잡을 수 없음: {}".format(sel))
            self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            self.page.wait_for_timeout(step.get("ms", 800))

        elif action == "fill":
            value = step.get("value", "")
            if is_write_step(step):
                self._warn_prefix(value, case_id, idx)
            self.page.fill(sel, value, timeout=timeout)

        elif action == "select":
            self.page.select_option(sel, step["value"], timeout=timeout)

        elif action == "check":
            self.page.check(sel, timeout=timeout)

        elif action == "uncheck":
            self.page.uncheck(sel, timeout=timeout)

        elif action == "upload":
            try:
                f = find_fixture(self.workspace, step["file"])
            except FileNotFoundError as e:
                raise StepFailure(str(e))
            self.page.set_input_files(sel, str(f), timeout=timeout)
            self.page.wait_for_timeout(800)

        elif action == "expect_visible":
            loc = self.page.locator(sel)
            try:
                loc.first.wait_for(state="visible", timeout=step.get("timeout", 7000))
            except PWTimeout:
                if loc.count() == 0:
                    self._missing(sel, step, "보여야 할 요소가")
                raise AssertFail("보여야 할 요소가 숨겨져 있음: {}".format(sel))

        elif action == "expect_hidden":
            has_anchor = self._require_anchor(step, step.get("timeout", 7000))
            loc = self.page.locator(sel)

            def shown():
                try:
                    if loc.count() and loc.first.is_visible():
                        return "숨겨져야 할 요소가 보임: {}".format(sel)
                except Exception:
                    pass
                return None
            err = self._hold(shown, step)
            if err:
                raise AssertFail(err)
            if loc.count() == 0 and not has_anchor:
                raise StepFailure(REASON_ABSENCE_UNKNOWN.format(sel))

        elif action == "expect_text":
            loc = self.page.locator(sel)
            positive = any(k in step for k in ("contains", "equals", "matches"))
            if not positive:
                # not_contains 단독 = 부재 판정. expect_hidden · count:0 과 같은 경로로 본다:
                # 요소가 0개여도 기다리지 않고, anchor 가 보이면 통과 · anchor 가 없으면 판정 불가
                has_anchor = self._require_anchor(step, timeout)

                def bad():
                    ts = loc.all_inner_texts()     # 기다리지 않음 (0개면 빈 목록)
                    for i, t in enumerate(ts if step.get("all") else ts[:1]):
                        err = text_mismatch(t, step)
                        if err:
                            return ("{}번째 행: ".format(i + 1) if step.get("all") else "") + err
                    return None
                err = self._hold(bad, step)
                if err:
                    raise AssertFail(err)
                if loc.count() == 0 and not has_anchor:
                    raise StepFailure(REASON_ABSENCE_UNKNOWN.format(sel))
                self.check_blocked()
                return None
            try:
                loc.first.wait_for(state="attached", timeout=timeout)
            except PWTimeout:
                self._missing(sel, step, "문구 요소가")
            if "not_contains" in step:
                self._require_anchor(step, timeout)

            def texts():
                # 목록을 다시 그리다 요소가 떨어지면 기본 30초를 기다리므로 짧게 끊는다
                return loc.all_inner_texts() if step.get("all") else [loc.first.inner_text(timeout=1000)]

            def check():
                for i, t in enumerate(texts()):
                    err = text_mismatch(t, step)
                    if err:
                        return ("{}번째 행: ".format(i + 1) if step.get("all") else "") + err
                return None
            # 있어야 하는 문구는 그려질 때까지 기다린다. 없어야 하는 문구는 관찰 구간 내내 없어야 한다
            if positive:
                err = self._until(check, step.get("settle", 5000))
                if err:
                    raise AssertFail(err)
            if "not_contains" in step:
                err = self._hold(check, step)
                if err:
                    raise AssertFail(err)

        elif action == "expect_count":
            loc = self.page.locator(sel)
            if is_absence_check(step):
                has_anchor = self._require_anchor(step, timeout)
                err = self._hold(lambda: count_mismatch(loc.count(), step), step)
                if err:
                    raise AssertFail(err)
                if loc.count() == 0 and not has_anchor:
                    raise StepFailure(REASON_ABSENCE_UNKNOWN.format(sel))
            else:
                err = self._until(lambda: count_mismatch(loc.count(), step), step.get("settle", 5000))
                if err:
                    if loc.count() == 0:
                        self._missing(sel, step, "개수를 셀 요소가")
                    raise AssertFail(err)

        elif action == "expect_attr":
            loc = self.page.locator(sel)
            try:
                loc.first.wait_for(state="attached", timeout=timeout)
            except PWTimeout:
                self._missing(sel, step, "속성을 볼 요소가")

            def check():
                val = loc.first.get_attribute(step["attr"], timeout=1000) or ""
                if "equals" in step and val != step["equals"]:
                    return "{} 불일치 - 기대:'{}' / 실제:'{}'".format(step["attr"], step["equals"], val)
                if "contains" in step and step["contains"] not in val:
                    return "{}에 '{}' 없음 - 실제:'{}'".format(step["attr"], step["contains"], val)
                return None
            err = self._until(check, step.get("settle", 3000))
            if err:
                raise AssertFail(err)

        elif action == "expect_dialog":
            # contains(부분일치) · equals(완전일치, 토씨까지) · matches(정규식). 알럿이 여럿이면 하나라도 맞으면 통과
            want = {k: step[k] for k in ("contains", "equals", "matches") if k in step}
            if not want:
                raise StepFailure("expect_dialog 에 contains · equals · matches 중 하나가 필요하다")
            desc = " · ".join("{} '{}'".format(k, v) for k, v in want.items())

            def check():
                if not self.dialogs:
                    return "알럿이 떠야 하는데 안 뜸 - 기대 문구: {}".format(desc)
                if any(text_mismatch(m, want) is None for m in self.dialogs):
                    return None
                return "알럿 문구 불일치 - 기대: {} / 실제:'{}'".format(desc, " | ".join(self.dialogs))
            err = self._until(check, step.get("timeout", 5000))
            # 취소 기록은 여기서 소비한다. 확인창을 취소했으면 문구가 맞든 다르든 개입으로 남긴다.
            # 기대 문구가 확인을 눌러야 뜨는 다음 알럿이면 취소 때문에 안 뜬 것이고, 러너는 확인창 문구 자체가
            # 틀린 경우와 가를 수 없다. 그래서 문구 불일치도 점검 필요(ERROR)로 두고, 문구가 맞아도 이후 화면은
            # 실제 사용(확인을 누름)과 다르므로 이후의 판정 실패도 점검 필요다. 취소 기록이 없을 때의 불일치만 FAIL
            cancelled, self.dismissed = bool(self.dismissed), []
            if cancelled:
                self.interfered.add(INTERFERE_DIALOG)
            if err:
                raise AssertFail(err)

        elif action == "expect_no_dialog":
            # 알럿은 조작 뒤 조금 늦게 뜰 수 있다. 바로 보면 "안 떴다" 로 거짓 통과한다
            self.page.wait_for_timeout(step.get("ms", 2000))
            # 확인창이 떴으면 그 자체가 기대와 다른 것(FAIL). 취소 기록은 여기서 소비한다
            self.dismissed = []
            if self.dialogs:
                raise AssertFail("알럿이 뜨면 안 되는데 뜸: {}".format(" | ".join(self.dialogs)))

        elif action == "expect_box":
            self._expect_box(step)

        elif action == "expect_overflow":
            loc = self.page.locator(sel)
            try:
                loc.first.wait_for(state="visible", timeout=timeout)
            except PWTimeout:
                self._missing(sel, step, "말줄임을 볼 요소가")
            st = loc.first.evaluate("""e => ({over: e.scrollWidth > e.clientWidth + 1
                || e.scrollHeight > e.clientHeight + 1,
                ellipsis: getComputedStyle(e).textOverflow === 'ellipsis'
                || parseInt(getComputedStyle(e).webkitLineClamp || '0') > 0})""")
            if step.get("ellipsis", True):
                if not (st["over"] and st["ellipsis"]):
                    raise AssertFail("말줄임 안 됨: {} ({})".format(
                        sel, "글자가 칸 안에 다 들어감" if not st["over"] else "넘친 글자가 잘리지 않음"))
            elif st["over"]:
                raise AssertFail("글자가 칸을 넘침: {}".format(sel))

        elif action == "expect_js":
            # CSS로 표현 안 되는 판정(노출 순서·계산값 비교)에만 쓴다. 읽기 전용 강제.
            value = self._eval_readonly(step["js"])

            # {ok, detail} 을 돌려주면 ok 로 판정하고 detail 을 사유에 그대로 싣는다.
            if isinstance(value, dict) and "ok" in value:
                if not value["ok"]:
                    detail = value.get("detail", "")
                    raise AssertFail("{}{}".format(
                        step.get("desc", "expect_js 불일치"),
                        " - {}".format(detail) if detail else ""))
                return None

            expected = step.get("equals", True)
            if value != expected:
                raise AssertFail("{} - 기대:{} / 실제:{}".format(
                    step.get("desc", "expect_js 불일치"), expected, value))

        self.check_blocked()
        return None

    def _eval_readonly(self, js):
        """expect_js · row_count_js 평가. 금지 패턴을 먼저 거르고, 평가하는 동안은 서버 쓰기를 전부 막는다."""
        flat = js.replace(" ", "")
        for bad in JS_MUTATION_PATTERNS:
            if bad.replace(" ", "") in flat:
                raise StepFailure("expect_js 는 읽기 전용만 허용 - 금지 패턴 '{}' 발견".format(bad))
        self.js_guard = True
        try:
            value = self.page.evaluate(js)
            self.page.wait_for_timeout(200)   # 평가가 남긴 요청이 이 구간 안에 걸리게
        finally:
            self.js_guard = False
        if self.blocked:
            lines, self.blocked = self.blocked, []
            raise StepFailure(REASON_JS_BLOCKED.format(" / ".join(lines[:3])))
        return value


# -- 케이스 실행 ------------------------------------------------

INTERACTION_ACTIONS = {"click", "dblclick", "press", "fill", "type", "select", "check",
                       "uncheck", "upload", "hover"}


def case_write_steps(case):
    """스펙만 보고 쓰기로 판정되는 스텝들 (본 스텝 + cleanup)."""
    out = []
    for phase, steps in (("test", case.get("steps", [])), ("cleanup", case.get("cleanup", []))):
        for i, st in enumerate(steps, 1):
            if is_write_step(st):
                out.append((phase, i, st))
    return out


def case_writes(case):
    """쓰기가 일어나는 케이스인지. 선언(writes)이 빠졌어도 쓰기 스텝이 있으면 쓰기 케이스다."""
    return bool(case.get("writes")) or bool(case_write_steps(case))


def step_label(step, qa_prefix=""):
    """스텝 한 줄. 재현 절차에 그대로 쓰이므로 입력값 · 키도 담는다.

    운영 데이터에 쓰는 값은 QA 프리픽스가 붙은 것만 보이고, 나머지는 글자 수만 적는다.
    """
    act = step.get("action")
    tgt = step.get("selector", step.get("url", step.get("name", "")))
    extra = ""
    if act in ("fill", "type", "select"):
        v = str(step.get("value", step.get("text", "")))
        if (qa_prefix and qa_prefix in v) or not is_write_step(step):
            extra = ' "{}"'.format(v if len(v) <= 40 else v[:30] + "…({}자)".format(len(v)))
        else:
            extra = " (값 {}자)".format(len(v))
    elif act == "press":
        extra = " [{}]".format(step.get("key", ""))
    elif act == "upload":
        extra = " ({})".format(step.get("file", ""))
    return "{} {}{}".format(act, tgt, extra)


def diagnose_chain(runner, case, result, dry_run):
    """chains 케이스가 실패했을 때, 이어서 해서 깨진 건지 원래 깨진 건지 가린다.

    실패한 스텝을 **직전 동작 하나만 앞세워** 새 페이지에서 다시 밟아본다.
    통과하면 앞선 동작들이 남긴 상태가 원인이고, 그대로 실패하면 기능 자체 문제다.

    쓰기를 다시 하게 되는 경우(GET 쓰기 의심이 난 케이스 포함)는 진단하지 않는다. dry-run 실행도
    애초에 기획대로 밟은 게 아니라 진단할 수 없다.
    """
    if dry_run or case_writes(case) or runner.get_suspects:
        return None
    steps = case["steps"]
    failed = next((s["n"] for s in result["steps"]
                   if s["phase"] == "test" and s["status"] == "FAIL"), None)
    if not failed:
        return None
    idx = failed - 1                                   # 실패 스텝의 0-기반 위치

    first_goto = next((s for s in steps if s.get("action") == "goto"), None)
    last_act = next((i for i in range(idx - 1, -1, -1)
                     if steps[i].get("action") in INTERACTION_ACTIONS), None)
    if first_goto is None or last_act is None:
        return None                                    # 되짚을 선행 동작이 없다
    replay = [first_goto] + steps[last_act:idx + 1]
    if any(is_write_step(s) for s in replay):
        return None

    old_page, old_dialogs = runner.page, runner.dialogs
    old_interfered = set(runner.interfered)
    runner.opening = True
    try:
        pg = old_page.context.new_page()
    finally:
        runner.opening = False
    runner.attach(pg)
    runner.page, runner.dialogs = pg, []
    st = None
    try:
        for n, st in enumerate(replay, 1):
            runner.run(st, case["id"] + "_solo", n)
        verdict = "단독 재현: 통과 → 이어서 조작할 때만 발생"
    except SessionExpired:
        raise
    except AssertFail:
        # 마지막 스텝의 판정이 단독으로도 어긋났을 때만 결론을 낸다
        verdict = "단독 재현: 실패 → 연속 사용과 무관한 결함" if st is replay[-1] else None
    except Exception:
        # 시간 초과 · 요청 차단 · 쓰기 차단은 화면 판정까지 못 간 것이라 결론을 내지 않는다
        verdict = None
    finally:
        runner.page, runner.dialogs = old_page, old_dialogs
        runner.blocked, runner.dismissed = [], []
        runner.interfered = old_interfered
        try:
            pg.close()
        except Exception:
            pass
    return verdict


def final_status(exc, steps, interfered, dismissed, blocked_log):
    """케이스 본 스텝이 끝난 예외를 (상태, 사유) 로 바꾼다. 브라우저 없이 단위 테스트한다.

    exc 가 None 이면 통과. steps(스텝 기록)는 지금 판정에는 쓰지 않는다.
    화면이 기대와 다르면 FAIL, 요소를 못 찾았거나 시간 초과 · 설정 오류면 ERROR(점검 필요).
    ERROR 는 개발 전달에 넣지 않는다. 셀렉터가 낡아서 난 실패를 결함으로 넘기지 않기 위함이다.
    dismissed 는 아직 소비되지 않은 취소 기록이다 (판정 스텝 도중에 늦게 떠서 취소한 확인창).
    """
    if exc is None:
        return "PASS", ""
    if isinstance(exc, SessionExpired):
        return "ERROR", REASON_SESSION.format(exc)
    if isinstance(exc, WriteBlocked):
        return "BLOCKED", str(exc)
    if isinstance(exc, PreconditionUnmet):
        # 운영 데이터가 바뀌어 검수할 대상이 없는 것. 결함도 스크립트 오류도 아니다
        return "NA", REASON_NA.format(exc)
    reason = _reason(exc)
    if not isinstance(exc, AssertFail):
        return "ERROR", reason
    # 안전망이 요청을 막았거나 확인창을 취소했으면 화면 흐름이 실제 사용과 다르다.
    # 그 뒤의 판정 실패는 결함이라고 할 수 없으므로 개발 전달로 보내지 않는다
    what = set(interfered) | ({INTERFERE_DIALOG} if dismissed else set())
    if what:
        return "ERROR", REASON_INTERFERED.format(
            what=" · ".join(sorted(what)), reason=reason,
            blocked=REASON_INTERFERED_BLOCKED.format(" / ".join(blocked_log[:2])) if blocked_log else "")
    return "FAIL", reason


def merge_retry(first, second):
    """1차 결과(first)와 재실행 결과(second)를 합친 새 결과. 두 dict 는 바꾸지 않는다.

    first["shots"] 는 _try1 로 이름을 바꿔 둔 1차 스크린샷이어야 한다.
    """
    r = dict(second)
    r["duration"] = round(first["duration"] + second["duration"], 1)
    r["try1"] = {"status": first["status"], "reason": first["reason"], "shots": first["shots"]}
    r["shots"] = first["shots"] + second["shots"]
    # 1차의 GET 쓰기 의심 기록은 재실행이 그 지점까지 못 가도 남긴다
    sus = first.get("get_suspects", [])
    sus = sus + [x for x in second.get("get_suspects", []) if x not in sus]
    if sus:
        r["get_suspects"] = sus[:10]
    if r["status"] == "PASS" and first["status"] == "FAIL":
        # 화면이 기대와 달랐다가 다시 보니 맞았다. 통과로 덮지 않고 기획팀이 본다
        r.update(status="FLAKY", reason=REASON_FLAKY.format(first["reason"]))
    elif r["status"] == "PASS":
        r["flaky"] = first["reason"]
    else:
        r["retried"] = True
        if first["status"] == "FAIL" and r["status"] == "ERROR":
            # 재실행이 판정까지 못 갔다고 1차의 판정 실패를 지우지 않는다. 상태는 점검 필요 그대로
            r["reason"] = REASON_RETRY_ERROR.format(first=first["reason"][:150], second=r["reason"])
    return r


def run_case(runner, case, blocklist, dry_run):
    """케이스 하나 실행. 결과 dict 반환."""
    cid = case["id"]
    result = {
        "id": cid,
        "title": case["title"],
        "requirement": case.get("requirement", ""),
        "source": case.get("source", ""),
        # 실패했을 때 개발팀에 넘길 건지 기획팀이 먼저 볼 건지를 가르는 값
        "kind": case.get("kind", "defect"),
        # 웍스 등록용 로그에 쓰는 값. area 는 묶음 제목, fix 는 오류 한 줄
        "area": case.get("area", ""),
        "fix": case.get("fix", ""),
        "writes": case_writes(case),
        "approved": case.get("approved", False),
        # 치명 · 주요 · 경미. 개발 전달 목록에서 같은 영역 안의 순서를 정한다
        "severity": case.get("severity", ""),
        # 이 케이스가 검증하는 요건 ID (스펙 최상위 requirements). 요건 커버리지 계산용
        "req": case.get("req") or [],
        "status": "PASS",
        "reason": "",
        "steps": [],
        "shots": [],
        "signals": [],
        "duration": 0,
    }
    t0 = time.time()
    approved_write = bool(case.get("writes") and case.get("approved") and not dry_run)
    # 승인된 운영 쓰기 케이스에는 내장 blocklist(발송 · 결제 · 발주 · 일괄삭제)를 더한다
    eff_block = effective_blocklist(blocklist, approved_write, runner.spec["meta"].get("blocklist_off"))

    # -- 게이트 1: blocklist (스펙 문자열) --
    for step in case["steps"] + case.get("cleanup", []):
        hit = static_block_hit(step, eff_block)
        if hit:
            result.update(status="BLOCKED", reason=REASON_BLOCKLIST.format(hit), blocked_by=BLOCK_BLOCKLIST)
            return result

    # -- 게이트 2: 쓰기 선언 · 승인 --
    # 승인은 케이스 선언(writes)이 아니라 스텝 단위로 본다. 선언이 빠진 채 쓰기 스텝이 있으면
    # 승인 절차를 거치지 않은 쓰기이므로 실행하지 않는다.
    wsteps = case_write_steps(case)
    if wsteps and not case.get("writes"):
        phase, i, st = wsteps[0]
        result.update(status="BLOCKED", reason=REASON_UNDECLARED.format(
            phase=REASON_UNDECLARED_CLEANUP if phase == "cleanup" else "", n=i, label=step_label(st)),
            blocked_by=BLOCK_UNDECLARED)
        return result
    if case.get("writes"):
        if dry_run:
            result.update(status="SKIPPED", reason=REASON_DRY_RUN)
            return result
        if not case.get("approved"):
            result.update(status="SKIPPED", reason=REASON_UNAPPROVED)
            return result
        if not case.get("cleanup"):
            result.update(status="BLOCKED", reason=REASON_NO_CLEANUP, blocked_by=BLOCK_NO_CLEANUP)
            return result
        # 앞 케이스가 남긴 타이머 · 화면의 요청이 쓰기 허용 구간에 섞이지 않게, 새로 접속한 뒤에만 연다
        if (case["steps"] or [{}])[0].get("action") != "goto":
            result.update(status="BLOCKED", reason=REASON_FIRST_GOTO, blocked_by=BLOCK_FIRST_GOTO)
            return result

    # 새로 접속하는 케이스는 앞 문서를 먼저 내린다. 앞 문서의 타이머 요청 · 늦은 알럿이 이 케이스의
    # 첫 goto 도중(다음 페이지 응답을 기다리는 동안)에 나가 이 케이스 기록에 섞이지 않게 한다
    if (case["steps"] or [{}])[0].get("action") == "goto":
        try:
            runner.page.goto("about:blank")
            runner.page.wait_for_timeout(100)
        except Exception:
            pass

    shots_before = len(runner.shots)
    runner.signals, runner.blocked, runner.dismissed = [], [], []
    runner.get_suspects, runner.interfered, runner.fail_url = [], set(), ""
    runner.blocked_log = []
    runner.dialogs = []
    runner.anchor = case.get("anchor")
    runner.blocklist = eff_block
    runner.allow_writes = False
    runner.step_write = False
    runner.watch_get = False
    soft_fails = []
    state = {"failed_step": None, "opened": False}
    qa_prefix = runner.qa_prefix

    def exec_steps(steps, phase):
        for i, step in enumerate(steps, 1):
            label = step_label(step, qa_prefix)
            act = step.get("action")
            write = is_write_step(step)
            if step.get("precondition") and write:
                raise StepFailure(REASON_PRE_WRITE.format(label))
            # 확인창은 쓰기 스텝부터 다음 조작이 시작될 때까지 수락한다 (늦게 뜨는 확인창 대비).
            # download 도 조작이다. 쓰기 스텝 뒤 내보내기 버튼이 띄운 확인창까지 수락하지 않게 여기서 끊는다
            if write or act in INTERACTION_ACTIONS or act in ("goto", "download"):
                runner.step_write = write
            try:
                runner.run(step, cid, i)
                result["steps"].append({"phase": phase, "n": i, "label": label,
                                        "action": act,
                                        "status": "OK", "write": runner.step_write and (
                                            write or act in INTERACTION_ACTIONS), "msg": ""})
            except (SessionExpired, WriteBlocked):
                raise
            except Exception as e:
                msg = _reason(e)
                result["steps"].append({"phase": phase, "n": i, "label": label,
                                        "action": act, "pre": bool(step.get("precondition")),
                                        "status": "FAIL", "write": write, "msg": msg})
                if phase == "test" and step.get("precondition"):
                    # 안전망이 요청을 막았거나 확인창을 취소했으면 조건이 안 맞는 건지 알 수 없다.
                    # 조건 미충족(NA)으로 두면 "운영 데이터가 바뀜" 으로 안내되고 연속 NA 경고까지 뜬다
                    if isinstance(e, AssertFail) and (runner.interfered or runner.blocked or runner.dismissed):
                        what = set(runner.interfered) | ({INTERFERE_DIALOG} if runner.dismissed else set())
                        if runner.blocked and not what:
                            what.add(INTERFERE_REQUEST)
                        logs = runner.blocked_log or runner.blocked
                        raise StepFailure(REASON_PRE_INTERFERED.format(
                            what=" · ".join(sorted(what)), reason=msg,
                            blocked=REASON_INTERFERED_BLOCKED.format(" / ".join(logs[:2])) if logs else ""))
                    # 조건이 안 맞는 건지, 화면이 안 그려졌거나 셀렉터가 낡은 건지 anchor 로 가른다
                    if isinstance(e, AssertFail) and runner._anchor_visible(step, 2000):
                        raise PreconditionUnmet(msg)
                    raise StepFailure(REASON_PRE_UNKNOWN.format(msg), timeout=is_timeout(e))
                state["failed_step"] = step
                # soft: 판정이 틀려도 멈추지 않고 다음 검증까지 본다. 끝나고 한꺼번에 실패 처리
                if (phase == "test" and step.get("soft") and isinstance(e, AssertFail)
                        and act.startswith("expect_")):
                    soft_fails.append(msg)
                    continue
                raise
            # 승인 쓰기 케이스는 첫 goto 가 끝난 뒤부터 쓰기 요청을 통과시킨다
            if approved_write and phase == "test" and act == "goto" and not state["opened"]:
                state["opened"] = True
                runner.allow_writes = True
        # 마지막 조작이 남긴 요청 · 확인창도 놓치지 않는다
        runner.check_dismissed()
        runner.check_blocked()

    aborted = None
    try:
        exec_steps(case["steps"], "test")
        if soft_fails:
            raise AssertFail(" / ".join(soft_fails))
    except Exception as e:
        status, reason = final_status(e, result["steps"], runner.interfered, runner.dismissed,
                                      runner.blocked_log)
        result.update(status=status, reason=reason)
        if isinstance(e, SessionExpired):
            # 개별 케이스 실패로 묻지 않고 전체 실행을 멈춘다. 그 전에 정리는 시도한다
            aborted = e
        elif isinstance(e, PreconditionUnmet):
            try:
                runner._shoot("{}_NA".format(cid))
            except Exception:
                pass
        elif isinstance(e, WriteBlocked):
            result["blocked_by"] = e.blocked_by
        else:
            # 셀렉터 갱신 요청 집계용. 사유 문장이 아니라 예외로 가린다 (판정 실패 AssertFail 은 제외)
            if status == "ERROR" and not isinstance(e, AssertFail) and is_timeout(e):
                result["timeout"] = True
                result["err_selector"] = (state["failed_step"] or {}).get("selector", "")
            try:
                runner._shoot_failure(cid, state["failed_step"])
            except Exception:
                pass
            if runner.fail_url:
                result["fail_url"] = runner.fail_url
            # chains 케이스는 "이어서 했기 때문인지" 를 자동으로 가린다
            if case.get("chains") and result["status"] == "FAIL":
                try:
                    diag = diagnose_chain(runner, case, result, dry_run)
                except SessionExpired as e2:
                    aborted, diag = e2, None
                if diag:
                    result["diagnosis"] = diag

    # -- 정리(cleanup)는 본 테스트 실패와 무관하게 항상 시도 --
    # 첫 goto 전에 멈췄으면 쓰기 허용 구간이 열린 적이 없으므로 정리할 것도 없다
    if approved_write and state["opened"]:
        runner.allow_writes = True
        runner.step_write = False
        try:
            exec_steps(case["cleanup"], "cleanup")
        except Exception as e:
            result["unclean"] = True
            # 운영 데이터를 직접 찾아 지울 수 있게 이 케이스가 입력한 값을 남긴다
            result["unclean_inputs"] = [
                str(st.get("value", st.get("text", ""))) for st in case["steps"]
                if st.get("action") in ("fill", "type", "select") and is_write_step(st)][:10]
            result["reason"] += REASON_CLEANUP_FAIL.format(_reason(e)[:150])
            # 정리 실패는 테스트 데이터 문제지 구현 결함이 아니다. 개발 전달로 가지 않게 ERROR 로 둔다
            if result["status"] == "PASS":
                result["status"] = "ERROR"
    runner.allow_writes = False
    runner.step_write = False
    runner.blocklist = list(runner.spec_blocklist)

    result["duration"] = round(time.time() - t0, 1)
    result["shots"] = runner.shots[shots_before:]
    result["signals"] = list(runner.signals)
    if runner.get_suspects:
        result["get_suspects"] = list(runner.get_suspects)
    if aborted is not None:
        result["aborted"] = True
        raise SessionAbort(str(aborted), result)
    return result


def _cp949_ok(ch):
    try:
        ch.encode("cp949")
        return True
    except UnicodeEncodeError:
        return False


def safe_name(s):
    """폴더 이름에 못 쓰는 글자 · 공백을 _ 로.

    run_qa.bat 은 _last_run.txt 의 폴더 이름을 cp949 로 읽으므로 cp949 로 못 쓰는 글자도 _ 로
    바꾼다. 그 밖의 글자는 그대로 두어 예전 실행 폴더 이름(이력)이 바뀌지 않게 한다.
    """
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", str(s)).strip("_") or "qa"
    return "".join(ch if _cp949_ok(ch) else "_" for ch in s)


def _past_runs(out_root, project):
    """같은 프로젝트의 지난 실행 폴더 (results.json 있는 것만), 오래된 순."""
    pat = re.compile(r"^{}_\d{{8}}_\d{{4,6}}$".format(re.escape(safe_name(project))))
    if not out_root.exists():
        return []
    return sorted((d for d in out_root.iterdir()
                   if d.is_dir() and pat.match(d.name) and (d / "results.json").exists()),
                  key=lambda d: d.name)


def _load_run(folder):
    """(실행 정보, 결과 목록). 예전 형식(결과 목록만)은 실행 정보가 빈 dict.

    읽을 수 없는 results.json 은 실행 정보에 _broken 을 달아 돌려준다. 하나가 깨졌다고
    이후 실행이 모두 시작부터 멈추면 안 된다.
    """
    try:
        data = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    except Exception:
        return {"_broken": True}, []
    if isinstance(data, dict):
        return data.get("meta") or {}, data.get("results") or []
    if isinstance(data, list):
        return {}, data
    return {"_broken": True}, []


def _load_results(folder):
    return _load_run(folder)[1]


def baseline_key(spec, spec_path):
    """비교 기준을 고를 때 맞춰야 하는 것: 같은 스펙, 그리고 쓰기 케이스가 있으면 같은 모드.

    쓰기 케이스가 없는 스펙은 dry-run 이든 아니든 같은 케이스를 같은 방식으로 돌리므로
    모드를 가리지 않는다. 가리면 팀원 bat(dry-run)과 Claude 재검수가 서로를 못 본다.
    """
    return {"spec": spec_path.name,
            "ids": {c["id"] for c in spec.get("cases", [])},
            "mode": any(c.get("writes") for c in spec.get("cases", []))}


def _is_baseline(meta, results, dry_run, key=None):
    """비교 기준이 될 수 있는 실행: 전체 실행 · 중단 안 됨 · 같은 스펙 · (필요하면) 같은 모드."""
    if meta.get("_broken") or meta.get("partial") or meta.get("aborted"):
        return False
    key = key or {}
    if key.get("spec"):
        if meta.get("spec"):
            if meta["spec"] != key["spec"]:
                return False
        else:
            # 예전 형식은 스펙 이름이 없다. 케이스 ID 가 절반 이상 겹쳐야 같은 스펙으로 본다
            ids = {r.get("id") for r in results}
            if not ids or len(ids & key.get("ids", set())) * 2 < len(ids):
                return False
    if key.get("mode", True) and "dry_run" in meta and bool(meta["dry_run"]) != bool(dry_run):
        return False
    return True


def _baseline(past, dry_run, key=None):
    for d in reversed(past):
        meta, res = _load_run(d)
        if _is_baseline(meta, res, dry_run, key):
            return d
    return None


def _resolve_run(arg, past, out_root, dry_run=False, key=None):
    """--rerun-failed · --compare 값을 실행 폴더로.

    'latest' 는 이름순 마지막이 아니라 같은 스펙 · 같은 모드의 마지막 전체 실행이다. 한 건만 다시
    돌린 실행, 중간에 멈춘 실행, 같은 프로젝트의 다른 스펙 실행을 기준으로 잡으면
    "다시 돌릴 실패 없음" · "새로 실패" 가 엉뚱하게 나온다.
    """
    if arg == "latest":
        return _baseline(past, dry_run, key)
    for cand in (Path(arg), out_root / arg):
        if (cand / "results.json").exists():
            return cand.resolve()
    return None


def mark_trend(results, prev_results):
    """지난 실행 대비 변화. 개발팀이 고쳤다고 한 뒤 다시 돌릴 때 가장 먼저 보는 정보다.

    "계속 실패" 는 지난번에도 실패(FAIL)였을 때만 쓴다. 점검 필요였다가 실패가 된 건은
    개발팀에는 처음 가는 건이라 "새로 실패" 다.
    """
    prev = {r["id"]: r for r in prev_results}
    for r in results:
        pr = prev.get(r["id"])
        p, now = (pr or {}).get("status"), r["status"]
        if now == "PASS" and p in BAD:
            r["trend"] = "fixed"
        elif now == "FAIL":
            if p == "FAIL":
                r["trend"] = "still"
                # 계속 실패인데 실제 현상이 달라졌으면 개발팀이 일부만 고친 것일 수 있다
                if pr.get("reason") and pr["reason"] != r["reason"]:
                    r["changed"] = pr["reason"]
            else:
                r["trend"] = "new"      # 지난번 통과(회귀) · 점검 필요 · 새 케이스
        elif now == "ERROR" and p is not None:
            # 지난번엔 판정까지 갔는데 이번엔 못 감 (기준이 엄격해졌거나 화면 · 셀렉터가 바뀜)
            r["trend"] = "still_err" if p == "ERROR" else "unjudged"
    return results


def na_streak_warnings(results, past, n=3):
    """같은 케이스가 n회 연속 조건 미충족이면 precondition 이 결함을 가리고 있을 수 있다. [(코드, 문장)]."""
    out = []
    hist = [_load_results(d) for d in past[-(n - 1):]] if n > 1 else []
    for r in results:
        if r["status"] != "NA" or len(hist) < n - 1:
            continue
        if all(any(x["id"] == r["id"] and x["status"] == "NA" for x in h) for h in hist):
            out.append((W_NA_STREAK, WARN_NA_STREAK.format(r["id"], n)))
    return out


# 러너가 실행 폴더에 만드는 것. 이 밖의 파일이 있으면 사람이 넣은 것이라 폴더를 지우지 않는다
RUNNER_FILES = {"results.json", "results.json.tmp", "리포트.html", "개발전달.md", "웍스등록.txt",
                "screenshots", "traces", "downloads"}


def prune_outputs(out_root, project, keep, protect, max_remove=3):
    """지난 실행 폴더 정리. 원칙은 "모르면 남긴다".

    남기는 것: 최근 keep 회, protect(이번 비교 · 재검수 기준), 스펙 × 모드별 마지막 기준 실행,
    러너 버전 정보가 없는 예전 형식 · 읽을 수 없는 실행, 운영 데이터 미정리(unclean)가 있는 실행,
    사람이 넣은 파일이 있는 폴더. 개발 전달 근거(n_dev)는 남기되 내려받은 운영 데이터만 지운다.
    한 번에 max_remove 개까지만 지우고(n_dev 실행의 downloads 폴더 삭제도 같이 센다), 이름을 바꾼 뒤 지워
    반쯤 지워진 폴더가 이력에 섞이지 않게 한다. 돌려주는 것은 통째로 지운 실행 폴더 이름.
    """
    import shutil
    past = _past_runs(out_root, project)
    if keep <= 0 or len(past) <= keep:
        return []
    hold = {d.resolve() for d in past[-keep:]} | set(protect)
    seen = set()
    for d in reversed(past):
        meta, res = _load_run(d)
        k = (meta.get("spec") or "?", bool(meta.get("dry_run")))
        if k not in seen and _is_baseline(meta, res, meta.get("dry_run")):
            seen.add(k)
            hold.add(d.resolve())
    removed = []
    n_rm = 0          # 이번에 지운 폴더 수 (실행 폴더 + downloads 폴더). 보안 프로그램의 대량 삭제 차단 대비
    for d in past[:-keep]:
        if n_rm >= max_remove:
            break
        if d.resolve() in hold:
            continue
        meta, res = _load_run(d)
        if meta.get("_broken") or not meta.get("runner"):
            continue
        if any(r.get("unclean") for r in res):
            continue
        try:
            if any(p.name not in RUNNER_FILES for p in d.iterdir()):
                continue
        except OSError:
            continue
        if meta.get("n_dev"):
            if (d / "downloads").exists():
                shutil.rmtree(d / "downloads", ignore_errors=True)
                n_rm += 1
            continue
        trash = d.with_name("_trash_" + d.name)
        try:
            d.rename(trash)
        except OSError:
            continue
        shutil.rmtree(trash, ignore_errors=True)
        removed.append(d.name)
        n_rm += 1
    return removed


# trace 에서 값을 지울 헤더 · 이름 (쿠키 · 인증 · 세션 · 토큰류)
SENSITIVE_NAME = re.compile(r"cookie|auth|token|session|csrf|xsrf|api[-_]?key|secret|passw", re.I)
_QUERY_VALUE = re.compile(r"([?&#;][^=&#?]+=)[^&#]*")
# 문자열 안 어디에 있든 주소를 찾는다 (로그 문장 "navigating to \"http://…?sid=…\"" · Referer 등)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'\\]+")


def _mask_trace_url(u):
    """주소의 쿼리 · fragment 값을 *** 로. 경로는 그대로 둔다."""
    cut = [i for i in (u.find("?"), u.find("#"), u.find(";")) if i >= 0]
    if not cut:
        return u
    i = min(cut)
    return u[:i] + _QUERY_VALUE.sub(r"\1***", u[i:])


def _async_request(req, mime=""):
    """비동기 요청(XHR · fetch)인지 요청 헤더로 본다. trace 에는 요청 종류(resourceType)가 없다.

    Playwright trace 의 요청 헤더에는 Sec-Fetch-Dest 가 빠져 있다(2차 r2_tok 실측). 그래서 HTML 응답은
    페이지 이동 요청에만 붙는 Upgrade-Insecure-Requests 가 없으면 비동기로 본다 (fetch 로 받은 HTML 조각).
    """
    names = set()
    for h in (req or {}).get("headers") or []:
        if not isinstance(h, dict):
            continue
        name, value = str(h.get("name", "")).lower(), str(h.get("value", "")).strip().lower()
        names.add(name)
        if name == "x-requested-with" or (name == "sec-fetch-dest" and value == "empty"):
            return True
    return "html" in mime and "upgrade-insecure-requests" not in names


def strip_trace(src):
    """동작 기록(trace zip)에서 쿠키 · 인증값을 지운 사본을 만든다. 개발팀이 요청할 때만 쓴다.

    지우는 것: 쿠키 · 저장된 로그인 상태, 이름에 auth · token · session · csrf 등이 든 헤더 값,
    주소의 쿼리 값(fragment 포함, 로그 문장 · 헤더 안 주소까지), 요청 본문, JSON · XML · 비동기 응답 본문.
    남는 것: 화면 스냅샷(HTML) 안의 입력값 · 숨은 토큰, 화면에 보이는 운영 데이터.
    """
    import zipfile
    src = Path(src)

    def clean(o):
        if isinstance(o, dict):
            if "name" in o and "value" in o and SENSITIVE_NAME.search(str(o["name"])):
                o["value"] = "(삭제)"
            for k in list(o):
                if k in ("cookies", "storageState") and isinstance(o[k], (list, dict)):
                    o[k] = [] if isinstance(o[k], list) else {}
                elif k == "queryString" and isinstance(o[k], list):
                    # 요청의 쿼리 목록. 이름(sid · ticket 등)으로 가리지 않고 값은 전부 지운다
                    o[k] = [dict(q, value="***") if isinstance(q, dict) and "value" in q else q for q in o[k]]
                else:
                    o[k] = clean(o[k])
            return o
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, str) and "://" in o:
            return _URL_IN_TEXT.sub(lambda m: _mask_trace_url(m.group(0)), o)
        return o

    def body_refs(o, out):
        """요청 본문 전부와 API 응답 본문(resources/<sha1>)을 지울 대상으로 모은다.

        요청 본문에는 로그인 비밀번호 · 입력값이 들고, API 응답에는 토큰 · 운영 데이터가 든다.
        """
        snap = o.get("snapshot") if isinstance(o, dict) else None
        if not isinstance(snap, dict):
            return
        req = snap.get("request") or {}
        pd = req.get("postData")
        if isinstance(pd, dict):
            if pd.get("_sha1"):
                out.add(pd["_sha1"])
            for k in ("text", "params"):
                if k in pd:
                    pd[k] = "(삭제)"
        content = (snap.get("response") or {}).get("content") or {}
        mime = str(content.get("mimeType", "")).lower()
        # JSON · XML 류 응답, 그리고 비동기 요청의 응답(jQuery .load() 처럼 HTML 조각을 돌려주는 것 포함)
        api = _async_request(req, mime) or any(m in mime for m in ("json", "xml", "text/plain", "form"))
        if api and isinstance(content, dict):
            if content.get("_sha1"):
                out.add(content["_sha1"])
            if "text" in content:
                content["text"] = "(삭제)"

    dst = src.with_name(src.stem + "_전달용.zip")
    with zipfile.ZipFile(src) as zi:
        drop, texts = set(), {}
        for info in zi.infolist():
            if info.filename.endswith((".trace", ".network")):
                lines = []
                for line in zi.read(info.filename).decode("utf-8", "replace").splitlines():
                    try:
                        o = json.loads(line)
                    except Exception:
                        lines.append(line)
                        continue
                    body_refs(o, drop)
                    lines.append(json.dumps(clean(o), ensure_ascii=False))
                texts[info.filename] = "\n".join(lines).encode("utf-8")
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
            for info in zi.infolist():
                if info.filename.startswith("resources/") and info.filename.split("/")[-1] in drop:
                    continue
                zo.writestr(info, texts.get(info.filename) or zi.read(info.filename))
    print("[*] 쿠키 · 인증값을 지운 사본: {}".format(dst))
    print("    지운 것: 쿠키 · 로그인 상태, 인증 · 토큰 · 세션 헤더 값, 주소의 쿼리 값(fragment 포함), "
          "요청 본문, JSON · XML · 비동기 응답 본문")
    print("    남는 것: 화면 스냅샷 안의 입력값 · 숨은 토큰, 화면에 보이는 운영 데이터.")
    print("    넘기기 전에 QA 계정에서 로그아웃해 세션을 끊어 두세요.")
    return 0


# -- 작업 폴더 초기화 -------------------------------------------

# 러너 위치는 bat 을 실행할 때 찾는다. 플러그인을 업데이트하면 설치 경로가 바뀌어
# --init 때 박아 둔 경로는 지워지거나 구버전으로 남는다.
# Keep this block ASCII only - Korean bytes inside for /f can break the cmd parser.
FIND_RUNNER = r"""set "RUNNER="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try {{ $j = Get-Content -Raw -Encoding UTF8 (Join-Path $env:USERPROFILE '.claude\plugins\installed_plugins.json') | ConvertFrom-Json; ($j.plugins.'qa-test@claude-skills' | Select-Object -First 1).installPath }} catch {{}}" 2^>nul`) do set "RUNNER=%%P\skills\qa-test\qa_auto\qa_run.py"
if defined RUNNER if not exist "!RUNNER!" set "RUNNER="
if not defined RUNNER set "RUNNER=%USERPROFILE%\.claude\plugins\marketplaces\claude-skills\plugins\qa-test\skills\qa-test\qa_auto\qa_run.py"
if not exist "!RUNNER!" set "RUNNER={runner}"
if not exist "!RUNNER!" (
    echo   qa-test 플러그인 설치가 필요합니다. Claude Code 에서 설치한 뒤 다시 실행하세요.
    echo.
    pause
    exit /b 1
)
rem Old runners lack safety fixes. Stop unless RUNNER_VERSION is at least {need}.
set "RVER=0"
for /f "tokens=3" %%V in ('findstr /b /c:"RUNNER_VERSION = " "!RUNNER!"') do set "RVER=%%V"
if !RVER! LSS {need} (
    echo   qa-test 플러그인 업데이트가 필요합니다.
    echo   Claude Code 에 "qa-test 플러그인 업데이트해줘" 라고 요청한 뒤 다시 실행하세요. 모르면 이 화면을 담당 기획자에게 보내 주세요.
    echo   [설치된 러너 버전 !RVER! / 필요 버전 {need}]
    echo.
    pause
    exit /b 1
)
rem Use the py launcher, or the default per-user Python when py is missing.
set "PY=py"
py -c "" >nul 2>&1 || set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
if not "!PY!"=="py" if not exist "!PY!" (
    echo   Python 을 찾지 못했습니다. 이 화면을 담당 기획자에게 보내 주세요.
    echo.
    pause
    exit /b 1
)
"""

BAT_TEMPLATE = """@echo off
setlocal enabledelayedexpansion
set PYTHONIOENCODING=cp949:replace
cd /d "%~dp0"

echo.
echo ===============================================
echo   QA 자동화 실행
echo ===============================================
echo.

""" + FIND_RUNNER + """
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
echo   1. 읽기만 실행  (운영 데이터에 쓰는 케이스는 건너뜀)
echo   2. 지난 실행에서 실패한 것만 다시
echo.
echo   운영 데이터에 쓰는 케이스는 담당 기획자가 Claude 로 실행합니다.
echo.
set "MODE="
set /p "MODE=모드 번호 입력 [기본 1]: "
if not defined MODE set "MODE=1"

set "OPT=--dry-run"
if "%MODE%"=="2" set "OPT=--dry-run --rerun-failed latest"

echo.
"!PY!" "!RUNNER!" "!TARGET!" %OPT%
set RESULT=%errorlevel%

rem Open only this run's report. The runner writes the folder name to output\\_last_run.txt.
set "LAST="
if exist "output\\_last_run.txt" set /p LAST=<"output\\_last_run.txt"
if defined LAST if exist "output\\!LAST!\\리포트.html" start "" "%cd%\\output\\!LAST!\\리포트.html"

echo.
if "%RESULT%"=="3" (
    echo   [!] 실행이 중간에 멈췄습니다. 리포트 맨 위 안내를 확인하세요.
    echo       로그인 세션 만료면 login_qa.bat 을 실행해 다시 로그인한 뒤 이 파일을 다시 실행하세요.
    echo.
)
pause
exit /b %RESULT%
"""


COOKIES_TEMPLATE = """# 크롬 F12 > Application > Cookies 에서 표 전체를 선택해 복사한 뒤
# 이 줄들 아래에 그대로 붙여넣으세요. JSON 으로 고칠 필요 없습니다.
# 컬럼 순서: Name / Value / Domain / Path / ... (탭 구분)
#
# 스펙에는  "cookies": "cookies.txt"  를 넣습니다.
# 로그인 창을 띄울 수 없는 환경에서만 쓰세요. 보통은  --login  으로 충분합니다.
# 세션을 브라우저 정보(UA)에 묶는 사이트는 복사한 쿠키가 안 먹을 수 있습니다.
# 그때는 스펙 meta 에  "user_agent": "<쿠키를 복사한 브라우저의 navigator.userAgent>"  를 넣습니다.
"""


LOGIN_BAT_TEMPLATE = """@echo off
setlocal enabledelayedexpansion
set PYTHONIOENCODING=cp949:replace
cd /d "%~dp0"

echo.
echo ===============================================
echo   로그인 세션 저장
echo ===============================================
echo.

""" + FIND_RUNNER + """
echo   브라우저가 열리면 로그인만 하세요.
echo   로그인이 확인되면 저장하고 창이 저절로 닫힙니다.
echo   그냥 Enter 를 누르면 스펙에 적힌 검수 주소로 로그인합니다.
echo   다른 주소면 입력하세요 (여러 개면 띄어쓰기로 이어서).
echo.

set "TARGET="
set /p TARGET=로그인 주소 [Enter: 스펙 주소]:

"!PY!" "!RUNNER!" --login !TARGET! --ws "%~dp0."

echo.
pause
"""


LOGIN_TIMEOUT = 15 * 60

# 화면이 로그인된 상태인지 본다. 페이지 안에서 읽기만 하므로 탭을 새로 띄우지 않는다.
# 로그인 안 된 신호: 보이는 비밀번호 칸, 또는 글자가 딱 "로그인"인 링크·버튼.
# 리다이렉트 중간의 빈 화면을 로그인으로 오판하지 않도록 본문 길이도 본다.
LOGIN_STATE_JS = """(done) => {
  if (document.readyState !== 'complete' || !document.body) return {ready: false};
  const vis = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const word = /^(로그인|login|log in|sign in|signin)$/i;
  const text = document.body.innerText || '';
  return {
    ready: true,
    pw: [...document.querySelectorAll('input[type=password]')].some(vis),
    loginLink: [...document.querySelectorAll('a,button,input[type=submit],input[type=button]')]
      .some((el) => vis(el) && word.test((el.innerText || el.value || '').trim())),
    len: text.length,
    done: done ? text.includes(done) : null,
  };
}"""


def _host(url):
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _logged_in(page, done_text):
    try:
        st = page.evaluate(LOGIN_STATE_JS, done_text or "")
    except Exception:
        return False
    if not st.get("ready"):
        return False
    if done_text:
        return bool(st.get("done"))
    return not st["pw"] and not st["loginLink"] and st["len"] >= 200


def _snapshot(ctx):
    """storage_state 형식으로 세션을 모은다.

    ctx.storage_state() 는 열려 있지 않은 출처의 localStorage 를 읽으려고 숨은 탭을 띄웠다
    닫는다. 헤드풀 창에서 이걸 주기적으로 부르면 화면이 계속 깜빡이므로 쓰지 않는다.
    쿠키는 ctx.cookies(), localStorage 는 지금 열린 탭에서만 읽는다.
    """
    origins = {}
    for pg in ctx.pages:
        if pg.is_closed():
            continue
        try:
            o = pg.evaluate("""() => ({origin: location.origin,
                localStorage: Object.keys(localStorage).map(k => ({name: k, value: localStorage.getItem(k)}))})""")
        except Exception:
            continue
        if o["origin"].startswith("http"):
            origins[o["origin"]] = o
    return {"cookies": ctx.cookies(), "origins": list(origins.values())}


def _ua_path(auth):
    return auth.with_name(auth.stem + ".meta.json")


def do_login(urls, ws, done_text=None):
    """로그인 창을 띄우고, 로그인이 끝나면 세션을 저장한 뒤 창을 스스로 닫는다.

    사용자 동선은 "로그인만 한다" 로 끝난다. 창을 직접 닫을 필요도, 콘솔로 돌아갈 필요도 없다.
    주소를 여러 개 주면 하나가 로그인되는 대로 같은 탭을 다음 주소로 보낸다
    (예: 프론트 + 어드민). 기존 auth.json 이 있으면 불러와서 시작하므로, 이미 살아 있는
    사이트는 바로 통과하고 빠진 사이트만 로그인하면 된다.

    로그인 당시 브라우저의 UA 를 auth.meta.json 에 같이 남긴다. 세션을 UA 에 묶어두는
    사이트가 있어서(블루닥), 헤드리스 실행 때 UA 가 달라지면 쿠키가 살아 있어도 비로그인으로
    보인다. 러너는 이 파일이 있으면 같은 UA 로 실행한다.
    """
    ws.mkdir(parents=True, exist_ok=True)
    auth = ws / "auth.json"
    print("[*] 로그인 창을 엽니다: {}".format(" -> ".join(urls)))
    print("    로그인만 하세요. 로그인이 확인되면 저장하고 창이 저절로 닫힙니다.")

    done_sites, snap, ua = [], None, None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx_args = {"viewport": {"width": 1600, "height": 1000}}
        if auth.exists():
            ctx_args["storage_state"] = str(auth)
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        # UA는 이동 전에 읽는다 (첫 주소가 로그인 페이지로 리다이렉트되면 evaluate 가 컨텍스트 파기로 죽음)
        ua = page.evaluate("navigator.userAgent")
        page.goto(urls[0], wait_until="domcontentloaded", timeout=120000)

        idx, streak, start = 0, 0, time.time()
        try:
            while time.time() - start < LOGIN_TIMEOUT:
                pages = [pg for pg in ctx.pages if not pg.is_closed()]
                if not pages:
                    break                                   # 사용자가 창을 닫음
                try:
                    snap = _snapshot(ctx)
                except Exception:
                    break
                # 같은 사이트 판정은 안전망과 같은 함수 (IP 주소는 주소 전체로 비교)
                site = site_of(_host(urls[idx]))
                hit = next((pg for pg in pages
                            if site_of(_host(pg.url)) == site and _logged_in(pg, done_text)), None)
                streak = streak + 1 if hit else 0
                if streak >= 2:                             # 두 번 연속이어야 확정 (중간 화면 오판 방지)
                    print("[*] 로그인 확인: {}".format(_host(urls[idx])))
                    done_sites.append(urls[idx])
                    idx, streak = idx + 1, 0
                    if idx >= len(urls):
                        snap = _snapshot(ctx)
                        break
                    hit.goto(urls[idx], wait_until="domcontentloaded", timeout=120000)
                    print("    다음 로그인: {}".format(urls[idx]))
                time.sleep(1.5)
        except KeyboardInterrupt:
            pass
        try:
            browser.close()
        except Exception:
            pass

    if not snap or not snap["cookies"]:
        print("[!] 세션이 저장되지 않았습니다. 다시 실행해 로그인하세요.")
        return 1
    auth.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    _ua_path(auth).write_text(json.dumps({"user_agent": ua}, ensure_ascii=False), encoding="utf-8")
    print("[*] 저장 완료: {} (쿠키 {}건)".format(auth, len(snap["cookies"])))
    missing = [u for u in urls if u not in done_sites]
    if missing:
        print("[!] 로그인 확인 전에 창이 닫혔거나 시간이 지났습니다: {}".format(", ".join(missing)))
        print("    이 주소는 로그인이 안 된 상태로 저장됐을 수 있습니다.")
        return 1
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
    bat.write_text(BAT_TEMPLATE.format(runner=SKILL_DIR / "qa_run.py", need=RUNNER_VERSION),
                   encoding="cp949", errors="replace")

    lbat = ws / "login_qa.bat"
    lbat.write_text(LOGIN_BAT_TEMPLATE.format(runner=SKILL_DIR / "qa_run.py", need=RUNNER_VERSION),
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
            "auth.meta.json\n"
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
    print("    run_qa.bat    더블클릭 실행")
    print("    login_qa.bat  로그인 세션 저장 (터미널 없이)")
    print("    cookies.txt  (선택) 개발자도구 쿠키 표 붙여넣기용")
    print("")
    print("[*] 로그인이 필요한 화면이면:")
    print('    py "{}" --login <URL> --ws "{}"'.format(SKILL_DIR / "qa_run.py", ws))
    return 0


def _context_args(conf, name, meta, spec_path, workspace):
    """세션 설정 하나를 브라우저 컨텍스트 인자로 바꾼다. 인증 파일이 없으면 실행 전에 멈춘다."""
    args = {"viewport": conf.get("viewport") or meta.get("viewport", DEFAULT_VIEWPORT)}
    label = " [{}]".format(conf.get("label") or name) if name else ""

    # auth: --login 으로 저장한 세션(쿠키 + localStorage). 쿠키 복붙보다 이쪽을 권한다.
    if conf.get("auth"):
        auth_path = None
        for base in (spec_path.parent, workspace):
            cand = (base / conf["auth"]).resolve()
            if cand.exists():
                auth_path = cand
                break
        if auth_path is None:
            print("[!] 인증 파일 없음{}: {} — `--login <URL>` 로 먼저 로그인하세요.".format(label, conf["auth"]))
            sys.exit(1)
        args["storage_state"] = str(auth_path)
        print("[*] 세션 사용{}: {}".format(label, auth_path.name))
        # 세션을 UA 에 묶는 사이트가 있어 로그인 당시 UA 로 맞춘다 (do_login 참고)
        ua_file = _ua_path(auth_path)
        if not conf.get("user_agent") and ua_file.exists():
            saved_ua = json.loads(ua_file.read_text(encoding="utf-8")).get("user_agent")
            if saved_ua:
                args["user_agent"] = saved_ua
    elif name and not conf.get("cookies"):
        print("[*] 세션 사용{}: 비로그인".format(label))

    if conf.get("user_agent"):
        args["user_agent"] = conf["user_agent"]
    return args


# 탐색 페이지에서 화면 글자가 blocklist 에 걸리는 요소의 클릭을 막는다 (캡처 단계에서 먼저 잡음).
# 러너의 _guard_click 과 같은 기준: 요소 자신과 그것을 감싼 버튼 · 링크의 글자
GUARD_CLICK_INIT_JS = """(() => {
  const PATS = %s;
  const norm = s => String(s || '').replace(/\\s+/g, ' ').trim();
  const read = x => (""" + LABEL_JS + """)(x).map(norm).filter(Boolean);
  const hit = el => {
    const b = el.closest('button,a,[role=button],input[type=submit],input[type=button],input[type=image],area,label,[onclick]');
    const ts = read(el).concat(b && b !== el ? read(b) : []);
    return PATS.find(([k, n]) => n && ts.some(t => k === 'exact' ? t === n : t.includes(n)));
  };
  document.addEventListener('click', ev => {
    const el = ev.target instanceof Element ? ev.target : null;
    const p = el && el !== document.body && hit(el);
    if (p) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      console.warn('[qa-test] blocklist 버튼 클릭을 막음: ' + p[1]);
    }
  }, true);
})();"""


@contextmanager
def guarded_page(spec_path, headed=True, session=""):
    """셀렉터 매핑(2단계) 때 운영 화면을 직접 열어 볼 페이지. 러너 실행과 같은 안전망을 건다.

        with guarded_page("<기획_폴더>/qa_auto/specs/x.json") as page:
            page.goto("https://...")

    - 스펙의 세션(auth · cookies · user_agent)으로 연다. session 으로 meta.sessions 의 이름을 고른다.
    - 서버로 가는 쓰기 요청(POST · PUT · DELETE 등)은 막는다 (meta.allow_post 의 조회 POST 만 통과).
    - 확인창(confirm · prompt)은 모두 취소하고, 알럿은 닫는다.
    - 스펙 blocklist 의 주소 패턴은 메서드와 무관하게 막고, 스펙 blocklist 와 내장 blocklist(발송 · 결제 ·
      발주 · 일괄삭제)의 화면 글자에 걸리는 버튼은 클릭을 막는다.
    - 막지 못하는 것은 러너와 같다 (blocklist 에 없는 GET 쓰기, 웹소켓). 팝업 창의 요청은 막힌다.
    결과 폴더 · 스크린샷은 만들지 않는다.
    """
    spec_path = Path(spec_path).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    meta = spec["meta"]
    workspace = resolve_spec_workspace(spec_path)
    sessions = {"": {k: meta[k] for k in SESSION_KEYS if k in meta}}
    sessions.update(meta.get("sessions") or {})
    if session not in sessions:
        raise ValueError("meta.sessions 에 없는 session: {}".format(session))
    conf = sessions[session]
    args = _context_args(conf, session, meta, spec_path, workspace)
    # 탐색은 승인 케이스가 아니므로 meta.blocklist_off 로 푼 항목도 막는다
    blocklist = effective_blocklist(spec.get("blocklist", []), True)
    pats = []
    for p in blocklist:
        tp = _text_pattern(p) or ("exact", norm(p))
        pats.append(list(tp))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            ctx = browser.new_context(**args)
            if conf.get("cookies"):
                ctx.add_cookies(load_cookies(spec_path, conf["cookies"], workspace))
            ctx.add_init_script(GUARD_CLICK_INIT_JS % json.dumps(pats, ensure_ascii=False))
            page = ctx.new_page()
            runner = Runner(page, spec, spec_path, None, workspace)
            runner.blocklist = blocklist
            runner.guard_context(ctx)
            ctx.on("page", runner.on_new_page)
            yield page
        finally:
            try:
                browser.close()
            except Exception:
                pass


# -- 메인 ------------------------------------------------------

def resolve_spec_workspace(spec_path):
    """스킬에 번들된 스펙(_smoke.json 등)을 돌릴 때는 스킬 폴더 대신 현재 폴더에 쓴다."""
    try:
        spec_path.relative_to(SKILL_DIR)
        inside_skill = True
    except ValueError:
        inside_skill = False
    if inside_skill:
        cwd = Path.cwd().resolve()
        try:
            cwd.relative_to(SKILL_DIR.parent.resolve())
            # 스킬 폴더 안에서 돌리면 스킬 폴더에 결과가 쌓인다. 임시 폴더로 보낸다
            import tempfile
            return Path(tempfile.gettempdir()) / "qa-test-smoke"
        except ValueError:
            pass
        return cwd / "qa_auto" if (cwd / "qa_auto").is_dir() else cwd
    return resolve_workspace(spec_path)


def spec_login_urls(ws):
    """작업 폴더 스펙들의 검수 주소. login_qa.bat 에서 주소를 비워 두면 이걸로 로그인한다."""
    urls = []
    for p in sorted((ws / "specs").glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            meta = json.loads(p.read_text(encoding="utf-8")).get("meta", {})
        except Exception:
            continue
        u = str(meta.get("base_url", ""))
        if u.startswith("http") and u not in urls:
            urls.append(u)
    return urls


ICONS = {"PASS": "O", "FAIL": "X", "ERROR": "E", "NA": "~", "SKIPPED": "-",
         "BLOCKED": "!", "FLAKY": "?"}


def save_outputs(spec, results, warnings, out_dir, dry_run, spec_path, prev, run_meta):
    """results.json 을 먼저 쓰고 리포트를 만든다. 리포트 경로, 실패하면 None.

    리포트를 만들다 예외가 나도 결과 · 운영 데이터 미정리(unclean) 기록이 남아야 다음 실행의
    비교 · 정리(prune)가 이 실행을 제대로 다룬다 (results.json 없는 폴더는 지난 실행 목록에서 빠진다).
    """
    write_results(out_dir, results, run_meta)
    try:
        return build_report(spec, results, warnings, out_dir, dry_run, str(spec_path), prev=prev,
                            partial=run_meta["partial"], run_meta=run_meta)
    except Exception as e:
        print("[!] 리포트 생성 실패 - results.json 은 남음: {} ({})".format(out_dir / "results.json", _reason(e)))
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", help="spec.json 경로")
    ap.add_argument("--init", metavar="DIR",
                    help="해당 폴더에 작업 폴더(qa_auto/)와 실행 배치파일을 만든다")
    ap.add_argument("--login", metavar="URL", nargs="*",
                    help="로그인 창을 띄워 세션을 auth.json 으로 저장한다. 여러 주소면 차례로 로그인. "
                         "주소를 비우면 --ws 의 스펙에 적힌 검수 주소")
    ap.add_argument("--done-text", metavar="TEXT",
                    help="--login 에서 로그인 완료로 볼 문구 (예: 로그아웃). 없으면 화면으로 자동 판정")
    ap.add_argument("--ws", metavar="DIR", default="qa_auto",
                    help="--login 이 저장할 작업 폴더 (기본: ./qa_auto)")
    ap.add_argument("--strip-trace", metavar="ZIP",
                    help="동작 기록(trace zip)에서 쿠키 · 인증 헤더를 지운 사본을 만든다")
    ap.add_argument("--dry-run", action="store_true", help="쓰기 케이스 전부 건너뜀")
    ap.add_argument("--headed", action="store_true", help="브라우저 표시")
    ap.add_argument("--case", help="특정 케이스 ID만 실행 (쉼표로 여러 개)")
    ap.add_argument("--rerun-failed", metavar="RUN",
                    help="지난 실행(결과 폴더 또는 latest)에서 실패 · 점검 필요 · 불안정이었던 케이스만 실행")
    ap.add_argument("--compare", metavar="RUN",
                    help="비교 기준 실행 폴더 (기본: 같은 모드의 마지막 전체 실행)")
    ap.add_argument("--no-retry", action="store_true", help="실패 케이스 1회 재실행 끄기")
    ap.add_argument("--no-trace", action="store_true", help="실패 케이스 동작 기록(trace) 끄기")
    args = ap.parse_args()

    if args.init:
        return init_workspace(Path(args.init).resolve())
    if args.login is not None:
        ws = Path(args.ws).resolve()
        urls = args.login or spec_login_urls(ws)
        if not urls:
            print("[!] 로그인 주소가 없습니다. 주소를 입력하거나 specs 폴더에 스펙을 먼저 만드세요.")
            return 1
        return do_login(urls, ws, args.done_text)
    if args.strip_trace:
        return strip_trace(args.strip_trace)
    if not args.spec:
        ap.error("spec 경로가 필요하다 (또는 --init DIR / --login URL)")

    spec_path = Path(args.spec).resolve()
    if not spec_path.exists():
        spec_path = (SKILL_DIR / args.spec).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    workspace = resolve_spec_workspace(spec_path)
    meta = spec["meta"]
    project = safe_name(meta["project"])
    out_root = workspace / "output"
    last_file = out_root / "_last_run.txt"
    try:
        last_file.unlink()          # bat 이 지난 실행 리포트를 이번 것으로 착각해 열지 않게
    except Exception:
        pass

    # 새 키를 모르는 구버전 러너가 조용히 무시하고 돌면 precondition 이 빠진 채 결함이 넘어간다
    need = int(meta.get("runner_min", 0) or 0)
    if need > RUNNER_VERSION:
        print("[!] 이 스펙은 러너 버전 {} 이상이 필요합니다 (설치된 러너: {}).".format(need, RUNNER_VERSION))
        print('    Claude Code 에 "qa-test 플러그인 업데이트해줘" 라고 요청한 뒤 다시 실행하세요.')
        return 1

    # 개발 전달로 가는 값(kind · severity · source)이 틀리면 엉뚱한 건이 개발팀에 간다
    errs = spec_errors(spec)
    if errs:
        print("[!] 스펙 오류 - 실행하지 않았습니다:")
        for e in errs:
            print("    - " + e)
        return 1

    past = _past_runs(out_root, project)
    cases = spec["cases"]
    if args.case:
        ids = [x.strip() for x in args.case.split(",") if x.strip()]
        cases = [c for c in cases if c["id"] in ids]
        missing = [i for i in ids if i not in {c["id"] for c in cases}]
        if missing:
            print("[!] 케이스 없음: {}".format(", ".join(missing)))
            return 1

    # 재검수: 개발팀이 고쳤다고 한 뒤 지난번 실패만 다시 돌린다
    key = baseline_key(spec, spec_path)
    rerun_base = None
    if args.rerun_failed:
        rerun_base = _resolve_run(args.rerun_failed, past, out_root, args.dry_run, key)
        if rerun_base is None:
            print("[!] 다시 돌릴 기준 실행을 찾을 수 없음: {} (같은 스펙의 전체 실행이 필요)".format(
                args.rerun_failed))
            return 1
        bad_ids = {r["id"] for r in _load_results(rerun_base) if r["status"] in RERUN}
        cases = [c for c in cases if c["id"] in bad_ids]
        print("[*] 재검수 기준: {} (실패 · 점검 필요 · 불안정 {}건)".format(rerun_base.name, len(cases)))
        if not cases:
            print("[*] 지난 실행에 다시 돌릴 실패가 없습니다.")
            return 0

    compare_base = (_resolve_run(args.compare, past, out_root, args.dry_run, key) if args.compare
                    else rerun_base or _baseline(past, args.dry_run, key))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / "{}_{}".format(project, run_id)
    shot_dir = out_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    last_file.write_text(out_dir.name, encoding="cp949", errors="replace")

    print("[*] 대상: {}".format(meta["base_url"].replace("{QA_ROOT}", SKILL_DIR.as_uri())))
    print("[*] 작업폴더: {}".format(workspace))
    print("[*] 케이스: {}건{}".format(
        len(cases), "  (DRY-RUN - 쓰기 없음)" if args.dry_run else ""))

    rc = meta.get("residue_check")
    if isinstance(rc, list):
        rc = {"steps": rc}

    # 세션: meta 의 auth/cookies/user_agent/login_check 가 기본 세션이고, meta.sessions 에
    # 이름 붙인 세션을 더 둘 수 있다 ({} 면 비로그인). 케이스의 "session" 으로 고른다.
    sessions = {"": {k: meta[k] for k in SESSION_KEYS if k in meta}}
    sessions[""]["label"] = meta.get("session_label", "")
    sessions.update(meta.get("sessions") or {})
    rc_session = (rc or {}).get("session", "")
    unknown = sorted({c.get("session") for c in cases if c.get("session", "") not in sessions}
                     | ({rc_session} if rc_session not in sessions else set()))
    if unknown:
        print("[!] meta.sessions 에 없는 session: {}".format(", ".join(unknown)))
        return 1
    used = []
    for name in [c.get("session", "") for c in cases] + ([rc_session] if rc else []):
        if name not in used:
            used.append(name)
    ctx_args = {name: _context_args(sessions[name], name, meta, spec_path, workspace) for name in used}

    trace = not args.no_trace
    trace_dir = out_dir / "traces"
    blocklist = spec.get("blocklist", [])
    results = []
    aborted = None
    runner = Runner(None, spec, spec_path, shot_dir, workspace, run_id)
    runner.warnings += spec_warnings(spec)
    timeout_hits = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        pages, contexts = {}, {}

        def page_for(name):
            """세션별 컨텍스트는 그 세션을 처음 쓰는 케이스에서 만든다.

            페이지가 닫혔으면(사이트가 window.close 를 부름 등) 같은 컨텍스트에서 새로 연다.
            """
            pg = pages.get(name)
            if pg is not None and not pg.is_closed():
                return pg
            if pg is None:
                ctx = browser.new_context(**ctx_args[name])
                runner.guard_context(ctx)
                if sessions[name].get("cookies"):
                    ctx.add_cookies(load_cookies(spec_path, sessions[name]["cookies"], workspace))
                if trace:
                    # 실패 케이스의 동작을 그대로 재생할 수 있게 남긴다. 통과 케이스 기록은 버린다
                    ctx.tracing.start(screenshots=True, snapshots=True)
                ctx.on("page", runner.on_new_page)
                contexts[name] = ctx
            runner.opening = True
            try:
                pg = contexts[name].new_page()
            finally:
                runner.opening = False
            runner.attach(pg)
            pages[name] = pg
            return pg

        def close_others(page):
            """케이스를 시작할 때 지금 페이지 말고 같은 세션에 열린 창(팝업)을 닫는다."""
            for other in list(page.context.pages):
                if other is not page:
                    try:
                        other.close()
                    except Exception:
                        pass

        def keep_try1(r):
            """재실행하면 같은 파일명으로 덮어쓰므로 1차 스크린샷은 _try1 을 붙여 남긴다."""
            kept = []
            for n in r.get("shots", []):
                src = shot_dir / n
                dst = shot_dir / (Path(n).stem + "_try1" + Path(n).suffix)
                try:
                    src.replace(dst)
                    kept.append(dst.name)
                except Exception:
                    pass
            runner.shots = [s for s in runner.shots if s not in r.get("shots", [])] + kept
            return kept

        def run_one(case):
            name = case.get("session", "")
            runner.use_page(page_for(name), sessions[name].get("login_check"))
            close_others(runner.page)
            tracing = runner.page.context.tracing
            if trace:
                tracing.start_chunk(title=case["id"])
            try:
                r = run_case(runner, case, blocklist, args.dry_run)
                sel = r.get("err_selector")
                if sel:
                    timeout_hits[sel] = timeout_hits.get(sel, 0) + 1
                # 한 번 실패로 단정하지 않는다. 서버가 잠깐 느렸던 건지 진짜 결함인지 가린다.
                # 쓰기 케이스 · GET 쓰기 의심이 난 케이스는 운영 데이터를 한 번 더 바꾸게 되므로 재실행하지 않는다.
                # 같은 셀렉터가 여러 케이스에서 시간 초과면 화면 구조가 바뀐 것이라 재실행해도 소용없다.
                if (r["status"] in BAD and not args.no_retry and not r["writes"]
                        and not r.get("get_suspects") and timeout_hits.get(sel, 0) < 2):
                    first = dict(r, shots=keep_try1(r))
                    # 1차가 window.close() 로 페이지를 닫았거나 팝업을 열어 두었으면 그대로 쓰지 않는다
                    runner.use_page(page_for(name), sessions[name].get("login_check"))
                    close_others(runner.page)
                    r = merge_retry(first, run_case(runner, case, blocklist, args.dry_run))
            finally:
                if trace:
                    try:
                        bad = "r" in locals() and r["status"] in BAD + ("FLAKY",)
                        if bad:
                            trace_dir.mkdir(parents=True, exist_ok=True)
                            tname = safe_name(case["id"]) + ".zip"
                            tracing.stop_chunk(path=str(trace_dir / tname))
                            r["trace"] = "traces/" + tname
                        else:
                            tracing.stop_chunk()
                    except Exception:
                        pass
            r["session"] = sessions[name].get("label") or name
            tag = " ({})".format(r["session"]) if r["session"] else ""
            if r.get("flaky"):
                tag += " [재실행 통과]"
            print("  [{}] {}{} {}  {}".format(ICONS[r["status"]], r["id"], tag, r["title"][:50],
                                             r["reason"][:60]))
            return r

        try:
            for n, case in enumerate(cases, 1):
                try:
                    results.append(run_one(case))
                except SessionAbort as e:
                    e.result["session"] = sessions[case.get("session", "")].get("label") or case.get("session", "")
                    results.append(e.result)
                    aborted = {"at": n, "total": len(cases), "case": case["id"], "msg": str(e),
                               "why": "session", "recent_blocked": list(runner.recent_blocked),
                               "note": runner.session_note()}
                    lbl = e.result["session"]
                    # "직전에 막은 요청" 은 콘솔 끝 안내에 한 번만 찍는다
                    print("\n[!] 실행 중단 - {}로그인이 풀림 {}".format(
                        "[{}] ".format(lbl) if lbl else "", _without_note(str(e), aborted["note"])))
                    break
                except Exception as e:
                    # 브라우저가 죽었거나 쿠키 파일이 깨지는 등 케이스 밖의 문제. 그때까지 결과로 리포트는 남긴다
                    aborted = {"at": n, "total": len(cases), "case": case["id"],
                               "msg": _reason(e), "why": "error"}
                    print("\n[!] 실행 중단 - {}: {}".format(case["id"], _reason(e)))
                    break

            # 잔여 데이터 점검: 승인된 쓰기 케이스가 실제로 돈 경우에만 마지막에 한 번
            wrote = any(r["writes"] and r["approved"] and r["status"] not in ("SKIPPED", "BLOCKED")
                        for r in results)
            if rc and wrote and not aborted:
                residue = {"id": "QA-잔여", "title": "테스트 데이터 잔여 확인",
                           "requirement": "쓰기 케이스가 만든 {} 데이터가 남아 있지 않아야 한다".format(
                               runner.qa_prefix),
                           "source": "meta.residue_check", "kind": "qa", "writes": False,
                           "anchor": rc.get("anchor"), "steps": rc.get("steps", [])}
                if rc_session:
                    residue["session"] = rc_session
                if case_write_steps(residue):
                    r = run_case(runner, dict(residue, steps=[]), blocklist, args.dry_run)
                    r.update(status="BLOCKED", reason=REASON_RESIDUE_WRITE, blocked_by=BLOCK_RESIDUE_WRITE)
                else:
                    try:
                        r = run_one(residue)
                    except SessionAbort as e:
                        r = e.result
                if r["status"] != "PASS":
                    runner.warnings.append((W_RESIDUE, WARN_RESIDUE.format(r["reason"])))
                results.append(r)
        except Exception as e:
            if not aborted:
                aborted = {"at": len(results), "total": len(cases), "case": "-",
                           "msg": _reason(e), "why": "error"}
                print("\n[!] 실행 중단 - {}".format(_reason(e)))
        finally:
            try:
                browser.close()
            except Exception:
                pass

    for sel, n in timeout_hits.items():
        if n >= 2:
            runner.warnings.append((W_SELECTOR_UPDATE, WARN_SELECTOR_UPDATE.format(sel, n, spec_path.stem)))
    runner.warnings += na_streak_warnings(results, past)

    prev = None
    if compare_base is not None:
        mark_trend(results, _load_results(compare_base))
        prev = {"name": compare_base.name}

    run_meta = {
        "run_id": run_id, "spec": spec_path.name, "runner": RUNNER_VERSION,
        "dry_run": bool(args.dry_run), "partial": bool(args.case or args.rerun_failed),
        "aborted": bool(aborted), "case_ids": [c["id"] for c in cases],
        "n_dev": len(_dev_items(results)),
    }
    if aborted:
        run_meta["aborted_info"] = aborted
    report_path = save_outputs(spec, results, runner.warnings, out_dir, args.dry_run, spec_path, prev, run_meta)

    # 지난 실행 정리: 최근 N회 · 비교 기준 · 예전 형식 · 사람이 넣은 파일은 남긴다
    protect = {d.resolve() for d in (compare_base, rerun_base, out_dir) if d is not None}
    try:
        removed = prune_outputs(out_root, project, int(meta.get("keep_runs", 10)), protect)
    except Exception as e:
        removed = []
        print("[!] 지난 실행 정리 건너뜀: {}".format(_reason(e)))
    if removed:
        print("[*] 오래된 실행 {}개 정리".format(len(removed)))

    count = {k: sum(1 for r in results if r["status"] == k) for k in ("FAIL", "ERROR")}
    print()
    for line in console_summary(results):
        print(line)
    if prev:
        tr = [r.get("trend") for r in results]
        print("[*] 지난 실행({}) 대비: 수정됨 {} / 계속 실패 {} / 새로 실패 {} / 판정 불가로 바뀜 {} / "
              "계속 점검 필요 {}".format(prev["name"], tr.count("fixed"), tr.count("still"), tr.count("new"),
                                     tr.count("unjudged"), tr.count("still_err")))
    if report_path is not None:
        print("[*] 리포트: {}".format(report_path))
    n_dev = run_meta["n_dev"]
    if n_dev and not aborted:
        print("[*] 개발 전달용 {}건: {}".format(n_dev, out_dir / "개발전달.md"))
    unclean = [r["id"] for r in results if r.get("unclean")]
    if unclean:
        print("[!] 정리되지 않은 쓰기 케이스: {} - 운영 데이터 수동 확인 필요".format(", ".join(unclean)))
    if aborted:
        if aborted.get("why") == "session":
            print("[!] 세션 만료로 {}번째 케이스에서 멈췄습니다. 그때까지 결과로 리포트를 만들었습니다.".format(
                aborted["at"]))
            print("    login_qa.bat (또는 py qa_run.py --login <URL> --ws <작업폴더>) 로 로그인한 뒤 다시 실행하세요.")
            if aborted.get("note"):
                print("    " + aborted["note"])
        else:
            print("[!] {}번째 케이스에서 실행이 멈췄습니다 ({}). 그때까지 결과로 리포트를 만들었습니다.".format(
                aborted["at"], _clip(aborted["msg"], 120)))
        print("    중단된 실행은 개발팀에 전달하지 않습니다.")
        return EXIT_ABORT
    return 1 if count["FAIL"] or count["ERROR"] or report_path is None else 0


if __name__ == "__main__":
    sys.exit(main())
