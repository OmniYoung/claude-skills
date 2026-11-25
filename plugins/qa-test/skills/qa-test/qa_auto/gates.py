"""
쓰기 판정 · blocklist · 문구 판정처럼 브라우저 없이 결정되는 규칙.

qa_run.py 가 쓰고, tests/test_gates.py 가 단위 테스트한다. 운영 서버 안전과 직결되는
판정이라 브라우저를 띄우지 않고도 회귀를 확인할 수 있게 따로 둔다.
"""

import re
from urllib.parse import urlparse

# -- 쓰기 판정 --------------------------------------------------
WRITE_ACTIONS = {"fill", "type", "select", "check", "uncheck", "upload"}
# 버튼 글자에 이 말이 들어 있으면 쓰기로 본다. 부분일치라 넓게 잡힌다.
# 조회용 버튼("확인", "발송 내역" 탭)은 스펙에서 write:false 로 내린다. POST · PUT · DELETE 쓰기는
# 네트워크 안전망이 따로 막지만, GET 으로 보내는 쓰기와 웹소켓은 막지 못한다.
WRITE_HINTS = ["저장", "확인", "등록", "추가", "삭제", "수정", "전송", "발송", "적용", "완료",
               "승인", "반려", "처리", "결제", "주문", "신청", "환불", "출고", "변경", "해제",
               "업로드", "초기화", "일괄"]
READ_KEYS = {"escape", "tab", "arrowup", "arrowdown", "arrowleft", "arrowright",
             "pageup", "pagedown", "home", "end", "shift+tab"}
READ_ACTIONS = {
    "goto", "wait", "screenshot", "hover",
    "expect_visible", "expect_hidden", "expect_text",
    "expect_count", "expect_attr", "expect_dialog", "expect_no_dialog",
    "expect_js", "download", "expect_file", "expect_box", "expect_overflow",
}
CLICK_ACTIONS = {"click", "dblclick"}
ALL_ACTIONS = WRITE_ACTIONS | READ_ACTIONS | CLICK_ACTIONS | {"press"}

# 스텝 안의 문자열 중 blocklist 를 대조할 칸
SCAN_FIELDS = ("selector", "label", "value", "text", "url", "js")

# 안전망이 통과시키는 메서드. GET 으로 쓰기를 하는 레거시 화면은 막지 못하므로
# blocklist 의 주소 패턴(url_block_hit)과 GET 쓰기 의심 신호(get_write_suspect)로 보완한다.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# 내장 blocklist: 발송·결제·발주·일괄삭제(띄어쓴 '일괄 삭제' 포함).
# 승인된 운영 쓰기 케이스 실행 중에는 스펙 blocklist 와 별도로 이 버튼 글자를 항상 막는다.
# 승인 케이스에서는 쓰기 힌트와 네트워크 안전망이 꺼지므로 마지막 보호다.
# 정말 눌러야 하는 스펙만 meta.blocklist_off 에 항목을 그대로 적어 푼다.
DEFAULT_BLOCKLIST = ["text*=발송", "text*=결제", "text*=발주", "text*=일괄삭제", "text*=일괄 삭제"]

# 분석 · 수집 도구. 막기는 하되(운영 통계 오염 방지) 판정에는 쓰지 않는다.
# 화면을 그리는 외부 서비스(상담 채팅 위젯 등)는 넣지 않는다. 막혀서 화면이 달라졌는데 판정에서 빠지면
# 그 뒤 판정 실패가 결함으로 넘어간다. 그런 요청은 스펙의 ignore_requests 에 호스트 형식으로 적는다.
IGNORE_HOSTS = ("google-analytics.com", "analytics.google.com", "googletagmanager.com",
                "doubleclick.net", "clarity.ms", "facebook.net", "wcs.naver.net",
                "wcs.naver.com", "hotjar.com", "hotjar.io", "amplitude.com", "mixpanel.com",
                "segment.io", "sentry.io", "datadoghq.com")
# 호스트 전체가 아니라 수집 경로만 무시하는 것 (호스트, 경로들)
IGNORE_HOST_PATHS = (("facebook.com", ("/tr", "/tr/")),)

# blocklist 의 주소 패턴을 대조하지 않는 요청 종류. 'send' 같은 짧은 단어가 sendbird.min.js 처럼
# 스크립트 · 스타일 파일 이름에 걸려 화면이 안 그려지고 케이스가 줄줄이 점검 필요가 되는 것을 막는다
URL_BLOCK_SKIP_TYPES = ("script", "stylesheet", "font")

# GET 쓰기 의심 신호용 단어 (주소의 경로 · 쿼리). 막는 근거로는 쓰지 않는다.
GET_WRITE_WORDS = ("delete", "remove", "save", "toggle", "withdraw", "cancel", "approve",
                   "update", "insert", "write", "send", "modify", "regist", "confirm", "proc")


def is_write_step(step):
    """스펙만 보고 쓰기 스텝인지 판정 (spec_schema.md '쓰기 판정 규칙').

    write 를 명시하면 그 값이 최우선이다. 클릭은 화면에 그려진 글자로 한 번 더 보므로
    (runtime_write_hint) 여기서 놓친 버튼도 실행 직전에 잡힌다.
    """
    action = step.get("action")
    if "write" in step:
        return bool(step["write"])
    if action in WRITE_ACTIONS:
        return True
    if action in CLICK_ACTIONS:
        target = "{} {}".format(step.get("selector", ""), step.get("label", ""))
        return any(h in target for h in WRITE_HINTS)
    if action == "press":
        return step.get("key", "").lower() not in READ_KEYS
    return False


def _norm(s):
    """공백 · 줄바꿈을 한 칸으로. 화면 글자와 기대 문구를 같은 기준으로 비교한다."""
    return " ".join(str(s or "").split())


def norm(s):
    return _norm(s)


_SEL_TEXT = [
    re.compile(r"""^\s*text\s*=\s*["']?(.*?)["']?\s*$"""),
    re.compile(r""":(?:has-text|text-is|text)\(\s*["'](.*?)["']\s*\)"""),
    re.compile(r"""\[\s*name\s*=\s*["'](.*?)["']\s*(?:[is]\s*)?\]"""),
]


def selector_texts(selector):
    """셀렉터에 적힌 화면 글자. 'text=저장', 'button:has-text("저장")', role=button[name="저장"]."""
    out = []
    for part in str(selector or "").split(">>"):
        for pat in _SEL_TEXT:
            out += [m for m in pat.findall(part) if m]
    return [_norm(t) for t in out]


def _text_pattern(pattern):
    """('exact' | 'part', 글자) 또는 None. text=X 는 완전일치, text*=X 는 부분일치."""
    if pattern.startswith("text*="):
        return "part", _norm(pattern[6:])
    if pattern.startswith("text="):
        return "exact", _norm(pattern[5:])
    return None


def _text_hit(kind, needle, texts):
    if not needle:
        return False
    if kind == "exact":
        return needle in texts
    return any(needle in t for t in texts)


def static_block_hit(step, blocklist):
    """스펙 문자열만 보고 blocklist 에 걸리는지.

    화면 글자 패턴은 두 가지다. `text=발송` 은 글자가 정확히 "발송" 일 때만 막는다
    ("발송 내역" 탭은 통과). `text*=발송` 은 "알림톡 발송" 처럼 그 말이 들어 있기만 해도 막는다.
    그 밖의 패턴(URL 일부, 함수명 등)은 셀렉터 · URL · 입력값 · js 어디에 있어도 막는다.
    """
    for pattern in blocklist or []:
        tp = _text_pattern(pattern)
        if tp:
            texts = selector_texts(step.get("selector", "")) + [_norm(step.get("label", ""))]
            if _text_hit(tp[0], tp[1], [t for t in texts if t]):
                return pattern
        else:
            needle = pattern.lower()
            hay = " ".join(str(step.get(k, "")) for k in SCAN_FIELDS).lower()
            if needle and needle in hay:
                return pattern
    return None


def runtime_block_hit(texts, blocklist):
    """클릭 직전 요소의 화면 글자(innerText · value · aria-label · title)가 blocklist 에 걸리는지.

    글자 패턴이 아닌 것(URL 일부 등)도 화면 글자와 완전히 같으면 막는다.
    """
    cands = [_norm(t) for t in texts if _norm(t)]
    for pattern in blocklist or []:
        tp = _text_pattern(pattern) or ("exact", _norm(pattern))
        if _text_hit(tp[0], tp[1], cands):
            return pattern
    return None


def runtime_write_hint(texts):
    """화면 글자에 쓰기 힌트가 들어 있으면 그 힌트."""
    joined = " ".join(_norm(t) for t in texts)
    return next((h for h in WRITE_HINTS if h in joined), None)


# 같은 버튼을 가리키는 내장 항목 (띄어쓰기만 다름). 하나를 풀면 같이 푼다
_OFF_PAIRS = {"text*=일괄삭제": "text*=일괄 삭제", "text*=일괄 삭제": "text*=일괄삭제"}


def blocklist_off_item(p):
    """meta.blocklist_off 한 항목을 내장 목록 표기로. 접두어 없이 '발주' 라고 적어도 'text*=발주' 로 받는다."""
    p = str(p or "").strip()
    return p if _text_pattern(p) else "text*=" + p


def effective_blocklist(blocklist, approved_write=False, off=None):
    """이 케이스에 적용할 blocklist. 승인된 쓰기 케이스면 내장 목록을 더한다.

    off(meta.blocklist_off) 중 내장 목록에 없는 항목은 무시한다 (그 버튼은 계속 막힌다, 경고로 알림).
    """
    out = list(blocklist or [])
    if approved_write:
        offs = {blocklist_off_item(p) for p in (off or [])}
        offs |= {_OFF_PAIRS[p] for p in offs if p in _OFF_PAIRS}
        out += [p for p in DEFAULT_BLOCKLIST if p not in offs and p not in out]
    return out


def url_block_hit(url, blocklist, resource_type=""):
    """요청 주소가 blocklist 의 주소 패턴(text= · text*= 가 아닌 것)에 걸리는지.

    GET 으로 보내는 쓰기(레거시 delete.php?id= 등)는 메서드로 못 가린다. 기획자가 적어 둔
    주소 패턴만큼은 메서드와 승인 여부에 관계없이 네트워크 단에서 막는다.
    스크립트 · 스타일 · 글꼴 파일 요청(resource_type)은 대조하지 않는다 (쓰기가 아니고, 막으면 화면이 깨진다).
    """
    if resource_type in URL_BLOCK_SKIP_TYPES:
        return None
    low = str(url or "").lower()
    for pattern in blocklist or []:
        if _text_pattern(pattern) or not pattern:
            continue
        if pattern.lower() in low:
            return pattern
    return None


# -- 네트워크 안전망 -------------------------------------------

_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def site_of(url_or_host):
    """같은 사이트 판정용. admin.x.co.kr 과 x.co.kr 을 같은 곳으로 본다. IP 는 주소 전체."""
    host = url_or_host
    if "://" in str(url_or_host):
        try:
            host = urlparse(url_or_host).hostname or ""
        except Exception:
            host = ""
    host = (host or "").lower()
    if _IP.match(host) or ":" in host or "." not in host:
        return host
    labels = host.split(".")
    n = 3 if len(labels) >= 3 and labels[-2] in ("co", "or", "go", "ne", "ac", "re", "pe") else 2
    return ".".join(labels[-n:])


def _path_match(path, pattern):
    """allow_post · ignore_requests 한 항목과 주소 경로 대조. 're:' 로 시작하면 정규식 전체일치."""
    if pattern.startswith("re:"):
        try:
            return re.fullmatch(pattern[3:], path) is not None
        except re.error:
            return False
    return path == pattern


def _path_of(url):
    try:
        return urlparse(url).path or "/"
    except Exception:
        return ""


def split_entry(p):
    """allow_post · ignore_requests 한 항목을 (호스트 또는 None, 경로 패턴) 으로.

    "/admin/list.do" · "re:/api/x" 는 호스트 없음(검수 사이트에만 적용).
    "//ext.api.com/front/boot" · "re://ext.api.com/front/(a|b)" 는 그 호스트에만 적용한다.
    경로 패턴은 정규식이면 're:' 를 앞에 붙여 돌려준다. 호스트 뒤에 경로가 없으면 경로는 "".
    """
    p = str(p)
    is_re = p.startswith("re:")
    body = p[3:] if is_re else p
    if not body.startswith("//"):
        return None, p
    rest = body[2:]
    i = rest.find("/")
    host, path = (rest, "") if i < 0 else (rest[:i], rest[i:])
    return host.lower(), ("re:" + path if is_re and path else path)


def _entry_hit(url, entry, base_site):
    """주소가 항목 하나와 맞는지. 호스트 없는 항목은 검수 사이트(base_site) 요청에만 맞는다.

    base_site 가 None 이면 호스트를 보지 않는다 (예전 호출 호환, 러너는 항상 넘긴다).
    """
    if not entry:
        return False
    host, pat = split_entry(entry)
    try:
        h = (urlparse(url).hostname or "").lower()
    except Exception:
        h = ""
    if host is None:
        if base_site is not None and site_of(h) != base_site:
            return False
    elif h != host.split(":")[0]:
        return False
    return bool(pat) and _path_match(_path_of(url), pat)


def post_allowed(method, url, allow_post, base_site=None):
    """meta.allow_post 에 적은 조회용 POST 인지.

    POST 에만 적용한다(PUT · DELETE 는 조회일 수 없다). 경로 전체가 같거나, 're:' 정규식이
    경로 전체와 맞아야 한다. 쿼리는 보지 않는다. 일부 문자열로 비교하면 공용 진입점 하나를
    적었을 때 그 아래 쓰기 요청까지 다 열린다. 호스트 없는 항목은 검수 사이트에만, "//호스트/경로"
    항목은 그 호스트에만 적용한다 (외부 API 경로를 적었을 때 자기 사이트의 같은 경로가 열리지 않게).
    """
    if str(method).upper() != "POST":
        return False
    return any(_entry_hit(url, p, base_site) for p in (allow_post or []))


def ignored_request(url, ignore_requests=None, base_site=None):
    """판정에 쓰지 않을 요청(분석 도구 · meta.ignore_requests). 막기는 그대로 한다."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if any(host == h or host.endswith("." + h) for h in IGNORE_HOSTS):
        return True
    path = _path_of(url)
    if any((host == h or host.endswith("." + h)) and path in paths for h, paths in IGNORE_HOST_PATHS):
        return True
    return any(_entry_hit(url, p, base_site) for p in (ignore_requests or []))


def get_write_suspect(url):
    """GET 쓰기로 의심되는 주소면 걸린 단어. 신호로만 쓴다."""
    try:
        u = urlparse(url)
        hay = (u.path + "?" + u.query).lower()
    except Exception:
        return None
    return next((w for w in GET_WRITE_WORDS if w in hay), None)


# 정규식의 앞쪽 글자(접두) 뒤에 붙여 보는 쓰기 주소. 하나라도 정규식 전체와 맞으면 넓은 정규식이다
_WRITE_PROBES = ("save", "/save", "save.do", "/delete.do", "x/update.php", "proc")
# 경계 없는 '.' 반복 (.* · .+ · .{n,}) - 그 자리에 쓰기 경로가 다 들어간다
_DOT_REPEAT = re.compile(r"(?<!\\)\.[*+{]")
_RE_SPECIAL = set(".^$*+?{}[]\\|()")
_HOST_OK = re.compile(r"^[a-z0-9.-]+(:\d+)?$")


def _literal_prefix(rx):
    """정규식 앞쪽의 글자 그대로인 부분. 're:/admin/goods/(list|search)\\.do' 면 '/admin/goods/'."""
    out, i = [], 1 if rx.startswith("^") else 0
    while i < len(rx):
        c = rx[i]
        if c == "\\" and i + 1 < len(rx) and not rx[i + 1].isalnum():
            ch, i = rx[i + 1], i + 2
        elif c in _RE_SPECIAL:
            break
        else:
            ch, i = c, i + 1
        if i < len(rx) and rx[i] in "?*{":
            break                       # 바로 뒤에 반복 기호가 붙은 글자는 없을 수도 있다
        out.append(ch)
    return "".join(out)


def _regex_errors(name, p, pat):
    """경로 정규식(re: 뒤) 검증. 넓으면 저장 · 삭제 주소까지 맞는다."""
    try:
        rx = re.compile(pat)
    except re.error as e:
        return ["meta.{}: 정규식 오류 '{}' ({})".format(name, p, e)]
    if _DOT_REPEAT.search(pat):
        return ["meta.{}: '{}' 에 경계 없는 '.*' · '.+' 반복이 있음 - 그 자리에 쓰기 주소가 다 들어간다. "
                "'(list|search)' · '\\d+' 처럼 들어올 값을 정해 적는다".format(name, p)]
    pre = _literal_prefix(pat)
    probe = next((pre + w for w in _WRITE_PROBES if rx.fullmatch(pre + w)), None)
    if rx.fullmatch("/") or rx.fullmatch("/qa/probe/save.do") or probe:
        return ["meta.{}: '{}' 는 넓어서 쓰기 주소까지 맞음{} - 경로를 구체적으로 적는다".format(
            name, p, " (예: {})".format(probe) if probe else "")]
    return []


def path_list_errors(name, items, base_site=None):
    """allow_post · ignore_requests 값 검증. 너무 넓은 값은 쓰기 요청까지 열어 버린다.

    "//호스트/경로" · "re://호스트/정규식" 도 받는다. ignore_requests 의 외부 호스트 항목만 경로 '/'
    (짧은 경로)를 허용한다. 외부 수집기가 루트로 보내는 요청은 경로로 가를 수 없어서다.
    """
    errs = []
    for p in items or []:
        p = str(p)
        host, pat = split_entry(p)
        if host is not None and (not host or not _HOST_OK.match(host)):
            errs.append("meta.{}: '{}' 의 호스트를 읽을 수 없음 - '//api.example.com/경로' 형식".format(name, p))
            continue
        external = host is not None and (base_site is None or site_of(host.split(":")[0]) != base_site)
        if pat.startswith("re:"):
            errs += _regex_errors(name, p, pat[3:])
        elif "?" in pat or not pat.startswith("/"):
            errs.append("meta.{}: '{}' 는 쓸 수 없음 - '/admin/list.do' 처럼 경로 전체를 적는다 "
                        "(쿼리 · 4글자 미만 · '/' 단독 불가)".format(name, p))
        elif len(pat) < 4 and not (name == "ignore_requests" and external):
            errs.append("meta.{}: '{}' 는 쓸 수 없음 - '/admin/list.do' 처럼 경로 전체를 적는다 "
                        "(쿼리 · 4글자 미만 · '/' 단독 불가)".format(name, p))
    return errs


# -- 문구 판정 --------------------------------------------------

def text_mismatch(actual, step):
    """expect_text 판정. 맞으면 None, 어긋나면 사유.

    화면 글자와 기대 문구 모두 공백을 정규화한 뒤 비교한다.
    equals 는 완전일치, matches 는 정규식, contains / not_contains 는 부분일치.
    """
    a = _norm(actual)
    if "equals" in step and a != _norm(step["equals"]):
        return "문구 불일치 - 기대:'{}' / 실제:'{}'".format(_norm(step["equals"]), a[:150])
    if "matches" in step and not re.search(step["matches"], a):
        return "문구 형식 불일치 - 기대 형식:{} / 실제:'{}'".format(step["matches"], a[:150])
    if "contains" in step and _norm(step["contains"]) not in a:
        return "문구 불일치 - 기대:'{}' 포함 / 실제:'{}'".format(_norm(step["contains"]), a[:150])
    if "not_contains" in step and _norm(step["not_contains"]) in a:
        return "있으면 안 되는 문구가 있음 - '{}' / 실제:'{}'".format(
            _norm(step["not_contains"]), a[:150])
    return None


def count_mismatch(n, step):
    if "count" in step and n != step["count"]:
        return "개수 불일치 - 기대:{} / 실제:{}".format(step["count"], n)
    if "min" in step and n < step["min"]:
        return "최소 개수 미달 - 기대:{}개 이상 / 실제:{}".format(step["min"], n)
    if "max" in step and n > step["max"]:
        return "최대 개수 초과 - 기대:{}개 이하 / 실제:{}".format(step["max"], n)
    return None


def is_absence_check(step):
    """'없어야 한다' 판정인지. 셀렉터가 틀리거나 화면이 덜 그려져도 통과해 버리므로
    anchor 를 확인하고 관찰 구간(hold) 동안 계속 없을 때만 통과로 본다."""
    a = step.get("action")
    if a == "expect_hidden":
        return True
    if a == "expect_count" and (step.get("count") == 0 or "max" in step):
        return True
    if a == "expect_text" and "not_contains" in step:
        return True
    return False


# -- 스펙 검증 --------------------------------------------------

KINDS = ("defect", "improve", "gap", "qa")
SEVERITIES = ("", "치명", "주요", "경미")


def spec_errors(spec):
    """실행 전에 멈춰야 하는 스펙 오류 (개발 전달로 가는 필드 · 안전망을 여는 값만 엄격하게)."""
    meta = spec.get("meta", {})
    base_site = site_of(str(meta.get("base_url", "")))
    errs = path_list_errors("allow_post", meta.get("allow_post"), base_site)
    errs += path_list_errors("ignore_requests", meta.get("ignore_requests"), base_site)
    # blocklist_off 오타는 멈추지 않는다 (승인 케이스에만 쓰이는 값이라 팀원 dry-run 까지 막지 않음). 경고로 알림
    for c in spec.get("cases", []):
        cid = c.get("id", "?")
        kind = c.get("kind", "defect")
        if kind not in KINDS:
            errs.append("{}: kind '{}' 는 허용값이 아님 ({})".format(cid, kind, " · ".join(KINDS)))
        sev = c.get("severity", "")
        if sev not in SEVERITIES:
            errs.append("{}: severity '{}' 는 허용값이 아님 (치명 · 주요 · 경미)".format(cid, sev))
        if kind == "defect" and not str(c.get("source", "")).strip():
            errs.append("{}: 결함(defect) 케이스인데 source(기획서 근거)가 비어 있음".format(cid))
    return errs


# 경고 종류 코드. 경고는 (코드, 문장) 으로 다니고 리포트는 코드로 묶는다.
# 문장을 고쳐도 리포트 묶음 · 할 일 버튼이 깨지지 않게 하려는 것이다.
# 스펙 점검(spec_warnings) - 리포트 '스펙 점검' 블록(회색)으로 간다
W_UNKNOWN_REQ = "unknown_req"                  # requirements 에 없는 요건 ID
W_NO_FIX = "no_fix"                            # defect 인데 fix 없음
W_NO_AREA = "no_area"                          # defect 인데 area 없음
W_APPROVED_FIRST_GOTO = "approved_first_goto"  # 승인 쓰기 케이스 첫 스텝이 goto 아님
W_SOFT_POSITION = "soft_position"              # soft 가 expect_* 아닌 스텝에 있음
W_ABSENCE_NO_ANCHOR = "absence_no_anchor"      # '없어야 함' 판정에 anchor 없음
W_PRE_NO_ANCHOR = "precondition_no_anchor"     # precondition 에 anchor 없음
W_BROAD_ANCHOR = "broad_anchor"                # anchor 가 body · #wrap 처럼 페이지 전체
W_SHORT_BLOCK = "short_blocklist"              # 글자가 아닌 blocklist 항목이 5글자 미만
W_BLOCKLIST_OFF = "blocklist_off"              # blocklist_off 항목이 내장 목록에 없음 (무시, 계속 막음)
# 실행 중 경고(qa_run) - 리포트 '확인할 경고' 블록(주황)으로 간다
W_QA_PREFIX = "qa_prefix"                      # 입력값에 QA 프리픽스 없음
W_RESIDUE = "residue"                          # 테스트 데이터가 남아 있을 수 있음 (할 일: 잔여 데이터 확인)
W_SELECTOR_UPDATE = "selector_update"          # 같은 셀렉터가 여러 케이스에서 시간 초과 (할 일: 셀렉터 갱신 요청)
W_NA_STREAK = "na_streak"                      # 같은 케이스가 연속 조건 미충족

# BLOCKED 케이스가 왜 막혔는지 (결과의 blocked_by). 리포트 카드의 안내문을 이 값으로 고른다
BLOCK_BLOCKLIST = "blocklist"          # blocklist 패턴 (스펙 문자열 · 화면 글자)
BLOCK_UNDECLARED = "undeclared"        # writes 선언 없이 쓰기 동작 (스펙 판정 · 화면 글자 힌트)
BLOCK_NO_CLEANUP = "no_cleanup"        # 쓰기 케이스인데 cleanup 없음
BLOCK_FIRST_GOTO = "first_goto"        # 승인 쓰기 케이스 첫 스텝이 goto 아님
BLOCK_RESIDUE_WRITE = "residue_write"  # residue_check 에 쓰기 스텝


# 기준 요소(anchor)로 못 쓰는 페이지 전체 셀렉터. body · html · :root 는 러너가 anchor 없음으로 친다
BROAD_ANCHORS = ("body", "html", ":root", "header", "#header", "#wrap", "#wrapper", "#container",
                 "#content", "#contents", "main", "#main")
NO_ANCHORS = ("body", "html", ":root")


def is_broad_anchor(sel):
    return str(sel or "").strip().lower() in BROAD_ANCHORS


def spec_warnings(spec):
    """멈추지는 않고 리포트에 경고로 남길 것. [(코드, 문장)]."""
    out = []
    meta = spec.get("meta", {})
    for p in meta.get("blocklist_off") or []:
        if blocklist_off_item(p) not in DEFAULT_BLOCKLIST:
            out.append((W_BLOCKLIST_OFF, "meta.blocklist_off '{}': 내장 목록에 없어 무시함 (그 버튼은 계속 막힘) - "
                                         "내장 목록: {}".format(p, " · ".join(DEFAULT_BLOCKLIST))))
    for p in spec.get("blocklist") or []:
        p = str(p)
        if not _text_pattern(p) and len(p.strip()) < 5:
            out.append((W_SHORT_BLOCK, "blocklist '{}': 글자가 아닌 항목이 5글자 미만 - 요청 주소에 부분일치라 "
                                       "엉뚱한 요청까지 막혀 점검 필요가 늘어남. 버튼 글자면 text= 로 적는다".format(p)))
    reqs = {q["id"] for q in spec.get("requirements", [])}
    for c in spec.get("cases", []):
        cid = c.get("id", "?")
        broad = [a for a in [c.get("anchor")] + [st.get("anchor") for st in c.get("steps", [])] if is_broad_anchor(a)]
        for a in dict.fromkeys(broad):
            out.append((W_BROAD_ANCHOR, "{}: anchor '{}' 는 페이지 전체라 기준 요소가 못 됨 - 셀렉터가 낡아도 "
                                        "'요소 없음' 이 결함(FAIL)으로 나갈 수 있음. 판정하는 영역의 표 헤더 · "
                                        "패널 제목을 그 스텝의 anchor 로 단다".format(cid, a)))
        for rid in c.get("req") or []:
            if reqs and rid not in reqs:
                out.append((W_UNKNOWN_REQ, "{}: requirements 에 없는 요건 ID '{}'".format(cid, rid)))
        if c.get("kind", "defect") == "defect":
            if not str(c.get("fix", "")).strip():
                out.append((W_NO_FIX, "{}: fix(웍스 로그 한 줄)가 없어 '영역 오류: 실제 현상' 으로 대신함".format(cid)))
            if not str(c.get("area", "")).strip():
                out.append((W_NO_AREA, "{}: area 가 없어 웍스 로그에서 '기타' 아래로 감".format(cid)))
        if c.get("writes") and c.get("approved"):
            first = (c.get("steps") or [{}])[0]
            if first.get("action") != "goto":
                out.append((W_APPROVED_FIRST_GOTO,
                            "{}: 승인 쓰기 케이스는 첫 스텝이 goto 여야 함 - 이대로면 차단".format(cid)))
        for st in c.get("steps", []):
            act = str(st.get("action", ""))
            if st.get("soft") and not act.startswith("expect_"):
                out.append((W_SOFT_POSITION, "{}: soft 는 expect_* 스텝에만 적용됨 ({})".format(cid, act)))
            anchor = st.get("anchor") or c.get("anchor")
            if is_absence_check(st) and not anchor:
                out.append((W_ABSENCE_NO_ANCHOR,
                            "{}: '없어야 함' 판정({})에 anchor 가 없음 - 요소가 0개면 판정 불가로 처리".format(
                                cid, st.get("selector", ""))))
            if st.get("precondition") and not anchor:
                out.append((W_PRE_NO_ANCHOR,
                            "{}: precondition 에 anchor 가 없음 - 조건이 안 맞으면 점검 필요로 처리".format(cid)))
    return out
