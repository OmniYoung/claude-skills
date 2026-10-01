# spec.json 포맷 정의

`/qa-test` 2단계(셀렉터 매핑)의 산출물이자, `qa_run.py`가 실행하는 입력 파일.
**이 문서는 Claude와 러너 사이의 계약이다. 여기 없는 action은 러너가 거부한다.**

---

## 최상위 구조

```json
{
  "meta": { ... },
  "blocklist": [ ... ],
  "cases": [ ... ]
}
```

### meta

| 키 | 필수 | 설명 |
|---|---|---|
| `project` | O | 프로젝트명 (출력 폴더명에 사용) |
| `base_url` | O | 대상 사이트 origin |
| `auth` | - | `--login` 으로 저장한 세션 파일 경로 (보통 `auth.json`). 옆의 `auth.meta.json` 에 든 로그인 당시 UA 도 자동 적용 |
| `cookies` | - | 쿠키 파일 경로. 로그인 창을 띄울 수 없는 상황에서 수동으로 넣을 때 |
| `user_agent` | - | UA 강제 지정. 붙여넣은 쿠키가 UA 에 묶인 사이트에서 안 먹을 때 쿠키를 복사한 브라우저의 UA 를 넣는다 |
| `login_check` | - | 세션 만료 감지 조건. `{"url_contains": "...", "body_contains": "..."}` |
| `qa_prefix` | O | 테스트 생성 데이터에 강제로 붙는 식별자. 예: `[QA]20260911` |
| `sources` | O | 테스트 케이스 근거가 된 기획 자료 경로 목록 |
| `viewport` | - | `{"width":1600,"height":900}` (기본값) |

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

### blocklist

셀렉터 또는 텍스트 패턴 목록. **승인 여부와 무관하게 무조건 차단**되며, 매칭되면 해당 케이스는 즉시 ABORT.
운영 서버에서 절대 눌리면 안 되는 버튼을 여기 넣는다 (발송·결제·발주확정·권한변경 등).

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
| `fix` | - | **수정 지시 한 줄.** 웍스 로그에 그대로 실린다. 아래 참고 |
| `writes` | O | 이 케이스가 서버에 쓰기를 하는가 (true/false) |
| `approved` | O | **사람이 쓰기를 허용했는가.** `writes:true`인데 `approved:false`면 실행 안 하고 SKIP |
| `chains` | - | 독립 동작을 이어 붙인 케이스인가. 실패 시 단독 재현으로 원인을 가린다 (아래 참고) |
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
[09/22] 재고 대시보드 검수 · 수정 요청 5건

- 공통
1. 긴급재고 별도 칩 삭제, 임박재고 필터·건수에 포함
2. 담당자 컬럼 삭제

- 품절관리
3. 품절예상일 N일 뒤에 " 후" 표기 (N일 후)
```

- `area` 가 묶음 제목이 된다. 화면의 구획 이름을 쓴다. 안 적으면 묶음 없이 쭉 나열된다.
- `fix` 가 각 줄이다. **명사형으로 끝낸다** (`추가`, `삭제`, `변경`, `통일`, `표기`).
  `~해주세요`, `~하면 좋겠습니다` 로 쓰면 요청이 아니라 제안으로 읽힌다.
- 바뀌는 값은 `현재 > 목표` 로 적는다. 예: `숫자형 , 추가 (1000 > 1,000)`.
- `fix` 를 비우면 `title` 이 대신 쓰이는데, 제목은 보통 `~되지 않는다` 처럼 기대를 서술한
  문장이라 지시문으로는 약하다. **케이스를 만들 때 같이 적는다.**

번호는 `area` 묶음 순서를 따르고, 리포트의 `항목별 근거` 와 `개발전달.md` 가 **같은 번호**를
쓴다. "3번 건" 이라고만 해도 서로 같은 걸 가리킨다.

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
| `expect_visible` | `selector` | 요소가 보이는가 |
| `expect_hidden` | `selector` | 요소가 숨겨졌는가 |
| `expect_text` | `selector`, `contains` / `not_contains` | 요소 텍스트에 문구가 포함되는지 / 포함되면 안 되는지 (둘 다 지정 가능) |
| `expect_count` | `selector`, `count` 또는 `min` | 요소 개수 |
| `expect_attr` | `selector`, `attr`, `equals`/`contains` | 속성값 |
| `expect_dialog` | `contains` | 직전 액션이 띄운 알럿 문구 검증 |
| `expect_no_dialog` | - | 직전 액션에서 알럿이 없어야 함 |
| `expect_js` | `js`, `equals`(기본 true), `desc` | JS 평가 결과 비교. CSS로 표현 못 하는 판정(노출 순서·계산값)에만 쓴다 |

`{ok, detail}` 을 돌려주면 `ok` 로 판정하고 `detail` 을 실패 사유에 그대로 싣는다.
기대값이 데이터에 따라 변해 `equals` 에 못 박는 경우(건수 비교 등)에 쓴다 — 불리언만 쓰면
`기대:True / 실제:False` 로 남아 개발팀에 그대로 못 보낸다.

```json
{"action": "expect_js", "desc": "임박재고 칩 건수 = 임박+긴급 행 수",
 "js": "()=>{const a=..., b=...; return {ok: a===b, detail: `칩 ${a}건 / 행 ${b}건`};}"}
```

> `expect_js`는 **읽기 전용만 허용**한다. `.click(`, `.value =`, `fetch(`, `localStorage`, `dispatchEvent` 등
> 변경성 패턴이 들어 있으면 러너가 실행을 거부한다 — 임의 JS로 쓰기 승인 게이트를 우회하지 못하게 하기 위함이다.

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
2. action이 `click`/`dblclick`인데 셀렉터·텍스트에 `저장·확인·등록·추가·삭제·수정·전송·발송·적용·완료`가 포함 → **쓰기로 자동 승격**
3. action이 `press` 인데 `key` 가 `Escape`·`Tab`·방향키가 아니면 → 쓰기
4. 스텝에 `"write": true`가 명시 → 쓰기
5. 스텝에 `"write": false`가 명시 → 자동 승격을 해제 (예: "확인" 텍스트가 들어간 단순 조회 버튼)

쓰기 스텝인데 케이스가 `approved:false`거나 `--dry-run`이면 → 해당 케이스 **SKIPPED**, 리포트에 "승인 대기"로 표기.

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
