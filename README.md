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

### 시작

```bash
py "<스킬경로>/qa_auto/qa_run.py" --init <프로젝트_루트>
```

로그인이 필요한 화면이면 브라우저에서 직접 로그인해 세션을 저장합니다.

```bash
py "<스킬경로>/qa_auto/qa_run.py" --login <로그인_URL> --ws qa_auto
```

> 생성되는 `auth.json` 에는 실제 로그인 세션이 들어갑니다. `--init` 이 `.gitignore` 를 같이
> 만들지만, 커밋 전에 한 번 더 확인하세요.

---

## sb-storyboard / sb-storyboard-figma

확정된 화면(HTML 목업 또는 Figma)으로부터 개발자용 화면설계 스토리보드를 PPTX 로 만듭니다.
"개발자가 목업을 열어보지 않아도 개발 가능한 수준"이 목표입니다.

두 플러그인은 캡처 방식만 다르고 산출물 스타일은 같습니다. HTML 판은 Playwright 로 hover·click
같은 인터랙션 상태를 실제로 실행시켜 캡처하지만, Figma 판은 정적 캔버스라 **디자이너가 상태별
프레임을 미리 만들어둔 경우에만** 해당 상태를 담을 수 있습니다.

### 준비물

```bash
pip install python-pptx Pillow playwright
py -m playwright install chromium
```

Figma 판은 Figma MCP 서버 연결이 필요합니다.
렌더 검증(PNG 내보내기)은 Windows + PowerPoint 환경에서만 동작하며, 없어도 PPTX 생성 자체는 됩니다.

---

## 라이선스

MIT
