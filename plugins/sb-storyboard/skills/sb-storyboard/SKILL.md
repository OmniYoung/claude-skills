---
name: sb-storyboard
description: 확정된 HTML 화면 목업으로부터 개발자용 화면설계 스토리보드(SB, PPTX)를 만든다. 컨펌된 HTML 목업 경로가 주어지고, 개발자가 목업을 열어보지 않아도 되는 수준의 PPTX 스토리보드로 문서화해야 할 때 사용한다.
---

# SB(화면설계 스토리보드) 생성 — HTML 목업 소스

컨펌된 HTML 화면 목업으로부터 개발자용 화면설계 스토리보드(SB, PPTX)를 만든다. 사용자가 HTML 목업 경로를 주지 않았으면 먼저 요청한다.

> 전제: 화면 기획은 **① HTML 목업 → ② 이해관계자 컨펌 → ③ SB(PPTX)** 순서로 진행한다. 이 스킬은 **목업이 이미 컨펌된 뒤**에 쓴다 — 목업 자체가 아직 안 굳었으면 먼저 HTML을 다듬고 컨펌부터 받을 것.
>
> 목표는 "개발자가 이 SB만 보고 목업을 열어보지 않아도 개발 가능한 수준"이다. 스크린샷에만 있고 텍스트로는 없는 문구(툴팁·토스트·안내문 등)는 반드시 해당 항목 설명에 그대로 인용해서 옮겨 적을 것.

## 준비물

1. **컨펌된 HTML 목업** — 인터랙션(필터·정렬·인라인편집·툴팁·토스트 등)이 전부 실제로 동작해야 함.
2. **재사용 모듈**: `pptx_sb/sb_helpers.py` — 이 스킬 패키지에 함께 포함돼 있어 **별도 설치가 필요 없다**. v1.6 하우스 스타일(파란 헤더 배지, 요약 박스, 번호 지시선, 컬럼 스펙 그리드, 인터랙션 상태 그리드, 표지, 버전 관리 표)이 전부 함수로 있다. 이 SKILL.md를 읽은 실제 경로를 기준으로 같은 폴더의 `pptx_sb/sb_helpers.py` 절대경로를 찾아 sys.path에 추가해서 import한다 — 예를 들어 이 파일이 `~/.claude/skills/sb-storyboard/SKILL.md`에 설치돼 있다면 `~/.claude/skills/sb-storyboard/pptx_sb`를 추가한다. **이 파일 자체는 수정하지 말고** import해서 콘텐츠만 채운다.
3. **파이썬 의존성**: `python-pptx`, `Pillow`, `playwright`(+ `playwright install chromium`)가 사용자 환경에 없으면 설치를 안내한다.
4. **Windows + PowerPoint** — 렌더 검증(PNG 내보내기)과 안전한 파일 닫기(`close_if_open`)가 PowerPoint COM 자동화로 동작한다. 없으면 이 두 기능만 빠지고 PPTX 생성 자체는 가능하다.
5. 원 요구사항 문서(있으면) — SB 완성 후 반드시 원본 요구사항과 항목 단위로 대조한다. 부가 아이디어·수정 요청이 쌓여도 원본 요구사항 커버리지가 항상 최우선 기준이며, 업그레이드는 되어도 누락은 안 된다.
6. **작업 위치는 반드시 사용자 프로젝트 폴더 아래 `sb_build/`**(예: `projects/{프로젝트명}/sb_build/`) — 세션 임시 폴더에 만들면 세션 종료 시 사라져서 다음에 그 프로젝트 SB를 못 고친다. 폴더 구성 권장안:
   - `capture_new_mockup.py` — 목업 섹션/카드 전체 스크린샷 캡처
   - `capture_interaction_states.py` — hover/click/dblclick 등 인터랙션 상태 캡처
   - `build_sb.py` — sb_helpers를 import해서 실제 PPTX를 조립하는 빌드 스크립트
   - `assets/` — 캡처된 원본 PNG
7. **버전 관리 페이지는 모든 SB에 고정으로 넣는다** — 표지 바로 다음 슬라이드로 `sb_helpers.build_version_slide`를 쓴다. 새로 만드는 SB든 기존 SB를 갱신하는 것이든 예외 없이 넣는다.

## 실행 순서

### 1. 목업에서 스크린샷 확보 (Playwright)
- 섹션/카드 전체 스크린샷(컬럼 스펙 슬라이드용) — 카드 요소의 `bounding_box()` + 8px pad로 크롭.
- 컬럼 헤더 각각의 `bounding_box()`도 같이 뽑아서 JSON으로 저장해두면 `build_column_slide`의 `boxes_px_rel`을 손으로 재지 않아도 됨.
- 인터랙션 상태는 실제로 hover/click/dblclick을 실행시켜서 캡처 (하드코딩된 상태 이미지를 손으로 합성하지 말 것):
  - 호버 툴팁류: hover 후 `wait_for_timeout(150)` 뒤 캡처
  - 편집 모드: dblclick 후 캡처
  - 저장 완료(애니메이션 있는 경우): Enter 등으로 저장 트리거 직후 애니메이션 지속시간 이내(예: 80ms)에 캡처
  - 빈 상태처럼 실 데이터로 재현 안 되는 상태는 `page.evaluate()`로 강제 연출
  - 위로 열리는 툴팁(`bottom: 100%`)과 아래로 열리는 팝업(`top: 100%`)은 크롭 방향(`extra_top` vs `extra_bottom`)이 다르다 — 특히 sticky 헤더 안의 아이콘은 위로 열면 스크롤 영역 밖으로 잘리므로 CSS 자체를 아래로 열리게 뒤집어야 할 수 있음.

### 2. 빌드 스크립트 작성
```python
import sys
sys.path.insert(0, r"<이 SKILL.md를 읽은 실제 경로>/pptx_sb")  # 예: ~/.claude/skills/sb-storyboard/pptx_sb
from sb_helpers import *

prs = new_presentation()

build_cover_slide(prs, "제목 — 화면설계 스토리보드", "Ver 1.0 / YYYY-MM-DD / ...", [
    "버전 관리",
    "SB-01  개요 — ...",
    "SB-02  ... — 컬럼 명세",
    ...
])

build_version_slide(prs, [
    ("v1.0", "YYYY-MM-DD", "최초 작성 — SB-01 개요, SB-02 ..."),
    # 기존 SB를 갱신하는 경우 기존 이력 행을 그대로 두고 새 버전 행을 이어서 추가한다 (덮어쓰지 않는다)
])

slide1 = new_slide(prs)
add_header(slide1, "SB-01", "...", "신규 개발")
add_summary(slide1, [[...좌측 불릿...], [...우측 불릿...]], height=2.6, size=9.5)
# 필요하면 add_rect/add_oval_badge로 목업 스크린샷 위에 번호 지시선 직접 그리기

build_column_slide(prs, "SB-02", "품절관리", img_path, crop_w, crop_h, boxes_px_rel, items,
                    legend_labels=["어드민 화면명: URL", ...])

build_states_slide(prs, "SB-0N", "(1/M)", [[item1, item2, item3], [item4, item5]],
                    state_dir=STATE_DIR, img_max_h=4.4)

prs.save(OUT_PATH)
```
- `items` 튜플은 `(번호, 제목, 설명, is_check)` — `is_check=True`면 ★(주황)로 "확인 필요" 표시. 확인되면 반드시 `False`로 되돌리고 설명에 확정 근거를 채울 것. 완료 시 ★ 항목이 0개인지 grep으로 확인.
- 값이 원본을 그대로 보여주는 컬럼과, 산식으로 계산되어 나오는 컬럼을 구분한다 — 계산되는 컬럼(예: 비율·합산값)은 화면에 헤더 옆 정보 아이콘으로 산식 툴팁을 노출하고, SB 설명에도 정확한 산식(조건문 포함)을 적을 것.
- 인터랙션 상태는 한 슬라이드에 2행(각 2~3열)까지가 안전하다(`img_max_h=4.4` 기준 — 슬라이드 높이 초과). 항목이 넘치면 부록으로 빼지 말고 슬라이드 자체를 (N/총장수)로 나눌 것.
- SB 텍스트에는 산출물 자체에 대화 흔적을 남기지 않는다 — 왜 이렇게 정했는지 논의 서사 없이 결론(판정 공식, 데이터 출처, 식별자 등)만 담백하게 적는다.
- 기존 SB를 수정하는 경우 버전 관리 표에 새 행(버전·일자·변경 내용)을 추가하고 파일명도 버전을 올린다(`_v1.0` → `_v1.1` 등) — 이전 행은 지우지 않는다.

### 3. 렌더 확인 (자체 검수 루프)
```python
from sb_helpers import close_if_open, render_slides_to_png
close_if_open(OUT_PATH)          # PowerPoint에서 열려있으면 안전하게 닫기
# ... prs.save(OUT_PATH) 재실행 ...
pngs = render_slides_to_png(OUT_PATH, out_dir, slide_numbers=[1, 2, 3])
```
내보낸 PNG를 직접 열어 다음을 확인한다:
- 텍스트가 박스/이미지 밖으로 넘치지 않는지 (특히 불릿·설명 텍스트가 길어졌을 때)
- 배지 번호가 인접 도형에 가려지지 않는지
- 표지 TOC가 실제 슬라이드 구성과 일치하는지

겹침/오버플로 발견 시 텍스트를 줄이거나 박스 높이·이미지 위치를 조정하고 2단계부터 반복.

### 4. 원 요구사항 대조 + 정량 검증
- 원본 요구사항 문서가 있으면 항목별로 전부 커버됐는지 대조한다. 빠진 게 있으면 왜 빠졌는지(스코프 제외 확정인지, 그냥 누락인지) 확인 후 반영하거나 "이번 스코프 제외"로 명시적으로 처리한다.
- SB에 적은 산식·조건문을 원본 문서와 한 번 더 대조해서 AND/OR, 경계값(이상/초과), 예외 케이스(0으로 나누기 등)를 놓치지 않았는지 확인한다.

### 5. 마무리
- `close_if_open` 확인 후 최종 저장, `open_in_powerpoint(OUT_PATH)`로 사용자에게 열어 보여준다.
