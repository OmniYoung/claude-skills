# -*- coding: utf-8 -*-
"""
화면설계 스토리보드(SB) PPTX 빌더 — v1.6 전사_대시보드 하우스 스타일 재사용 모듈.

이 파일 자체를 수정하지 말고, 프로젝트별 빌드 스크립트에서
`from tools.pptx_sb.sb_helpers import *` 로 불러와 콘텐츠(문구·이미지·좌표값)만 채워 쓴다.
색상·폰트·좌표 상수를 건드려야 하는 경우가 아니면 이 파일은 그대로 둔다.

원본: 재고_대시보드 SB(2026-09) 제작 중 정립된 스타일을 일반화함.
참고: memory/feedback_ui_planning_html_then_sb.md, memory/reference_pptx_workflow.md

기본 워크플로우 (자세한 절차는 .claude/commands/sb-storyboard.md):
  1. HTML 목업을 만들고 인터랙션 상태를 Playwright로 캡처
  2. 이 모듈로 SB 슬라이드를 조립
  3. render_slides_to_png()로 렌더링 확인 (겹침·오버플로 체크)
  4. close_if_open() 으로 안전하게 닫고 재빌드 반복
"""
import os
import subprocess

from pptx import Presentation
from pptx.util import Cm, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from PIL import Image

# =========================================================
# 색상 (v1.6 하우스 스타일)
# =========================================================
BLUE = RGBColor(0x1D, 0x5B, 0xF0)
BLUE_DARK = RGBColor(0x12, 0x38, 0x95)
GRAY_TEXT = RGBColor(0x6B, 0x72, 0x80)
BODY_TEXT = RGBColor(0x1A, 0x1A, 0x1A)
DESC_TEXT = RGBColor(0x33, 0x33, 0x33)
AMBER = RGBColor(0xB4, 0x5A, 0x00)  # ★ 확인필요 항목용
BORDER_GRAY = RGBColor(0xCC, 0xD1, 0xD9)
BADGE_BG = RGBColor(0xE8, 0xF4, 0xFE)
BADGE_BORDER = RGBColor(0xA7, 0xD6, 0xFA)
SUMMARY_BG = RGBColor(0xF2, 0xF8, 0xFE)
SUMMARY_BORDER = RGBColor(0xD6, 0xE7, 0xF7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TOC_TEXT_COLOR = RGBColor(0xE4, 0xF2, 0xFE)  # 표지 TOC 전용 (흰 배경 위 연한 하늘색)

# =========================================================
# 레이아웃 상수 (와이드스크린 33.867 x 19.05cm 기준)
# =========================================================
SLIDE_W_EMU = 12192000
SLIDE_H_EMU = 6858000
PAGE_L = 1.14       # 좌측 여백(cm) — 모든 콘텐츠의 기준 왼쪽 좌표
PAGE_W = 31.57       # 콘텐츠 폭(cm) — 헤더 바·요약박스·그리드가 공유하는 폭


def new_presentation():
    """SB 표준 크기(33.867x19.05cm, 4:3 아님 와이드) 빈 프레젠테이션 생성."""
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W_EMU)
    prs.slide_height = Emu(SLIDE_H_EMU)
    return prs


def new_slide(prs):
    """빈 레이아웃(플레이스홀더 없음)으로 슬라이드 추가."""
    return prs.slides.add_slide(prs.slide_layouts[6])


# =========================================================
# 저수준 도형/텍스트 프리미티브
# =========================================================
def set_font(run, name="Malgun Gothic", size=11, bold=None, color=BODY_TEXT):
    run.font.name = name
    run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    run.font.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn('a:ea'))
    if ea is None:
        ea = rPr.makeelement(qn('a:ea'), {})
        rPr.append(ea)
    ea.set('typeface', name)


def add_textbox(slide, l, t, w, h, text, name="Malgun Gothic", size=11, bold=None,
                 color=BODY_TEXT, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
                 wrap=True, line_spacing=None):
    tb = slide.shapes.add_textbox(Cm(l), Cm(t), Cm(w), Cm(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    if line_spacing:
        p.line_spacing = line_spacing
    r = p.add_run()
    r.text = text
    set_font(r, name=name, size=size, bold=bold, color=color)
    return tb


def add_rect(slide, l, t, w, h, fill=None, line_color=None, line_w_pt=1.0, rounded=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(shape_type, Cm(l), Cm(t), Cm(w), Cm(h))
    if rounded:
        try:
            sh.adjustments[0] = 0.12
        except Exception:
            pass
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line_color is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line_color
        sh.line.width = Pt(line_w_pt)
    sh.shadow.inherit = False
    return sh


def add_oval_badge(slide, l, t, size, number, fill=BLUE, text_color=WHITE, font_size=9.5):
    """번호 원 배지 (컬럼 스펙 지시선, 인터랙션 상태 번호 등에 공용으로 씀)."""
    sh = slide.shapes.add_shape(MSO_SHAPE.OVAL, Cm(l), Cm(t), Cm(size), Cm(size))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = fill
    sh.line.width = Pt(1.0)
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = str(number)
    set_font(r, size=font_size, bold=True, color=text_color)
    return sh


def _set_bullet(p, char="•", indent_cm=0.42):
    pPr = p._p.get_or_add_pPr()
    pPr.set('marL', str(Cm(indent_cm)))
    pPr.set('indent', str(-Cm(indent_cm)))
    for tag in ('a:buNone', 'a:buChar', 'a:buAutoNum', 'a:buFont'):
        el = pPr.find(qn(tag))
        if el is not None:
            pPr.remove(el)
    buFont = pPr.makeelement(qn('a:buFont'), {'typeface': 'Arial'})
    buChar = pPr.makeelement(qn('a:buChar'), {'char': char})
    pPr.append(buFont)
    pPr.append(buChar)


def add_bullet_textbox(slide, l, t, w, h, items, name="Malgun Gothic", size=10.5,
                        color=BODY_TEXT, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.2, space_after=3):
    tb = slide.shapes.add_textbox(Cm(l), Cm(t), Cm(w), Cm(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = line_spacing
        if i < len(items) - 1:
            p.space_after = Pt(space_after)
        _set_bullet(p)
        r = p.add_run()
        r.text = item
        set_font(r, name=name, size=size, color=color)
    return tb


# =========================================================
# 슬라이드 공용 헤더/요약 박스
# =========================================================
def add_header(slide, sb_id, title_text, status_text):
    """SB 배지 + 타이틀 + 우측 상태 배지 + 파란 구분선. 모든 콘텐츠 슬라이드 상단에 공통 사용."""
    add_rect(slide, PAGE_L, 0.76, 2.34, 0.86, fill=BLUE, line_color=BLUE, line_w_pt=1.0, rounded=True)
    add_textbox(slide, PAGE_L, 0.76, 2.34, 0.86, sb_id, size=12, bold=True, color=WHITE,
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_textbox(slide, 3.81, 0.71, 21.84, 0.97, title_text, size=19, bold=True, color=BLUE,
                anchor=MSO_ANCHOR.MIDDLE)
    add_rect(slide, 26.37, 0.79, 6.35, 0.81, fill=BADGE_BG, line_color=BADGE_BORDER, line_w_pt=1.0, rounded=True)
    add_textbox(slide, 26.37, 0.79, 6.35, 0.81, status_text, size=10, color=BLUE_DARK,
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    ln = slide.shapes.add_connector(1, Cm(PAGE_L), Cm(1.83), Cm(PAGE_L + PAGE_W), Cm(1.83))
    ln.line.color.rgb = BLUE
    ln.line.width = Pt(1.6)


def add_summary(slide, bullets, height=1.17, size=10.5):
    """
    상단 요약 박스. `bullets`는 세 가지 형태를 받는다:
      - str: 기존 방식의 한 문단 텍스트(줄글)
      - list[str]: 불릿 목록 (단일 컬럼)
      - list[list[str]]: 여러 컬럼에 나눠 담는 불릿 목록 (예: 좌우 2단)
    문장이 길면 한 문단에 다 몰아넣지 말고 불릿로 쪼개는 쪽이 가독성이 좋다
    (2026-09 재고 대시보드 SB에서 사용자 피드백으로 확립).
    """
    add_rect(slide, PAGE_L, 2.84, PAGE_W, height, fill=SUMMARY_BG, line_color=SUMMARY_BORDER,
              line_w_pt=1.0, rounded=True)
    inner_w = PAGE_W - 0.36  # 좌우 각 0.36cm 여백 확보한 텍스트 폭
    if isinstance(bullets, str):
        add_textbox(slide, PAGE_L + 0.36, 2.84, inner_w, height, bullets, size=size, color=BODY_TEXT,
                    anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.1)
    elif bullets and isinstance(bullets[0], list):
        col_gap = 0.9
        col_w = (inner_w - col_gap * (len(bullets) - 1)) / len(bullets)
        for i, col_items in enumerate(bullets):
            add_bullet_textbox(slide, PAGE_L + 0.36 + i * (col_w + col_gap), 2.84, col_w, height, col_items,
                                size=size, color=BODY_TEXT, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.2)
    else:
        add_bullet_textbox(slide, PAGE_L + 0.36, 2.84, inner_w, height, bullets, size=size, color=BODY_TEXT,
                            anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.2)


# =========================================================
# 표지 슬라이드 (v1.6 원본에서 python-pptx로 좌표·폰트 직접 추출해 재현)
# =========================================================
def build_cover_slide(prs, title, subtitle, toc_lines):
    """
    배경 단색 BLUE, 흰 타이틀(34pt bold) + 부제(13.5pt) + 흰 구분선 + TOC(11pt, 연한 하늘색).
    title이 2줄로 접힐 걸 감안해 타이틀 박스 높이(2.986cm)에 여유를 뒀다 — 1줄일 땐 자동으로
    가운데 정렬되지 않고 위쪽에 붙으므로, 짧은 타이틀도 이 함수 그대로 써도 무방하다.
    subtitle 이하 요소 좌표는 이 여유폭 기준으로 고정돼 있다.
    """
    cover = new_slide(prs)
    cover.background.fill.solid()
    cover.background.fill.fore_color.rgb = BLUE

    add_textbox(cover, 2.54, 5.461, 28.70, 2.986, title, size=34, bold=True, color=WHITE)
    add_textbox(cover, 2.54, 8.447, 28.70, 1.016, subtitle, size=13.5, color=WHITE)

    divider = cover.shapes.add_connector(1, Cm(2.54), Cm(9.895), Cm(2.54 + 10.668), Cm(9.895))
    divider.line.color.rgb = WHITE
    divider.line.width = Pt(1.4)

    toc_tb = cover.shapes.add_textbox(Cm(2.54), Cm(13.767), Cm(28.70), Cm(3.81))
    toc_tf = toc_tb.text_frame
    toc_tf.word_wrap = True
    toc_tf.margin_left = 0
    toc_tf.margin_right = 0
    toc_tf.margin_top = 0
    toc_tf.margin_bottom = 0
    for i, line in enumerate(toc_lines):
        p = toc_tf.paragraphs[0] if i == 0 else toc_tf.add_paragraph()
        r = p.add_run()
        r.text = line
        set_font(r, size=11, color=TOC_TEXT_COLOR)
    return cover


# =========================================================
# 버전 관리 슬라이드 (2026-09-11부터 모든 SB 세트에 표지 다음 고정 삽입)
# =========================================================
def build_version_slide(prs, history, col_w=None, row_h=1.8, status_text="문서 관리"):
    """
    표지 바로 다음 슬라이드로 고정 사용하는 버전 이력 페이지 (개원체크리스트_배너관리 SB에서 처음 도입).
    history: [(버전, 일자, 변경내용), ...] — 위에서부터 순서대로 행이 쌓인다(보통 오래된 버전이 위, 최신이 아래).
    row_h: 데이터 행 높이(cm, 기본 1.8) — 변경내용이 길어 줄바꿈되면 늘릴 것. 행마다 다르게 주려면
    history 개수와 같은 길이의 list로 넘긴다.
    표지의 build_cover_slide(toc_lines)에는 SB 번호 없이 "버전 관리" 한 줄만 맨 앞에 추가해서 맞춘다.
    """
    slide = new_slide(prs)
    add_header(slide, "이력", "버전 관리", status_text)

    col_w = col_w or [2.4, 3.0, PAGE_W - 2.4 - 3.0]
    row_h_list = row_h if isinstance(row_h, list) else [row_h] * len(history)
    rows = [("버전", "일자", "변경 내용")] + list(history)
    row_heights = [1.0] + row_h_list

    gframe = slide.shapes.add_table(len(rows), 3, Cm(PAGE_L), Cm(3.2),
                                     Cm(sum(col_w)), Cm(sum(row_heights)))
    table = gframe.table
    for ci, cw in enumerate(col_w):
        table.columns[ci].width = Cm(cw)
    for ri, rh in enumerate(row_heights):
        table.rows[ri].height = Cm(rh)

    for ri, row in enumerate(rows):
        is_head = ri == 0
        for ci, cell_text in enumerate(row):
            cell = table.cell(ri, ci)
            cell.margin_left = Cm(0.3)
            cell.margin_right = Cm(0.3)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = BADGE_BG if is_head else WHITE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            r = p.add_run()
            r.text = cell_text
            set_font(r, size=11 if is_head else 10, bold=is_head,
                     color=BLUE_DARK if is_head else BODY_TEXT)
    return slide


# =========================================================
# 컬럼 명세 슬라이드 (표 스크린샷 + 번호 지시선 + 3열×2행 설명 그리드)
# =========================================================
def build_column_slide(prs, sb_id, section_label, img_path, crop_w, crop_h, boxes_px_rel, items,
                        legend_labels=None, extra_note="", summary_text=None):
    """
    img_path: 해당 섹션 표만 크롭한 스크린샷 (카드 전체, pad 포함) 경로.
    crop_w/crop_h: 그 스크린샷의 원본 픽셀 크기 (Playwright bounding_box + pad 기준).
    boxes_px_rel: {번호: (x, y, w, h)} — 스크린샷 좌표계 기준 각 컬럼의 픽셀 박스.
        (thead th의 bounding_box에서 카드 크롭 원점을 뺀 값 — 캡처 스크립트에서 함께 뽑아둘 것)
    items: [(번호, 제목, 설명, is_check)] — is_check=True면 아직 확인 안 된 항목(★, 주황색)으로 표시.
        확인되면 반드시 False로 되돌리고 설명에 확정 근거를 채울 것 — ★가 남아있으면 안 됨.
    legend_labels: 하단에 모노스페이스로 붙는 "어드민 참고 경로" 목록 (list[str] 또는 None).
    summary_text: 상단 요약 박스 문구. 기본값은 "표 컬럼 단위로 데이터 출처를 정리한다."
    """
    slide = new_slide(prs)
    add_header(slide, sb_id, f"{section_label} — 컬럼 명세", "컬럼 정의")
    add_summary(
        slide,
        (summary_text or "표 컬럼 단위로 데이터 출처를 정리한다.") + (f" {extra_note}" if extra_note else ""),
    )

    img_l2, img_t2 = PAGE_L, 4.36
    img_w2 = 29.0
    img_h2 = img_w2 * crop_h / crop_w
    slide.shapes.add_picture(img_path, Cm(img_l2), Cm(img_t2), Cm(img_w2), Cm(img_h2))
    add_rect(slide, img_l2, img_t2, img_w2, img_h2, fill=None, line_color=BORDER_GRAY, line_w_pt=0.75)

    sx = img_w2 / crop_w
    sy = img_h2 / crop_h

    def box_cm(x, y, w, h):
        return (img_l2 + x * sx, img_t2 + y * sy, w * sx, h * sy)

    is_check_map = {n: chk for n, _, _, chk in items}
    narrow_nums = {n for n, b in boxes_px_rel.items() if b[2] < 70}

    # 박스 테두리를 전부 먼저 그리고 배지(원)를 나중에 그려야 인접 박스가 배지를 가리는 z-order
    # 충돌이 안 생긴다 (2026-09 확인된 실수 — 반드시 2-pass로 그릴 것).
    badge_specs = []
    for num, (x, y, w, h) in boxes_px_rel.items():
        color = AMBER if is_check_map[num] else BLUE
        l, t, w_cm, h_cm = box_cm(x, y, w, h)
        add_rect(slide, l, t, w_cm, h_cm, fill=None, line_color=color, line_w_pt=1.5)
        badge_t = (t + h_cm - 0.24) if num in narrow_nums else (t - 0.24)
        badge_specs.append((l - 0.24, badge_t, num, color))
    for l, t, num, color in badge_specs:
        add_oval_badge(slide, l, t, 0.48, num, fill=color, font_size=8.5)

    # 설명 그리드: 3열 x N행, 이미지의 좌→우 컬럼 순서와 동일하게 좌상단부터 채운다.
    grid_top = img_t2 + img_h2 + 0.5
    n_cols = 3
    gap = 0.7
    col_w = (PAGE_W - (n_cols - 1) * gap) / n_cols
    row_h = 3.0
    row_gap = 0.2

    n_rows = 0
    for i, (num, title, desc, is_check) in enumerate(items):
        col = i % n_cols
        row = i // n_cols
        n_rows = max(n_rows, row + 1)
        cell_l = img_l2 + col * (col_w + gap)
        cell_t = grid_top + row * (row_h + row_gap)
        color = AMBER if is_check else BLUE_DARK
        add_oval_badge(slide, cell_l, cell_t, 0.48, num, fill=(AMBER if is_check else BLUE), font_size=8.5)
        label = title + (" ★" if is_check else "")
        add_textbox(slide, cell_l + 0.66, cell_t + 0.02, col_w - 0.66, 0.5, label, size=11, bold=True, color=color)
        add_textbox(slide, cell_l, cell_t + 0.62, col_w, row_h - 0.62, desc, size=8.5, color=DESC_TEXT, line_spacing=1.15)

    if legend_labels:
        legend_y = grid_top + n_rows * row_h + (n_rows - 1) * row_gap + 0.3
        add_textbox(slide, PAGE_L, legend_y, PAGE_W, 1.2,
                    "어드민 참고 경로 — " + "  ·  ".join(legend_labels),
                    name="Consolas", size=8, color=GRAY_TEXT, line_spacing=1.25)

    return slide


# =========================================================
# 인터랙션 상태 슬라이드 (hover/click/dblclick 캡처 모음)
# =========================================================
def build_state_row(slide, items_row, row_top, row_h, img_max_h, state_dir):
    """items_row: [(번호, 제목, 이미지파일명, 설명)]. 이미지파일명은 state_dir 기준 상대경로."""
    gap = 0.6
    title_h = 0.55
    n_cols = len(items_row)
    col_w = (PAGE_W - (n_cols - 1) * gap) / n_cols
    for c, (num, title, img_name, desc) in enumerate(items_row):
        cell_l = PAGE_L + c * (col_w + gap)
        cell_t = row_top

        add_oval_badge(slide, cell_l, cell_t, 0.48, num, fill=BLUE, font_size=8.5)
        add_textbox(slide, cell_l + 0.66, cell_t + 0.02, col_w - 0.66, title_h, title,
                    size=11, bold=True, color=BLUE_DARK)

        img_path = os.path.join(state_dir, img_name)
        with Image.open(img_path) as im:
            px_w, px_h = im.size
        scale = min(col_w / px_w, img_max_h / px_h)
        disp_w, disp_h = px_w * scale, px_h * scale
        img_l = cell_l + (col_w - disp_w) / 2
        img_t = cell_t + title_h + (img_max_h - disp_h) / 2
        slide.shapes.add_picture(img_path, Cm(img_l), Cm(img_t), Cm(disp_w), Cm(disp_h))
        add_rect(slide, img_l, img_t, disp_w, disp_h, fill=None, line_color=BORDER_GRAY, line_w_pt=0.5)

        desc_t = cell_t + title_h + img_max_h + 0.1
        add_textbox(slide, cell_l, desc_t, col_w, row_h - (desc_t - cell_t), desc,
                    size=9, color=DESC_TEXT, line_spacing=1.15)


def build_states_slide(prs, sb_id, part_label, rows, state_dir, img_max_h=4.4, title="인터랙션 상태",
                        status_text="공통 상태",
                        summary_text="마우스 오버·클릭·더블클릭에서만 나타나는 화면 상태를 캡처했다."):
    """
    rows: [[items_row1], [items_row2], ...] — 각 행에 원하는 개수(보통 2~3개)의 항목을 담는다.
    한 슬라이드에 최대 2행까지가 안전하다(img_max_h=4.4 기준, row_top=4.5부터 시작해서 슬라이드
    높이 19.05cm를 넘김). 항목이 더 많으면 슬라이드를 나누고 sb_id/part_label을 (n/총장수)로 늘릴 것
    — 부록으로 따로 빼지 말고 인터랙션 상태 슬라이드 자체를 나누는 쪽을 사용자가 선호함
    (2026-09 확인, memory/feedback_ui_planning_html_then_sb.md 참고).
    스크린샷에만 있고 텍스트로는 없는 문구(툴팁·토스트·안내문 등)는 반드시 desc에 그대로
    인용부호로 옮겨 적을 것 — 개발자가 스크린샷 없이도 그 문구를 복사해 쓸 수 있어야 한다.
    """
    slide = new_slide(prs)
    add_header(slide, sb_id, f"{title} {part_label}", status_text)
    add_summary(slide, summary_text)
    row_h = 0.55 + img_max_h + 0.9
    row_gap = 0.5
    row_top = 4.5
    for items_row in rows:
        build_state_row(slide, items_row, row_top, row_h, img_max_h, state_dir)
        row_top += row_h + row_gap
    return slide


# =========================================================
# 렌더 검증 / 안전한 파일 조작 유틸 (PowerPoint COM)
# =========================================================
def close_if_open(pptx_path):
    """
    같은 파일이 PowerPoint에서 열려 있으면 저장 안 된 변경사항이 없는 경우에만 닫는다.
    재빌드(prs.save) 전에 항상 먼저 호출할 것 — 안 그러면 PermissionError로 저장이 막힌다.
    반환: "closed" | "not_open" | "unsaved_changes"(닫지 않음) | "no_powerpoint"
    """
    script = f'''
    try {{
      $p = [System.Runtime.InteropServices.Marshal]::GetActiveObject("PowerPoint.Application")
      $found = $false
      foreach ($pres in $p.Presentations) {{
        if ($pres.FullName -eq "{pptx_path}") {{
          $found = $true
          if ($pres.Saved -eq -1) {{ $pres.Close(); Write-Output "closed" }}
          else {{ Write-Output "unsaved_changes" }}
        }}
      }}
      if (-not $found) {{ Write-Output "not_open" }}
    }} catch {{
      Write-Output "no_powerpoint"
    }}
    '''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                             capture_output=True, text=True)
    return result.stdout.strip()


def render_slides_to_png(pptx_path, out_dir, slide_numbers=None, width=2400, height=1350):
    """
    PowerPoint COM으로 슬라이드를 PNG로 내보내 Read 툴로 직접 눈으로 확인하는 용도.
    slide_numbers: 1-based 번호 리스트. None이면 전체 슬라이드를 내보낸다.
    반환: {슬라이드번호: png경로}
    주의: 이 함수를 부르기 전에 close_if_open()으로 열려있는 사본을 먼저 정리할 것.
    """
    os.makedirs(out_dir, exist_ok=True)

    if slide_numbers is None:
        count_script = f'''
        $app = New-Object -ComObject PowerPoint.Application
        $pres = $app.Presentations.Open("{pptx_path}", $true, $false, $false)
        Write-Output $pres.Slides.Count
        $pres.Close()
        $app.Quit()
        '''
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", count_script],
                              capture_output=True, text=True)
        n = int(out.stdout.strip().splitlines()[-1])
        slide_numbers = list(range(1, n + 1))

    results = {}
    export_lines = []
    for i in slide_numbers:
        png_path = os.path.join(out_dir, f"slide{i}.png")
        export_lines.append(f'$pres.Slides.Item({i}).Export("{png_path}", "PNG", {width}, {height})')
        results[i] = png_path

    script = f'''
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open("{pptx_path}", $true, $false, $false)
    {'; '.join(export_lines)}
    $pres.Close()
    $app.Quit()
    '''
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                    capture_output=True, text=True, check=True)
    return results


def open_in_powerpoint(pptx_path):
    """작업 종료 시 사용자에게 결과물을 보여주기 위해 기본 연결 프로그램(PowerPoint)으로 연다."""
    os.startfile(pptx_path)  # Windows 전용
