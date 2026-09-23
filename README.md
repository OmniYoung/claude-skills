# claude-skills

기획 업무용 [Claude Code](https://claude.com/claude-code) 스킬 모음입니다.
화면 QA 자동화와 개발자용 화면설계 스토리보드(SB) 생성을 다룹니다.

## 설치

```bash
claude plugin marketplace add OmniYoung/claude-skills
claude plugin install qa-test@claude-skills
```

필요한 것만 골라 설치하면 됩니다.

| 플러그인 | 하는 일 |
|---|---|
| `qa-test` | 구현된 화면이 기획서대로 만들어졌는지 자동 검수하고 HTML 리포트 생성 |
| `sb-storyboard` | 확정된 **HTML 목업** → 개발자용 화면설계 스토리보드(PPTX) |
| `sb-storyboard-figma` | 확정된 **Figma 디자인** → 개발자용 화면설계 스토리보드(PPTX) |

## 사용법

설치하고 **Claude Code 를 새로 시작하면** 슬래시로 부를 수 있습니다.

```
/qa-test              https://운영주소/화면   projects/내기획/기획_내용.md
/sb-storyboard        projects/내기획/목업_v1.html
/sb-storyboard-figma  https://figma.com/design/...?node-id=1234-5678
```

슬래시 없이 그냥 말해도 걸립니다 — *"이 화면 기획서대로 됐는지 확인해줘"*,
*"이 목업으로 스토리보드 만들어줘"* 처럼요.

각 스킬이 필요한 걸 안 주면 먼저 물어보니, 잘 모르겠으면 `/qa-test` 만 치고 시작해도 됩니다.

---

## 업데이트

```bash
claude plugin marketplace update claude-skills
```

이 한 줄이면 끝입니다. **재설치는 필요 없습니다** — 마켓플레이스가 로컬에 git 으로 복제돼 있고
플러그인이 그 폴더를 직접 참조하므로, 갱신하면 설치된 플러그인도 같이 최신이 됩니다.

바뀐 내용은 이렇게 확인합니다.

```bash
claude plugin details qa-test          # 어느 마켓플레이스에서 왔는지
claude plugin marketplace list         # 등록된 마켓플레이스 목록
```

새 플러그인이 추가된 경우에만 `claude plugin install <이름>@claude-skills` 를 한 번 더 해주면 됩니다.

> 업데이트 후에는 Claude Code 세션을 새로 시작해야 바뀐 스킬이 반영됩니다.

---

## 문제가 생기면

**스킬이 안 불린다** — 세션을 새로 시작했는지 확인하세요. 그래도 안 되면
`claude plugin details <이름>` 으로 설치 상태를 봅니다.

**설치가 실패한다** — 마켓플레이스가 등록돼 있는지(`claude plugin marketplace list`) 먼저 보고,
없으면 `claude plugin marketplace add OmniYoung/claude-skills` 를 다시 실행합니다.

**제거하려면**

```bash
claude plugin uninstall qa-test
claude plugin marketplace remove claude-skills
```

---

## qa-test

기획서·Figma·스토리보드를 근거로 테스트 케이스를 뽑고, 실제 사이트에 셀렉터를 매핑해
Playwright 로 실행한 뒤 HTML 리포트를 만듭니다.

```
[1] 케이스 생성   Claude   기획 자료 → 테스트 케이스 MD   → 사람 검토
[2] 셀렉터 매핑   Claude   실제 사이트 탐색 → 실행 스펙 JSON
[3] 실행·리포트   Python   스펙 반복 실행 → HTML 리포트
```

화면이 바뀌지 않는 한 3단계만 반복하면 되므로, 회귀 검사는 Claude 없이 배치 파일로 돌릴 수 있습니다.

### 운영 서버 대상 안전장치

운영 화면을 검수하는 일이 많아 쓰기에 잠금이 걸려 있습니다. 러너가 강제하며 우회할 수 없습니다.

- **승인 게이트** — 쓰기 케이스는 사람이 `approved: true` 를 켜야만 실행됩니다
- **blocklist** — 발송·결제·발주 등은 승인 여부와 무관하게 차단됩니다
- **cleanup 강제** — 정리 절차가 없는 쓰기 케이스는 실행을 거부합니다
- **`expect_js` 읽기 전용** — 임의 JS 로 승인 게이트를 우회할 수 없습니다

### 준비물

```bash
pip install playwright
py -m playwright install chromium
```

### 실제 진행 흐름

**1) 작업 폴더 만들기** — 프로젝트마다 한 번만

Claude 에게 이렇게만 말하면 됩니다. 스킬 설치 경로는 알아서 찾습니다.

> "qa-test 작업 폴더 만들어줘"

`qa_auto/{specs, cases, fixtures, output}` 과 `run_qa.bat` 이 생깁니다.

**2) 케이스 만들기** — Claude 에게 맡깁니다

```
/qa-test https://운영주소/화면 projects/내기획/기획_내용.md
```

기획 자료를 읽고 테스트 케이스를 `qa_auto/cases/` 에 정리한 뒤 **검토를 요청하며 멈춥니다.**
빠진 요건이 없는지, 쓰기로 표시된 것 중 운영에서 돌려도 되는 게 뭔지 확인하세요.

**3) 셀렉터 매핑** — 검토가 끝나면 Claude 가 실제 화면을 열어보며 `qa_auto/specs/` 에 실행 스펙을
만듭니다. 이때 쓰기 액션은 실행하지 않습니다.

**4) 실행** — `qa_auto/run_qa.bat` 더블클릭

```
1. 읽기만 실행  (안전 - 쓰기 케이스는 건너뜀)
2. 전체 실행    (승인된 쓰기 케이스까지 실행)
```

리포트가 브라우저로 열립니다. 스크린샷은 클릭하면 확대되고 `원본 크기` 로 1:1 확인이 됩니다.

**화면이 바뀌지 않는 한 4번만 반복**하면 됩니다. Claude 없이도 돌아갑니다.

### 쓰기 케이스를 돌리려면

기본은 전부 미승인이라 `SKIPPED` 로 나옵니다. 검토 후 스펙에서 해당 케이스만 켭니다.

```json
"approved": true
```

정리(cleanup) 절차가 없으면 러너가 실행을 거부합니다 — 만든 데이터를 되돌릴 방법이 없으면
애초에 돌리지 않는다는 뜻입니다.

### 로그인이 필요한 화면

로그인이 필요한 화면이면 브라우저에서 직접 로그인해 세션을 저장합니다.

> "이 주소 로그인 세션 저장하게 명령어 만들어줘"

라고 하면 경로가 채워진 명령을 주고, **그건 사용자가 터미널에서 직접 실행**합니다
(브라우저 창과 콘솔 입력이 필요해 Claude 가 대신 못 돌립니다). 로그인을 마치고 콘솔에서
Enter 를 누르면 `qa_auto/auth.json` 에 세션이 저장됩니다.

> 생성되는 `auth.json` 에는 실제 로그인 세션이 들어갑니다. `--init` 이 `.gitignore` 를 같이
> 만들지만, 커밋 전에 한 번 더 확인하세요.

---

## sb-storyboard / sb-storyboard-figma

확정된 화면(HTML 목업 또는 Figma)으로부터 개발자용 화면설계 스토리보드를 PPTX 로 만듭니다.
"개발자가 목업을 열어보지 않아도 개발 가능한 수준"이 목표입니다.

두 플러그인은 캡처 방식만 다르고 산출물 스타일은 같습니다. HTML 판은 Playwright 로 hover·click
같은 인터랙션 상태를 실제로 실행시켜 캡처하지만, Figma 판은 정적 캔버스라 **디자이너가 상태별
프레임을 미리 만들어둔 경우에만** 해당 상태를 담을 수 있습니다.

### 실제 진행 흐름

```
/sb-storyboard        projects/내기획/목업_v1.html
/sb-storyboard-figma  https://figma.com/design/...?node-id=1234-5678
```

Claude 가 화면을 캡처하고 `projects/{프로젝트}/sb_build/` 에 빌드 스크립트를 만든 뒤
PPTX 를 뽑습니다. 스크립트가 남으니 문구를 고쳐 다시 빌드할 수 있습니다.

전제는 **화면이 이미 컨펌된 상태**라는 것입니다. 목업이 아직 안 굳었으면 그것부터 정리하세요 —
SB 는 확정본을 개발자에게 넘기는 문서라, 흔들리는 화면으로 만들면 두 번 일하게 됩니다.

### 준비물

```bash
pip install python-pptx Pillow playwright
py -m playwright install chromium
```

Figma 판은 Figma MCP 서버 연결이 필요합니다.
렌더 검증(PNG 내보내기)은 Windows + PowerPoint 환경에서만 동작하며, 없어도 PPTX 생성 자체는 됩니다.

### 스타일 모듈은 프로젝트마다 복사됩니다

`sb_helpers.py`(색·폰트·좌표 상수와 슬라이드 빌더)는 프로젝트 폴더 안에 사본으로 들어갑니다.
한 프로젝트에서 스타일을 고쳐도 **다른 프로젝트 SB 는 그대로**이고, 과거에 승인된 SB 를 다시
빌드해도 그때와 같은 결과가 나옵니다.

하우스 스타일 자체를 바꿔 전체에 반영하려면 각 프로젝트 사본을 개별로 고쳐야 합니다.
새 프로젝트는 항상 플러그인의 최신 모듈에서 시작하므로, 플러그인만 최신으로 두면 앞으로
만드는 SB 는 자동으로 새 스타일을 씁니다.

---

## 라이선스

MIT
