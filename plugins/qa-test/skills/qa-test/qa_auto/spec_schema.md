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
| `auth` | - | `--login` 으로 저장한 세션 파일 경로 (보통 `auth.json`). **로그인이 필요하면 이걸 권장** |
| `cookies` | - | 쿠키 파일 경로. `--login` 을 못 쓰는 상황에서 수동으로 넣을 때 |
| `login_check` | - | 세션 만료 감지 조건. `{"url_contains": "...", "body_contains": "..."}` |
| `qa_prefix` | O | 테스트 생성 데이터에 강제로 붙는 식별자. 예: `[QA]20260911` |
| `sources` | O | 테스트 케이스 근거가 된 기획 자료 경로 목록 |
| `viewport` | - | `{"width":1600,"height":900}` (기본값) |

### 로그인

**권장 — 브라우저에서 직접 로그인**

```
py <skill>/qa_auto/qa_run.py --login https://admin.example.com/ --ws qa_auto
```

브라우저가 열리면 평소처럼 로그인하고 콘솔로 돌아와 Enter. 세션이 `qa_auto/auth.json` 에 저장된다.
쿠키뿐 아니라 localStorage 까지 담기므로 쿠키만 복사하는 것보다 확실하고, 만료되면 이 명령만 다시
돌리면 된다. 스펙에는 `"auth": "auth.json"` 한 줄만 넣는다.

**대안 — 개발자도구 표 붙여넣기**

`--login` 을 쓸 수 없는 상황(원격·자동화 환경)이면 크롬 `F12 > Application > Cookies` 에서
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
| `writes` | O | 이 케이스가 서버에 쓰기를 하는가 (true/false) |
| `approved` | O | **사람이 쓰기를 허용했는가.** `writes:true`인데 `approved:false`면 실행 안 하고 SKIP |
| `steps` | O | 실행 스텝 배열 |
| `cleanup` | - | 생성한 데이터 정리 스텝. `writes:true`면 필수 |

> `writes:false` 케이스의 `approved` 값은 무시된다 (조회·검수는 항상 실행).

---

## step actions

### 읽기 (쓰기 아님 — 항상 실행)

| action | 필드 | 동작 |
|---|---|---|
| `goto` | `url` | 페이지 이동 (상대경로면 base_url 결합) |
| `wait` | `ms` 또는 `selector` | 대기 |
| `screenshot` | `name` | 스크린샷 저장 (리포트에 첨부) |
| `hover` | `selector`, `ms` | 요소 위로 마우스 이동 (툴팁 검증용). 셀 안 인라인 span에서 `hover()`가 오판하므로 좌표 이동으로 처리 |
| `expect_visible` | `selector` | 요소가 보이는가 |
| `expect_hidden` | `selector` | 요소가 숨겨졌는가 |
| `expect_text` | `selector`, `contains` / `not_contains` | 요소 텍스트에 문구가 포함되는지 / 포함되면 안 되는지 (둘 다 지정 가능) |
| `expect_count` | `selector`, `count` 또는 `min` | 요소 개수 |
| `expect_attr` | `selector`, `attr`, `equals`/`contains` | 속성값 |
| `expect_dialog` | `contains` | 직전 액션이 띄운 알럿 문구 검증 |
| `expect_no_dialog` | - | 직전 액션에서 알럿이 없어야 함 |
| `expect_js` | `js`, `equals`(기본 true), `desc` | JS 평가 결과 비교. CSS로 표현 못 하는 판정(노출 순서·계산값)에만 쓴다 |

> `expect_js`는 **읽기 전용만 허용**한다. `.click(`, `.value =`, `fetch(`, `localStorage`, `dispatchEvent` 등
> 변경성 패턴이 들어 있으면 러너가 실행을 거부한다 — 임의 JS로 쓰기 승인 게이트를 우회하지 못하게 하기 위함이다.

### 쓰기 (승인 필요)

| action | 필드 | 동작 |
|---|---|---|
| `fill` | `selector`, `value` | 입력 |
| `select` | `selector`, `value` | 드롭다운 선택 |
| `check` / `uncheck` | `selector` | 체크박스·라디오 |
| `upload` | `selector`, `file` | 파일 업로드 (`fixtures/` 기준 상대경로) |
| `click` | `selector` | 클릭 — 아래 규칙으로 쓰기 여부 자동 판정 |

---

## 쓰기 판정 규칙 (러너가 강제)

1. action이 `fill`/`select`/`check`/`uncheck`/`upload` → **무조건 쓰기**
2. action이 `click`인데 셀렉터·텍스트에 `저장·확인·등록·추가·삭제·수정·전송·발송·적용·완료`가 포함 → **쓰기로 자동 승격**
3. 스텝에 `"write": true`가 명시 → 쓰기
4. 스텝에 `"write": false`가 명시 → 2번 자동 승격을 해제 (예: "확인" 텍스트가 들어간 단순 조회 버튼)

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
