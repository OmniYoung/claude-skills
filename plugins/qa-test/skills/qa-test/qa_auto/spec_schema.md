# spec.json 포맷 정의

`/qa-test` 2단계(셀렉터 매핑)의 산출물이자, `qa_run.py`가 실행하는 입력 파일.
**이 문서는 Claude와 러너 사이의 계약이다. 여기 없는 action은 러너가 거부한다.**

---

## 최상위 구조

```json
{
  "meta": { ... },
  "requirements": [ ... ],
  "blocklist": [ ... ],
  "cases": [ ... ]
}
```

### requirements · 요건 커버리지

1단계에서 뽑은 기획 요건 목록. 케이스의 `req` 와 이어져 리포트 맨 위에 "요건 커버리지" 표가 생긴다.
케이스 → 근거 방향만으로는 **케이스가 안 만들어진 요건**이 보이지 않기 때문에 요건 → 케이스 방향으로 본다.

```json
"requirements": [
  { "id": "REQ-01", "text": "임박재고 칩 건수에 긴급재고 포함", "source": "SB v1.4 > 3p" },
  { "id": "REQ-02", "text": "엑셀 양식 통일", "source": "기획_내용.md > 공통",
    "exclude": "통일 기준 정의 없음 - 기획 확정 후 추가" }
]
```

| 키 | 필수 | 설명 |
|---|---|---|
| `id` | O | `REQ-01` 형식 |
| `text` | O | 요건 원문 (짧게) |
| `source` | - | 근거 위치 |
| `exclude` | - | 검증하지 않는 사유 한 줄. 있으면 "제외" 로 집계 |

리포트에서 요건 상태는 연결된 케이스 결과로 정해진다: 통과 / 미반영(FAIL 있음) / 점검 필요(ERROR 있음) /
일부 통과 / 미확인(승인 대기·조건 미충족뿐) / 미실행 / **미검증(케이스도 제외 사유도 없음)** / 제외.
`requirements` 가 없으면 표를 만들지 않는다 (예전 스펙 그대로 동작).

### meta

| 키 | 필수 | 설명 |
|---|---|---|
| `project` | O | 프로젝트명 (출력 폴더명에 사용) |
| `base_url` | O | 대상 사이트 origin |
| `auth` | - | `--login` 으로 저장한 세션 파일 경로 (보통 `auth.json`). 옆의 `auth.meta.json` 에 든 로그인 당시 UA 도 자동 적용 |
| `cookies` | - | 쿠키 파일 경로. 로그인 창을 띄울 수 없는 상황에서 수동으로 넣을 때 |
| `user_agent` | - | UA 강제 지정. 붙여넣은 쿠키가 UA 에 묶인 사이트에서 안 먹을 때 쿠키를 복사한 브라우저의 UA 를 넣는다 |
| `login_check` | - | 세션 만료 감지 조건. `{"url_contains": "...", "body_contains": "..."}` 또는 그 목록. 아래 참고 |
| `sessions` | - | 로그인 상태가 다른 세션을 이름 붙여 더 둔다. 아래 "세션 여러 개" 참고 |
| `session_label` | - | 기본 세션(위 `auth` 등)의 리포트 표시 이름. 예: `회원` |
| `qa_prefix` | O | 테스트 생성 데이터에 강제로 붙는 식별자. 예: `[QA]20260911` |
| `sources` | O | 테스트 케이스 근거가 된 기획 자료 경로 목록 |
| `viewport` | - | `{"width":1600,"height":900}` (기본값) |
| `residue_check` | - | 테스트 데이터 잔여 점검 스텝 목록 (또는 `{"session": "<이름>", "steps": [...], "anchor": "..."}`). 아래 참고 |
| `allow_post` | - | 조회를 POST 로 보내는 **경로 전체** 목록. 예: `["/admin/goods/list.do", "re:/api/goods/search(/\\d+)?"]`. POST 에만 적용, 쿼리는 보지 않음, `re:` 는 경로 전체와 맞는 정규식. `/` 단독 · 4글자 미만 · 쿼리 · 거의 모든 경로와 맞는 정규식은 실행 전에 멈춘다. 저장 · 삭제 주소는 넣지 않는다 |
| `ignore_requests` | - | 로그 · 통계처럼 화면과 무관한 요청의 경로 목록 (형식은 `allow_post` 와 같음, 모든 메서드). 막기는 하되 판정에 쓰지 않는다. 분석 도구(GA · Clarity 등)는 러너가 이미 이렇게 처리 |
| `blocklist_off` | - | 승인 쓰기 케이스에 러너가 항상 더하는 내장 blocklist(`text*=발송` · `text*=결제` · `text*=발주` · `text*=일괄삭제` · `text*=일괄 삭제`) 중 풀 항목. 정말 눌러야 하는 스펙만 |
| `runner_min` | - | 이 스펙이 필요로 하는 러너 버전(정수, 현재 5). 설치된 러너가 낮으면 "플러그인 업데이트 필요" 로 멈춘다 |
| `keep_runs` | - | `output/` 에 남길 최근 실행 수. 기본 10 |

### residue_check · 테스트 데이터가 남았는지

cleanup 이 예외 없이 끝나도 실제로 지워졌다는 보장은 없다. 승인된 쓰기 케이스가 하나라도 실행됐으면
마지막에 이 스텝을 돌려 확인한다. 실패하면 리포트에 "테스트 데이터가 남아 있을 수 있음" 경고가 뜨고,
`QA-잔여` 케이스로 기획팀 검토 탭에만 실린다 (개발 전달 아님).

```json
"residue_check": {
  "session": "",
  "steps": [
    {"action": "goto", "url": "/totalAdmin/popup_list.php"},
    {"action": "fill", "selector": "#search_kw", "value": "{QA_PREFIX}", "write": false},
    {"action": "click", "selector": "#btn_search", "write": false},
    {"action": "expect_count", "selector": "table.list tbody tr.data", "count": 0}
  ]
}
```

**읽기 스텝만** 허용한다. 검색어 입력·검색 버튼처럼 서버에 쓰지 않는 조작은 `"write": false` 를 명시한다.
쓰기로 판정되는 스텝이 있으면 실행하지 않고 `BLOCKED` 로 남긴다.

### 로그인

**기본: 로그인 창**

```
py <skill>/qa_auto/qa_run.py --login https://www.example.com/ https://admin.example.com/ --ws qa_auto
```

창이 열리면 로그인만 한다. 로그인이 감지되면 다음 주소로 넘어가고, 마지막 주소까지 끝나면 저장 후
창이 저절로 닫힌다. 세션은 `qa_auto/auth.json`, 로그인 당시 UA 는 `qa_auto/auth.meta.json` 에
저장된다. 쿠키뿐 아니라 localStorage 까지 담기고, 만료되면 이 명령만 다시 돌리면 된다 (살아 있는
사이트는 바로 통과). 스펙에는 `"auth": "auth.json"` 한 줄만 넣는다.

화면 자동 판정이 안 맞는 사이트면 `--done-text 로그아웃` 처럼 로그인했을 때만 보이는 문구를 준다.

**예외: 개발자도구 표 붙여넣기**

로그인 창을 띄울 수 없는 상황(원격 세션 등)이면 크롬 `F12 > Application > Cookies` 에서
표 전체를 선택해 복사한 뒤, `qa_auto/cookies.txt` 에 **그대로 붙여넣는다.** 탭 구분 텍스트를
그대로 읽으므로 JSON 으로 고칠 필요가 없다.

```
Name	Value	Domain	Path	Expires	Size	...
PHPSESSID	abc123...	admin.example.com	/	Session	40	...
```

스펙에는 `"cookies": "cookies.txt"` 를 넣는다. 예전 방식인
`{"domain": "...", "cookies": [{"name":..., "value":...}]}` JSON 도 그대로 읽는다.

**세션 만료 감지**

`login_check` 를 넣어두면 첫 페이지에서 로그인이 풀린 걸 감지해 즉시 중단한다. 없으면 모든
케이스가 엉뚱한 이유로 줄줄이 실패해서 원인을 찾는 데 시간이 걸린다.

```json
"login_check": { "body_contains": "인증이 필요한 관리자 페이지" }
```

한 세션으로 프론트와 어드민을 오가면 조건을 목록으로 주고, `when_url` 로 어느 화면에서 볼 조건인지
나눈다. `when_url` 이 현재 URL 에 들어 있을 때만 그 조건을 본다. 한쪽의 비로그인 문구가 다른 쪽
화면에도 있으면(예: 프론트 비로그인 문구 "회원가입" 이 어드민 메뉴 "회원가입 이벤트페이지" 에도
있음) 나누지 않으면 오탐으로 실행이 멈춘다.

```json
"login_check": [
  { "when_url": "www.example.com", "body_contains": "회원가입" },
  { "when_url": "admin.example.com", "body_contains": "인증이 필요한 관리자 페이지" }
]
```

### 세션 여러 개 (비회원 · 회원 · 어드민을 한 스펙에)

로그인 상태가 다른 화면을 같이 검수할 때 스펙을 나누지 않는다. 리포트도 웍스 로그도 스펙마다
따로 나와서, 개발팀에 넘길 때 같은 결함이 두 번 실리거나 파일을 여러 개 봐야 한다.

`meta.sessions` 에 이름 붙인 세션을 두고, 케이스에 `"session": "<이름>"` 을 준다. `session` 을
안 준 케이스는 지금처럼 meta 의 `auth`·`cookies`·`user_agent`·`login_check` (기본 세션) 로 돈다.

```json
"meta": {
  "auth": "auth.json",
  "session_label": "회원",
  "login_check": { "when_url": "www.example.com", "body_contains": "회원가입" },
  "sessions": {
    "guest": { "label": "비회원" }
  }
}
```

- 세션 하나에 쓸 수 있는 키: `auth`, `cookies`, `user_agent`, `login_check`, `viewport`, `label`. 다 비우면(`{}`) 비로그인.
  `viewport` 를 주면 그 세션만 화면 크기가 달라진다 (예: 좁은 화면에서 숨는 요소 검수용 `"narrow": {"auth": "auth.json", "viewport": {"width":1600,"height":900}}`).
- 이름 붙인 세션은 meta 의 기본값을 물려받지 않는다. `login_check` 도 그 세션 것만 본다.
- 러너는 세션마다 브라우저 컨텍스트를 따로 연다 (쿠키가 섞이지 않는다). 같은 세션의 케이스끼리는
  페이지를 이어 쓰므로, 케이스는 지금처럼 `goto` 로 시작한다.
- 리포트 케이스 머리에 `label` 이 붙고, `개발전달.md` 에 "로그인 상태" 줄이 들어간다.
- 프론트 회원 세션과 어드민 세션은 `--login <프론트> <어드민>` 으로 받은 `auth.json` 하나에 같이
  담기므로, 보통은 기본 세션 하나(회원 + 어드민)와 `guest` 하나면 된다.

### blocklist

패턴 목록. **승인 여부와 무관하게 무조건 차단**되며, 걸리면 케이스는 `BLOCKED`.
운영 서버에서 절대 눌리면 안 되는 버튼을 여기 넣는다 (발송·결제·발주확정·권한변경 등).

| 패턴 | 막는 것 |
|---|---|
| `text=발송` | 버튼 글자가 **정확히** "발송" 인 것. "발송 내역" 탭은 통과 |
| `text*=발송` | 글자에 "발송" 이 **들어 있는** 것. "알림톡 발송" · "SMS 발송하기" · "발송 내역" 모두 |
| 그 밖 (`sendSms`, `/api/withdraw`) | 스텝의 셀렉터 · 주소 · 입력값 · js 에 그 문자열이 있는 것, 그리고 **실행 중 나가는 요청 주소**에 그 문자열이 있는 것 (대소문자 무시, 메서드 · 승인 무관) |

스펙의 셀렉터 · `label` 로 실행 전에 한 번, 누르기 직전 버튼의 화면 글자(innerText · value · aria-label ·
title)로 한 번 더 본다. `#approve` 처럼 셀렉터에 글자가 없어도 화면 글자로 걸린다. 글자를 읽은 요소를
그대로 누르므로 그 사이 버튼 글자가 바뀌어도 빠져나가지 못한다.

GET 으로 보내는 쓰기(`delete.php?id=` 링크, 이미지로 보내는 토글)는 네트워크 안전망이 막지 못한다.
그런 주소를 찾으면 주소 패턴을 여기 적는다.

승인된 쓰기 케이스에는 러너가 `text*=발송` · `text*=결제` · `text*=발주` · `text*=일괄삭제` ·
`text*=일괄 삭제` 를 항상 더한다. 풀려면 `meta.blocklist_off` 에 그 항목을 그대로 적는다.

---

## case

| 키 | 필수 | 설명 |
|---|---|---|
| `id` | O | `TC-001` 형식 |
| `title` | O | 한 줄 요약 |
| `requirement` | O | 검증하려는 기획 요건 원문 (근거) |
| `source` | O | 근거 위치. 예: `기획_내용.md > 배너(슬라이드)` |
| `kind` | - | 이 케이스가 실패했을 때 **누구 일인가**. 기본 `defect`. 아래 참고 |
| `area` | - | 웍스 로그의 묶음 제목. 예: `공통`, `품절관리`, `반품/폐기` |
| `fix` | - | **오류 한 줄.** 웍스 로그에 그대로 실린다. 아래 참고 |
| `req` | - | 이 케이스가 검증하는 요건 ID 목록. 예: `["REQ-01"]` |
| `severity` | - | `치명` · `주요` · `경미`. 같은 영역 안 전달 순서, `치명` 은 로그 줄 앞에 `[치명]` |
| `writes` | O | 이 케이스가 서버에 쓰기를 하는가 (true/false). 쓰기 스텝이 있는데 false 면 `BLOCKED` |
| `approved` | O | **사람이 쓰기를 허용했는가.** `writes:true`인데 `approved:false`면 실행 안 하고 SKIP |
| `anchor` | - | "이 영역이 다 그려졌다" 를 보여 주는 요소 셀렉터. 부재 판정 · precondition · 요소 없음 판정에 쓴다 (스텝 공통 플래그 참고) |
| `chains` | - | 독립 동작을 이어 붙인 케이스인가. 실패 시 단독 재현으로 원인을 가린다 (아래 참고) |
| `session` | - | 이 케이스를 돌릴 세션 이름 (`meta.sessions` 의 키). 없으면 기본 세션 |
| `steps` | O | 실행 스텝 배열 |
| `cleanup` | - | 생성한 데이터 정리 스텝. `writes:true`면 필수 |

> `writes:false` 케이스의 `approved` 값은 무시된다 (조회·검수는 항상 실행).

### kind · 실패를 누구에게 넘길 것인가

기획팀이 리포트를 먼저 보고, 그중 개발팀이 손댈 것만 추려서 넘긴다. `kind` 가 그 선을 긋는다.

| 값 | 의미 | 실패하면 |
|---|---|---|
| `defect` (기본) | 확정 기획서에 근거가 있는 검증 | **개발 전달용 탭·`개발전달.md` 에 실림** |
| `improve` | 기획서 근거 없는 사용성 제안 | 기획팀 검토 탭에만. 개발팀에 안 감 |
| `gap` | 기획서에 정의가 없어 판단 보류 | 기획팀 검토 탭에만. 개발팀에 안 감 |

`source` 에 확정 자료 위치를 적을 수 있으면 `defect` 다. "사용성 검수 #5" 처럼 근거가
사람의 관찰이면 `improve`, "엑셀 양식 통일(무엇을 통일하는지 정의 없음)" 처럼 기획서
자체가 비어 있으면 `gap` 이다.

개선 제안이 **여러 건**이면 `kind` 보다 **스펙 파일 분리**가 낫다 (SKILL.md 참고). 아직
구현 전이라 전부 실패로 나오므로, 회귀 스펙과 섞으면 "항상 빨간 리포트"가 된다. `kind` 는
회귀 스펙 안에 한두 건 섞여 들어올 때 쓴다.

### area · fix · 웍스에 올릴 한 줄

검수 결과는 기획팀이 웍스에 글로 올려 개발팀에 넘긴다. 그 글은 **할 일 목록**이라
설명이 붙으면 "추가 검토 요청"으로 읽혀 일이 안 돌아간다. 그래서 로그에는 지시문만 싣는다.

```
[09/22] 재고 대시보드 검수 · 기획 미반영 오류 3건

- 공통
1. [치명] 임박재고 칩 건수 오류: 긴급재고 제외하고 집계 중 (SB: 임박 + 긴급 합산) · TC-004
2. 담당자 컬럼 오류: 컬럼 노출 중 (SB: 담당자 컬럼 삭제) · TC-011

- 품절관리
3. 품절예상일 표기 오류: "N일" 로 노출 중 (SB: "N일 후") · TC-017
```

- `area` 가 묶음 제목이 된다. 화면의 구획 이름을 쓴다. 안 적은 항목은 맨 뒤 "- 기타" 아래로 간다.
- `fix` 가 없으면 테스트 제목 대신 `{area} 오류: {실제 현상} (SB: {source})` 로 만들고, 리포트 할 일에
  "fix 없음" 이 뜬다.
- 실행이 중간에 멈춘 결과는 로그 첫 줄이 "검수 중단 · 전달 보류 (N건 중 M건 실행)" 이고 항목을 싣지 않는다.
- 줄 끝 케이스 ID 는 러너가 붙인다. 회차마다 번호가 다시 매겨지므로 재검수 회신은 ID 로 받는다.
- 현재 현상 자리에 `{actual}` 을 쓰면 이번 실행의 실제 값(실패 메시지의 "실제:" 부분)으로 바뀐다.
  예: `품절예상일 표기 오류: "{actual}" 로 노출 중 (SB: "N일 후")`
- `fix` 가 각 줄이다. 형식은 **`대상 오류: 현재 현상 (SB: 기준)`** 이다.
  `~변경`·`~추가`·`~삭제` 로 쓰면 기획 변경(신규 요청)으로 읽혀 결함으로 처리되지 않는다.
  `~해주세요`, `~하면 좋겠습니다` 로 쓰면 제안으로 읽힌다. "변경"은 진짜 기획 변경일 때만 쓴다.
- 근거가 SB 가 아니면 그 자료 이름을 쓴다. 예: `(기획서: 1,000 단위 쉼표)`.
- `fix` 를 비우면 `title` 이 대신 쓰이는데, 제목은 보통 `~되지 않는다` 처럼 기대를 서술한
  문장이라 오류 문장으로는 약하다. **케이스를 만들 때 같이 적는다.**

번호는 `area` 묶음 순서를 따르고, 같은 실행 안에서는 리포트의 `항목별 근거` 와 `개발전달.md` 가
**같은 번호**를 쓴다. 실행이 바뀌면 번호도 바뀌므로 회차를 넘나드는 대화는 케이스 ID 로 한다.

### chains — 연속 사용에서만 나는 버그 가리기

사용자는 중간에 새로고침하지 않는다. 필터·탭·정렬처럼 **이어서 조작하는 요소**는 한 케이스 안에서
연속으로 밟아야 "이전 상태가 안 지워진다" 같은 버그가 드러난다. 그런 케이스에 `chains: true` 를 준다.

실패하면 러너가 **실패한 스텝을 직전 동작 하나만 앞세워 새 페이지에서 다시** 밟아보고, 결과를
사유에 덧붙인다.

```
[단독 재현: 통과 → 이어서 조작할 때만 발생]   ← 전환 버그. 재현 조건을 그대로 전달하면 된다
[단독 재현: 실패 → 연속 사용과 무관한 결함]   ← 기능 자체 문제
```

**한 동작을 쪼갠 케이스에는 쓰지 않는다.** 메모 편집(더블클릭 → 입력 → Enter → 확인)처럼
앞 스텝이 *선행 상태*가 아니라 *그 동작의 일부*라면, 단독으로 떼어낼 대상이 없다. 이미 최소
단위이므로 플래그를 달지 않는다 (달아도 선행 동작을 못 찾으면 아무것도 하지 않는다).

---

## 날짜 표기 (실행일 기준)

스텝의 `selector`·`value`·`text`·`url`·`js`·`contains`·`not_contains`·`equals`·`matches`·`anchor` 에 `{TODAY}` 를 쓰면 실행일
(YYYY-MM-DD)로 바뀐다. `{TODAY+30}`, `{TODAY-1}` 처럼 날짜 수를 더하고 뺄 수 있다.

```json
{"action": "fill", "selector": ".date_input", "value": "{TODAY}"},
{"action": "click", "selector": ".cal_day[data-date='{TODAY+1}']"}
```

날짜 입력 · 달력 · D-day 화면은 고정 날짜를 박으면 시간이 지나 과거 날짜가 되거나, 달력이 다른 달로
열렸을 때 "오늘" 칸을 집을 수 없다. 다만 저장된 값과 비교해야 하는 케이스(예: "기존 값과 같으면
저장 비활성")는 고정 날짜가 맞다. 앞 실행에서 저장한 값이 실행일에 따라 달라지면 비교가 깨진다.

같은 필드에서 `{QA_PREFIX}` 는 `meta.qa_prefix` 값으로 바뀐다. 잔여 데이터 점검에서 검색어로 쓴다.
`{RUN_ID}` 는 실행 번호(`20261124_143005`)로 바뀐다. 지난 실행이 남긴 데이터와 이번 데이터를 가를 때 쓴다.
`qa_prefix` 안에 `{TODAY}` · `{RUN_ID}` 를 넣어도 펼쳐진다.

---

## 스텝 공통 플래그

| 키 | 적용 | 동작 |
|---|---|---|
| `precondition` | 읽기 스텝 | 이 스텝의 판정이 어긋나고 `anchor` 가 보이면 케이스를 FAIL 이 아니라 **NA(조건 미충족)** 로 끝낸다. anchor 가 없거나 안 보이면 ERROR. 운영 데이터에 기대는 전제 확인용 |
| `anchor` | 판정 스텝 | 케이스의 `anchor` 를 이 스텝만 바꾼다 |
| `soft` | `expect_*` | 판정이 틀려도 멈추지 않고 다음 스텝을 계속한다. 끝나고 실패를 모아 한 번에 FAIL |
| `write` | 모든 스텝 | 쓰기 판정 강제 (아래 "쓰기 판정 규칙") |
| `timeout` | 대기하는 스텝 | 요소가 붙을 때까지 기다리는 밀리초. 기본 10000 (`expect_visible` 은 7000) |
| `settle` | `expect_text` · `expect_count` · `expect_attr` · `expect_hidden` | 요소가 붙은 뒤 기대값이 될 때까지 더 기다리는 밀리초. 기본 5000 ("0건" 뒤에 "12건" 이 그려지는 화면) |
| `hold` | 부재 판정 (`expect_hidden`, `count: 0` · `max`, `not_contains`) | 기대값이 된 뒤 그대로 유지돼야 하는 관찰 구간 밀리초. 기본 1500 |

```json
{"action": "expect_count", "selector": "tr[data-code='25692']", "count": 1, "precondition": true},
{"action": "expect_text", "selector": ".notice", "contains": "배송 안내", "soft": true},
{"action": "expect_text", "selector": ".price", "contains": "원", "soft": true}
```

`precondition` 에 쓰기 스텝을 주면 실행을 거부한다. `soft` 는 앞 스텝이 뒤 스텝의 전제인 흐름에는
쓰지 않는다. 앞이 틀린 상태로 뒤를 보면 실패가 줄줄이 따라 나온다.

### anchor · "없어야 함" 과 "요소 없음" 을 가르는 기준

`expect_hidden`, `expect_count` 의 `count: 0` · `max`, `expect_text` 의 `not_contains` 는 셀렉터에 오타가
있어도 0개라 통과해 버린다. 늦게 그려지는 컬럼 · 문구를 그리기 전에 봐도 통과한다. 그래서 이 판정들은
`anchor` 가 보일 때까지 기다린 뒤, 기대값이 될 때까지 기다리고(`settle`) **관찰 구간(`hold`) 내내 유지될 때만**
통과로 본다. 관찰이 끝났을 때 anchor 가 사라졌으면 판정 불가(ERROR)다. 목록을 다시 그리는 순간 잠깐 0개가
된 것을 통과로 보지 않고, 필터 뒤 늦게 빠지는 행을 실패로 보지 않는다.

| 상황 | anchor 없음 | anchor 보임 | anchor 안 보임 |
|---|---|---|---|
| 부재 판정, 요소 0개 | ERROR (판정 불가) | PASS | ERROR |
| 부재 판정, 요소 있고 보임 | FAIL | FAIL | ERROR |
| `expect_visible` 등, 요소 0개 | ERROR | **FAIL** (그 요소만 없음 = 미노출 결함) | ERROR |
| `precondition` 어긋남 | ERROR | NA | ERROR |

```json
{"id": "TC-020", "anchor": "table.goods thead",
 "steps": [{"action": "goto", "url": "/admin/goods"},
           {"action": "expect_count", "selector": "th.col-owner", "count": 0}]}
```

## 케이스 결과 상태

| 상태 | 언제 | 개발 전달 |
|---|---|---|
| `PASS` | 모든 판정 통과 | - |
| `FAIL` | `expect_*` 판정이 기대와 다름 | `kind: defect` 면 O |
| `ERROR` | 요소를 못 찾음 · 시간 초과 · 서버 요청 차단 · 확인창 취소 · 막은 뒤 판정 어긋남 · 앞 스텝 건너뜀 · 설정 오류 · cleanup 실패 · 세션 만료로 중단 | X (점검 필요) |
| `FLAKY` | 1차 FAIL, 재실행 PASS | X (불안정, 기획팀 판단) |
| `NA` | `precondition` 불충족 (anchor 보임) | X |
| `SKIPPED` | 쓰기 미승인 · dry-run | X |
| `BLOCKED` | blocklist · cleanup 누락 · writes 선언 누락 · 버튼 글자가 쓰기 | X |

요소 자체가 없으면 `ERROR` 다 (셀렉터가 낡았을 수 있다). 요소가 없는 것 자체가 결함이면 케이스에
`anchor` 를 준다. anchor 가 보이는데 요소가 없으면 `FAIL` 이 된다.

---

## step actions

### 읽기 (쓰기 아님 — 항상 실행)

| action | 필드 | 동작 |
|---|---|---|
| `goto` | `url` | 페이지 이동 (상대경로면 base_url 결합) |
| `wait` | `ms` 또는 `selector` | 대기 |
| `screenshot` | `name`, `selector`, `context`, `include_header`, `padding` | 스크린샷. `selector` 를 주면 그 지점만 잘라 찍는다 (아래 참고) |
| `download` | `selector`, `save_as`, `timeout` | 클릭해서 파일을 받아 `output/<실행폴더>/downloads/` 에 저장 |
| `expect_file` | `file`, `headers`, `min_rows`, `row_count_js` | 받아둔 파일의 헤더·행수를 검증 (xlsx·csv·HTML표) |
| `hover` | `selector`, `ms` | 요소 위로 마우스 이동 (툴팁 검증용). 셀 안 인라인 span에서 `hover()`가 오판하므로 좌표 이동으로 처리 |
| `expect_visible` | `selector` | 요소가 보일 때까지 기다린다 |
| `expect_hidden` | `selector`, `anchor` | 요소가 숨겨졌는가 (anchor 필요, 위 참고) |
| `expect_text` | `selector`, `equals` / `contains` / `matches` / `not_contains`, `all` | **화면에 보이는 글자**(숨은 글자 제외, 공백 정규화)로 판정. `equals` 완전일치(알럿 · 안내 문구 기본), `contains` 부분일치, `matches` 정규식, `not_contains` 없어야 함. `all: true` 면 매칭되는 모든 요소(표의 모든 행)에 적용 |
| `expect_count` | `selector`, `count` / `min` / `max` | 요소 개수. 기대값이 될 때까지 기다린다. `count: 0` · `max` 는 anchor 필요 |
| `expect_attr` | `selector`, `attr`, `equals`/`contains` | 속성값 |
| `expect_dialog` | `equals` / `contains` / `matches`, `timeout` | 직전 조작이 띄운 알럿 · 확인창 문구. `equals` 완전일치(토씨까지), `contains` 부분일치, `matches` 정규식. 늦게 뜨는 알럿을 기다린다 (기본 5000ms) |
| `expect_no_dialog` | `ms` | 직전 조작 뒤 `ms`(기본 2000) 동안 알럿이 안 떠야 함 |
| `expect_overflow` | `selector`, `ellipsis`(기본 true) | true 면 넘친 글자가 말줄임(…)으로 잘려야 함, false 면 칸을 넘치지 않아야 함 |
| `expect_box` | `selector`, `width`, `height`, `below`·`above`·`left_of`·`right_of`·`inside`, `gap`, `align`, `align_to`, `tol` | 크기와 기준 요소와의 상대 위치. 아래 참고 |
| `expect_js` | `js`, `equals`(기본 true), `desc` | JS 평가 결과 비교. CSS로 표현 못 하는 판정(노출 순서·계산값)에만 쓴다 |

`{ok, detail}` 을 돌려주면 `ok` 로 판정하고 `detail` 을 실패 사유에 그대로 싣는다.
기대값이 데이터에 따라 변해 `equals` 에 못 박는 경우(건수 비교 등)에 쓴다 — 불리언만 쓰면
`기대:True / 실제:False` 로 남아 개발팀에 그대로 못 보낸다.

```json
{"action": "expect_js", "desc": "임박재고 칩 건수 = 임박+긴급 행 수",
 "js": "()=>{const a=..., b=...; return {ok: a===b, detail: `칩 ${a}건 / 행 ${b}건`};}"}
```

> `expect_js`는 **읽기 전용만 허용**한다. `.click(`, `.value =`, `fetch(`, `localStorage`, `dispatchEvent` 등
> 변경성 패턴이 들어 있으면 러너가 실행을 거부한다. 패턴은 우회가 쉬워서, 평가하는 동안(평가 뒤 0.2초까지)은
> 승인된 쓰기 케이스여도 서버로 가는 쓰기 요청을 전부 막는다. 막히면 그 케이스는 ERROR. 그보다 늦게 나가는
> `setTimeout` 요청은 막지 못하므로 타이머를 쓰지 않는다.

### expect_box · 크기와 위치

px 고정값, "배너 아래", "검색창 오른쪽에 붙어서" 같은 요건을 판정한다. 위치는 절대 좌표가 아니라
**기준 요소와의 관계**로 본다. 화면 너비가 바뀌어도 판정이 흔들리지 않는다.

```json
{"action": "expect_box", "selector": ".popup_banner", "width": 500, "height": 500},
{"action": "expect_box", "selector": ".chat_btn", "below": ".ad_banner",
 "gap": {"min": 0, "max": 16}, "align": "left"},
{"action": "expect_box", "selector": ".badge", "inside": ".card", "width": {"max": 80}}
```

| 키 | 뜻 |
|---|---|
| `width` · `height` | 숫자면 `±tol`, `{"min":..,"max":..}` 면 범위 |
| `below` · `above` · `left_of` · `right_of` | 기준 요소 셀렉터. 그 방향에 있어야 함 (겹치면 실패) |
| `inside` | 기준 요소 안에 들어 있어야 함 |
| `gap` | 위 방향 관계에서 기준 요소와의 간격 (숫자 또는 범위) |
| `align` | `left`·`right`·`top`·`bottom`·`center_x`·`center_y` 중 하나 또는 목록. 기준 요소와 그 변이 맞아야 함 |
| `align_to` | 정렬 기준 요소. 없으면 방향 관계의 기준 요소 |
| `tol` | 허용 오차 px. 기본 1 |

실패하면 `크기·위치 불일치 - .chat_btn: .ad_banner 과의 간격 40px (기대 0~16px)` 처럼 실제 값이 사유에 남는다.

### 결함 증빙 스크린샷

전체 화면만 찍으면 수백 행짜리 표에서 **문제 지점을 찾을 수가 없다.** `selector` 로 범위를 좁히고,
주변 정상 행을 같이 담아 "이 행만 다르다"가 보이게 한다.

```json
{"action": "screenshot", "name": "반품정책_결함",
 "selector": ".row[data-code='25692']",
 "context": {"above": 2, "below": 2, "selector": ".row"},
 "include_header": ".table__head", "padding": 10}
```

`clip` 으로 잘라내므로 마우스를 움직이지 않는다 — **직전 `hover` 로 띄운 툴팁이 살아 있다.**
`selector` 가 없으면 기존처럼 전체 화면을 찍는다.

### 쓰기 (승인 필요)

| action | 필드 | 동작 |
|---|---|---|
| `fill` | `selector`, `value` | 입력 (기존 값을 지우고 한 번에 넣음) |
| `type` | `selector`, `text`, `clear`, `delay` | 한 글자씩 입력. 글자수 제한처럼 **입력 중** 동작하는 검증에 쓴다 |
| `select` | `selector`, `value` | 드롭다운 선택 |
| `check` / `uncheck` | `selector` | 체크박스·라디오 |
| `upload` | `selector`, `file` | 파일 업로드 (`fixtures/` 기준 상대경로) |
| `click` | `selector` | 클릭 — 아래 규칙으로 쓰기 여부 자동 판정 |
| `dblclick` | `selector` | 더블클릭. 인라인 편집 진입용 — `fill` 은 더블클릭 **이후에** 생기는 input 을 못 잡는다 |
| `press` | `key`, `selector`(선택) | 키 입력. `Enter` 는 쓰기, `Escape`·`Tab`·방향키는 읽기로 본다 |

---

## 쓰기 판정 규칙 (러너가 강제)

1. action이 `fill`/`type`/`select`/`check`/`uncheck`/`upload` → **무조건 쓰기**
2. action이 `click`/`dblclick`인데 셀렉터·`label` 에 쓰기 힌트(`저장·확인·등록·추가·삭제·수정·전송·발송·적용·완료·승인·반려·처리·결제·주문·신청·환불·출고·변경·해제·업로드·초기화·일괄`)가 포함 → **쓰기로 자동 승격**
3. 클릭 · 다운로드는 누르기 직전 **버튼의 화면 글자**로 2번을 한 번 더 본다. 셀렉터가 `#approve` 여도 글자가 "승인" 이면 쓰기
4. action이 `press` 인데 `key` 가 `Escape`·`Tab`·방향키가 아니면 → 쓰기
5. 스텝에 `"write": true`가 명시 → 쓰기
6. 스텝에 `"write": false`가 명시 → 자동 승격을 해제 (검색어 입력, "확인" 으로 모달 닫기, "발송 내역" 탭)

판정 결과:

- 쓰기 스텝이 있는데 케이스에 `writes: true` 가 없으면 → **BLOCKED** ("writes 선언 누락"). 3번(화면 글자)으로 걸리면 그 자리에서 BLOCKED
- `writes: true` 인데 `approved: false` 거나 `--dry-run` 이면 → 케이스 **SKIPPED**, 리포트에 "승인 대기"
- `writes: true` · `approved: true` 인데 `cleanup` 이 없거나, 첫 스텝이 `goto` 가 아니면 → **BLOCKED**

판정과 별개로, 승인된 쓰기 케이스 밖에서는 서버로 가는 쓰기 요청(POST · PUT · DELETE 등, GET · HEAD ·
OPTIONS 외)이 **네트워크 단에서 막힌다.** `write: false` 를 잘못 붙였거나 위 판정을 빠져나간 버튼이어도 이런
요청은 서버로 가지 않고, 그 케이스는 ERROR("서버 요청을 막음") 가 된다. 막은 뒤 화면 판정이 어긋나도 FAIL 이
아니라 ERROR("판정 불가 - 화면 흐름이 달라짐") 다. 다른 사이트로 가는 요청도 같다.
조회를 POST 로 하는 화면은 `meta.allow_post`, 로그성 요청은 `meta.ignore_requests` 에 경로를 적는다.

승인된 쓰기 케이스도 **첫 `goto` 가 끝난 뒤부터 cleanup 까지**, 지금 페이지에서 나간 요청만 통과한다.
팝업 · 앞 케이스가 남긴 창 · 다른 세션 탭에서 나간 요청은 항상 막는다.

**GET 으로 보내는 쓰기와 웹소켓은 막지 못한다.** blocklist 에 적은 주소 패턴만 막히고, 나머지는 클릭 직후
나간 같은 사이트 GET 중 쓰기 동사가 든 주소를 리포트에 "GET 쓰기 의심" 으로 남긴다.

"삭제하시겠습니까?" 같은 확인창은 승인된 쓰기 스텝부터 다음 조작 스텝 전까지만 수락하고(늦게 뜨는 확인창 대비),
나머지는 취소한다. 확인창 문구만 확인하려면 바로 다음 스텝에 `expect_dialog` 를 둔다 (그러면 취소가 의도한
것으로 처리된다).

---

## 테스트 데이터 규칙

쓰기 케이스에서 만드는 텍스트 데이터는 `meta.qa_prefix` 값을 반드시 포함시킨다.
러너는 `fill` 값에 prefix가 없으면서 길이 2자 이상인 텍스트를 감지하면 **경고**를 리포트에 남긴다 (차단은 안 함 — 검색어 입력 등 정당한 경우가 있으므로).

---

## 예시

```json
{
  "meta": {
    "project": "샘플_화면_개편",
    "base_url": "https://admin.example.com",
    "auth": "auth.json",
    "login_check": { "body_contains": "인증이 필요한 관리자 페이지" },
    "qa_prefix": "[QA]20260911",
    "runner_min": 5,
    "sources": ["projects/샘플_화면_개편/기획_내용.md"]
  },
  "blocklist": ["text=발송", "text=결제", "text=일괄삭제"],
  "cases": [
    {
      "id": "TC-001",
      "title": "조건을 만족하는 행에만 상세관리 버튼 노출",
      "requirement": "노출 여부가 Y 인 행에만 상세관리 버튼 추가",
      "source": "기획_내용.md > 리스트",
      "writes": false,
      "approved": false,
      "steps": [
        {"action": "goto", "url": "/totalAdmin/_sample_list.php?menuUid=000"},
        {"action": "expect_visible", "selector": "table.list"},
        {"action": "screenshot", "name": "목록"}
      ]
    }
  ]
}
```
