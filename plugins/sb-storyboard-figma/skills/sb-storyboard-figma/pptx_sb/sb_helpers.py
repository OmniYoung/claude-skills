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
    rPr.set('lang', 'ko-KR')   # 한국어 지정: PowerPoint가 한글을 단어 단위로 줄바꿈(지정 없으면 「오/더」처럼 글자 단위로 끊음)
    rPr.set('altLang', 'en-US')


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


def _summary_lines(bullets, size):
    if isinstance(bullets, str):
        items = [bullets]
    elif bullets and isinstance(bullets[0], list):
        items = [x for col in bullets for x in col]
    else:
        items = list(bullets or [])
    avail = PAGE_W - 1.4
    def width(x):  # 한글 · 전각 약 0.92em, 영문 · 숫자 · 공백 약 0.5em
        return sum((0.92 if ord(ch) >= 0x1100 else 0.5) for ch in x) * size * 0.0353
    return sum(max(1, -(-width(x) // avail)) for x in items)


def add_summary(slide, bullets, height=None, size=9.5):
    """
    상단 요약 박스. `bullets`는 세 가지 형태를 받는다:
      - str: 기존 방식의 한 문단 텍스트(줄글)
      - list[str]: 불릿 목록 (단일 컬럼)
      - list[list[str]]: 여러 컬럼에 나눠 담는 불릿 목록 (예: 좌우 2단)
    문장이 길면 한 문단에 다 몰아넣지 말고 불릿로 쪼개는 쪽이 가독성이 좋다
    (2026-09 재고 대시보드 SB에서 사용자 피드백으로 확립).
    """
    if isinstance(bullets, str):   # 한 문장도 점 있는 목록으로(모든 장 같은 모양)
        bullets = [bullets]
    if bullets and not isinstance(bullets[0], list):   # 요약 줄 끝 마침표 없이 통일
        bullets = [x[:-1] if x.endswith('.') else x for x in bullets]
    if height is None:   # 줄 높이 + 문단 간격을 실제대로, 위아래 여백은 줄 수와 관계없이 같게
        n_par = len(bullets) if not isinstance(bullets[0], list) else max(len(c) for c in bullets)
        height = 2 * 0.25 + size * 0.0421 * _summary_lines(bullets, size) + 0.106 * (n_par - 1)
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
    return height


# =========================================================
# 표지 슬라이드 (v1.6 원본에서 python-pptx로 좌표·폰트 직접 추출해 재현)
# =========================================================
def build_cover_slide(prs, title, subtitle, toc_lines, toc_cols=3):
    """
    배경 단색 BLUE, 흰 타이틀(34pt bold) + 부제(13.5pt) + 흰 구분선 + TOC(연한 하늘색).
    title이 2줄로 접힐 걸 감안해 타이틀 박스 높이(2.986cm)에 여유를 뒀다 — 1줄일 땐 자동으로
    가운데 정렬되지 않고 위쪽에 붙으므로, 짧은 타이틀도 이 함수 그대로 써도 무방하다.
    subtitle 이하 요소 좌표는 이 여유폭 기준으로 고정돼 있다.
    toc_lines:
      - [(구역 이름, [항목, ...]), ...]  → 왼쪽 구역 이름 칸(굵은 흰 글씨) + 오른쪽 toc_cols열 격자(권장).
        SB 번호가 세로로 줄 맞고 구역이 끊어져 보인다. 첫 구역 이름은 "" 로 두면 「버전 관리 · SB-01」 줄.
        항목이 10줄 이하면(화면 단위 SB 등) 구역 칸 없이 세로 목록으로 그린다.
      - [문자열, ...]  → 예전 방식(한 줄씩 나열). 기존 빌드 스크립트 호환용.
    """
    if toc_lines and isinstance(toc_lines[0], (tuple, list)) and sum(len(x[1]) for x in toc_lines) <= 10:
        toc_lines = [it for _, items in toc_lines for it in items]   # SB가 적으면(10줄 이하) 구역 칸 없이 세로 목록
    if toc_lines and isinstance(toc_lines[0], (tuple, list)):
        cover = new_slide(prs)
        cover.background.fill.solid()
        cover.background.fill.fore_color.rgb = BLUE
        add_textbox(cover, 2.54, 5.461, 28.70, 2.986, title, size=34, bold=True, color=WHITE)
        add_textbox(cover, 2.54, 8.447, 28.70, 1.016, subtitle, size=13.5, color=WHITE)
        divider = cover.shapes.add_connector(1, Cm(2.54), Cm(9.895), Cm(2.54 + 10.668), Cm(9.895))
        divider.line.color.rgb = WHITE
        divider.line.width = Pt(1.4)
        ty, rh, cw = 11.0, 0.52, 25.8 / toc_cols
        for grp, items in toc_lines:
            if grp:
                add_textbox(cover, 2.54, ty, 3.0, rh, grp, size=10.5, bold=True, color=WHITE)
            for k, it in enumerate(items):
                add_textbox(cover, 5.8 + (k % toc_cols) * cw, ty + (k // toc_cols) * rh, cw - 0.2, rh, it,
                            size=10.5, color=TOC_TEXT_COLOR)
            ty += ((len(items) - 1) // toc_cols + 1) * rh + 0.22
        return cover
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
def _est_text_h(text, width_cm, size=9, line_spacing=1.15):
    """설명 텍스트 높이 추정(한글 한 글자 ≈ 글자 크기 pt × 0.036cm, 영숫자는 그 절반)."""
    lines = 0
    for para in text.split("\n"):
        w = sum(size * (0.036 if ord(ch) > 0x2000 else 0.02) for ch in para)
        lines += max(1, int(w / max(width_cm, 1)) + (1 if w % max(width_cm, 1) > 0 else 0)) if para else 1
    return lines * size * 0.0353 * line_spacing + 0.15


def build_state_row(slide, items_row, row_top, row_h, img_max_h, state_dir, img_frac=0.6):
    """items_row: [(번호, 제목, 이미지파일명, 설명)]. 이미지파일명은 state_dir 기준 상대경로.
    배치: 칸마다 왼쪽 캡처 · 오른쪽 번호 · 제목 · 설명. 모든 상태 슬라이드에서 같은 배치로 통일한다
    (다른 화면 슬라이드도 캡처 왼쪽 · 설명 오른쪽이라 한 기획서 안에서 맞춤, 2026-10-28 피드백).
    가로로 긴 캡처는 한 행에 한 칸씩 넣는다. row_h는 무시하고 실제로 쓴 행 높이를 돌려준다."""
    gap = 0.8
    n_cols = len(items_row)
    col_w = (PAGE_W - (n_cols - 1) * gap) / n_cols
    cells = []
    for num, title, img_name, desc in items_row:
        img_path = os.path.join(state_dir, img_name)
        with Image.open(img_path) as im:
            px_w, px_h = im.size
        scale = min(col_w * img_frac / px_w, img_max_h / px_h)
        disp_w, disp_h = px_w * scale, px_h * scale
        text_w = col_w - disp_w - 0.5
        text_h = 0.6 + _est_text_h(desc, text_w)
        cells.append((num, title, img_path, desc, disp_w, disp_h, text_w, text_h))
    used_h = max(max(c[5], c[7]) for c in cells)
    for c, (num, title, img_path, desc, disp_w, disp_h, text_w, text_h) in enumerate(cells):
        cell_l = PAGE_L + c * (col_w + gap)
        slide.shapes.add_picture(img_path, Cm(cell_l), Cm(row_top), Cm(disp_w), Cm(disp_h))
        add_rect(slide, cell_l, row_top, disp_w, disp_h, fill=None, line_color=BORDER_GRAY, line_w_pt=0.5)
        tx = cell_l + disp_w + 0.5
        add_oval_badge(slide, tx, row_top, 0.48, num, fill=BLUE, font_size=8.5)
        add_textbox(slide, tx + 0.66, row_top + 0.02, text_w - 0.66, 0.55, title,
                    size=11, bold=True, color=BLUE_DARK)
        add_textbox(slide, tx, row_top + 0.6, text_w, used_h - 0.6, desc,
                    size=9, color=DESC_TEXT, line_spacing=1.15)
    return used_h


def build_states_slide(prs, sb_id, part_label, rows, state_dir, img_max_h=4.4, title="인터랙션 상태",
                        status_text="공통 상태",
                        summary_text="마우스 오버·클릭·더블클릭에서만 나타나는 화면 상태를 캡처했다.",
                        img_frac=0.6):
    """
    rows: [[items_row1], [items_row2], ...]. 한 행에 1~2칸(왼쪽 캡처 · 오른쪽 설명이라 3칸이면 좁다).
    가로로 긴 캡처(목록 행 · 배너 등)는 한 행에 한 칸. 행 높이는 캡처와 설명 중 큰 쪽에 맞춰 자동으로 정해진다.
    슬라이드 높이(19.05cm)를 넘으면 경고를 출력한다. 넘치면 부록으로 빼지 말고 슬라이드를 (n/총장수)로 나눌 것.
    스크린샷에만 있고 텍스트로는 없는 문구(툴팁·토스트·안내문 등)는 반드시 desc에 그대로
    인용부호로 옮겨 적을 것. 개발자가 스크린샷 없이도 그 문구를 복사해 쓸 수 있어야 한다.
    """
    slide = new_slide(prs)
    add_header(slide, sb_id, f"{title} {part_label}", status_text)
    add_summary(slide, summary_text)
    row_gap = 0.6
    row_top = 4.5
    for items_row in rows:
        row_top += build_state_row(slide, items_row, row_top, None, img_max_h, state_dir, img_frac) + row_gap
    if row_top - row_gap > 19.0:
        print(f"  !! {sb_id} {part_label} 상태 슬라이드 넘침 {row_top - row_gap:.1f}cm")
    return slide


# =========================================================
# 동작 흐름 슬라이드 (왼쪽 플로우차트 + 오른쪽 번호 설명)
# =========================================================
FLOW_RED = RGBColor(0xD9, 0x2D, 0x20)
FLOW_LIGHT = RGBColor(0xEE, 0xF3, 0xFE)


def _flow_node(slide, kind, cx, cy, w, h, text, border=BLUE, fill=WHITE, bold=False):
    shape_type = {"decision": MSO_SHAPE.DIAMOND, "process": MSO_SHAPE.RECTANGLE}.get(kind, MSO_SHAPE.ROUNDED_RECTANGLE)
    sh = slide.shapes.add_shape(shape_type, Cm(cx - w / 2), Cm(cy - h / 2), Cm(w), Cm(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = border
    sh.line.width = Pt(1.25)
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Cm(0.08)
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = line
        set_font(r, size=9, bold=bold, color=BODY_TEXT)
    return sh


def _flow_arrow(slide, x1, y1, x2, y2, label=None, label_at=None):
    c = slide.shapes.add_connector(1, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
    c.line.color.rgb = GRAY_TEXT
    c.line.width = Pt(1.25)
    ln = c.line._get_or_add_ln()
    tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
    ln.append(tail)
    if label:
        lx, ly = label_at
        add_textbox(slide, lx, ly, 1.6, 0.45, label, size=8, color=GRAY_TEXT)


def build_flow_slide(prs, sb_id, title, flow, items, status_text="변경", summary=None, side_gap=1.6):
    """
    여러 단계·분기가 있는 동작(업로드, 저장 검증, 결제, 승인 등)을 왼쪽 플로우차트 + 오른쪽 번호 설명으로 그린다.
    글로만 나열하면 검사 순서와 갈림길이 안 보이므로, 분기가 2개 이상인 동작은 이 슬라이드를 쓴다.

    flow: 위에서 아래로 내려가는 본 흐름 단계 목록. 각 단계는 dict:
        {"text": "파일 선택됨?", "kind": "start|process|decision|end",
         "down": "예",                       # 다음 단계로 가는 화살표 라벨(선택)
         "side": {"text": "알럿 ① · 중단",   # 옆으로 빠지는 결과(선택)
                  "label": "아니오", "tone": "fail|neutral|ok"}}
      - decision 은 마름모, process 는 사각형, start/end 는 둥근 사각형.
      - 그림 안에는 짧은 라벨만 넣고 번호(①~)를 붙인다. 알럿·확인창 문구 원문은 items 설명에만 「」로 적는다
        (같은 문구를 그림과 설명에 두 번 쓰지 않는다).
    items: 오른쪽 설명 [(번호, 제목, [설명 줄, ...]), ...] — 번호는 그림 안 번호와 맞춘다.
    """
    slide = new_slide(prs)
    add_header(slide, sb_id, title, status_text)
    top = 2.84
    if summary:
        add_summary(slide, summary)
        top = 4.4
    main_cx, main_w, dec_w, dec_h, box_h = PAGE_L + 3.6, 5.9, 6.5, 1.55, 1.0
    side_w = 4.5
    side_cx = main_cx + dec_w / 2 + side_gap + side_w / 2
    heights = [dec_h if st.get("kind") == "decision" else box_h for st in flow]
    avail = 18.4 - top - heights[0] / 2 - heights[-1] / 2
    pitch = min(2.1, avail / max(1, len(flow) - 1))
    ys = [top + heights[0] / 2 + i * pitch for i in range(len(flow))]
    for i, st in enumerate(flow):
        kind = st.get("kind", "process")
        is_end = kind in ("start", "end")
        _flow_node(slide, kind, main_cx, ys[i], dec_w if kind == "decision" else main_w, heights[i], st["text"],
                   fill=FLOW_LIGHT if kind in ("decision", "end") else WHITE, bold=is_end)
        if i < len(flow) - 1:
            y1, y2 = ys[i] + heights[i] / 2, ys[i + 1] - heights[i + 1] / 2
            _flow_arrow(slide, main_cx, y1, main_cx, y2, st.get("down"), (main_cx + 0.15, (y1 + y2) / 2 - 0.25))
        sd = st.get("side")
        if sd:
            tone = sd.get("tone", "fail")
            border = {"fail": FLOW_RED, "neutral": GRAY_TEXT, "ok": BLUE}[tone]
            sh_h = box_h if "\n" not in sd["text"] else box_h + 0.4
            _flow_node(slide, "end", side_cx, ys[i], side_w, sh_h, sd["text"], border=border)
            x1 = main_cx + (dec_w if kind == "decision" else main_w) / 2
            _flow_arrow(slide, x1, ys[i], side_cx - side_w / 2, ys[i], sd.get("label"), (x1 + 0.1, ys[i] - 0.55))
    # 오른쪽 설명
    rx = side_cx + side_w / 2 + 1.2
    rw = PAGE_L + PAGE_W - rx
    y = top
    cpl = max(10, int(rw / 0.33))  # 9.5pt 한글 기준 줄당 대략 글자 수
    for num, ttl, lines in items:
        add_oval_badge(slide, rx, y, 0.48, num, fill=BLUE, font_size=8.5)
        add_textbox(slide, rx + 0.66, y + 0.02, rw - 0.66, 0.55, ttl, size=11, bold=True, color=BLUE_DARK)
        y += 0.65
        for line in lines:
            n = max(1, -(-len(line) // cpl))
            add_textbox(slide, rx, y, rw, 0.45 * n, line, size=9.5, color=DESC_TEXT, line_spacing=1.1)
            y += 0.45 * n
        y += 0.35
    return slide


# =========================================================
# 화면 명세 슬라이드 (왼쪽 캡처 + 번호 박스, 오른쪽 번호 설명 · 넘치면 캡처 아래로 이어 씀)
# =========================================================
def add_paras(slide, l, t, w, h, lines, size=9, color=DESC_TEXT, line_spacing=1.15):
    """여러 줄 설명. lines 는 str 또는 list[str] (줄마다 문단)."""
    if isinstance(lines, str):
        lines = [lines]
    tb = slide.shapes.add_textbox(Cm(l), Cm(t), Cm(w), Cm(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = line_spacing
        r = p.add_run()
        r.text = ln
        set_font(r, size=size, color=color)
    return tb


def estimate_text_h(lines, width_cm, size=9, line_spacing=1.15):
    """한글 위주 텍스트 높이 추정(cm). 실제 렌더로 한 번 더 확인할 것."""
    if isinstance(lines, str):
        lines = [lines]
    per = max(8, int(width_cm / (size * 0.0353 * 0.82)))
    n = sum(max(1, -(-len(s) // per)) for s in lines)
    return n * size * 0.0353 * line_spacing * 1.18 + 0.05


def build_screen_slide(prs, sb_id, title, status_text, summary, img_path, crop_w, crop_h, boxes_px_rel, items,
                       img_w=16.0, cols=1, below=False, sum_h=1.5, size=9):
    """
    화면 영역 하나를 설명하는 슬라이드. 표(컬럼) 화면은 build_column_slide, 일반 화면 영역은 이 함수를 쓴다.
    img_path / crop_w / crop_h / boxes_px_rel: build_column_slide 와 같은 규칙(크롭 이미지 기준 픽셀 박스 {번호: (x, y, w, h)}).
    items: [(번호, 제목, 설명 str|list[str])] — 설명 안 화면 문구는 「」로 그대로 인용.
    cols: 오른쪽 설명 단 수(1~2). below=True 면 오른쪽이 차면 캡처 아래 2단으로 이어서 채운다(항목이 많을 때).
    번호 박스끼리 왼쪽 위 모서리가 겹치면 배지가 가려지니, 박스 대상을 안쪽 요소로 바꿔 캡처한다.
    """
    slide = new_slide(prs)
    add_header(slide, sb_id, title, status_text)
    top = 2.84
    if summary:
        add_summary(slide, summary, height=sum_h, size=9.5)
        top = 2.84 + sum_h + 0.45
    w = img_w
    if w * crop_h / crop_w > 18.6 - top:
        w = (18.6 - top) * crop_w / crop_h
    ih = w * crop_h / crop_w
    slide.shapes.add_picture(img_path, Cm(PAGE_L), Cm(top), Cm(w), Cm(ih))
    add_rect(slide, PAGE_L, top, w, ih, fill=None, line_color=BORDER_GRAY, line_w_pt=0.75)
    s = w / crop_w
    badges = []
    for num, (x, y, bw, bh) in boxes_px_rel.items():
        add_rect(slide, PAGE_L + x * s, top + y * s, bw * s, bh * s, fill=None, line_color=BLUE, line_w_pt=1.5)
        badges.append((PAGE_L + x * s - 0.24, top + y * s - 0.24, num))
    for bl, bt, num in badges:          # 박스를 다 그린 뒤 배지 (가림 방지)
        add_oval_badge(slide, bl, bt, 0.48, num, font_size=8.5)
    tx = PAGE_L + w + 0.9
    gap = 0.6
    cw = (PAGE_L + PAGE_W - tx - gap * (cols - 1)) / cols
    regions = [[tx + c * (cw + gap), top, cw] for c in range(cols)]
    if below:
        bw2 = (w - gap) / 2
        regions += [[PAGE_L, top + ih + 0.5, bw2], [PAGE_L + bw2 + gap, top + ih + 0.5, bw2]]

    def block(x, y, rw, num, ttl, desc):
        add_oval_badge(slide, x, y, 0.48, num, font_size=8.5)
        add_textbox(slide, x + 0.66, y, rw - 0.66, 0.5, ttl, size=10.5, bold=True, color=BLUE_DARK)
        h = estimate_text_h(desc, rw, size)
        add_paras(slide, x, y + 0.58, rw, h, desc, size=size)
        return y + 0.58 + h + 0.35

    if cols > 1 and not below:          # 단 높이 균형
        hs = [0.93 + estimate_text_h(d, cw, size) for _, _, d in items]
        target, ri, acc = sum(hs) / cols, 0, 0
        for i, (num, ttl, desc) in enumerate(items):
            if ri < cols - 1 and acc > 0 and acc + hs[i] / 2 > target:
                ri, acc = ri + 1, 0
            x, y, rw = regions[ri]
            regions[ri][1] = block(x, y, rw, num, ttl, desc)
            acc += hs[i]
    else:
        ri = 0
        for num, ttl, desc in items:
            while ri < len(regions) - 1 and regions[ri][1] + 0.58 + estimate_text_h(desc, regions[ri][2], size) > 18.7:
                ri += 1
            x, y, rw = regions[ri]
            regions[ri][1] = block(x, y, rw, num, ttl, desc)
    return slide


# =========================================================
# 화면 단위 SB (고정 배치) — 2026-11 개발팀 요청 형식
#   · 모든 장 같은 위치: 왼쪽 「Screen」 영역(캡처) · 오른쪽 「Description」 영역(설명)
#   · 도식(흐름도 · 표) 없이 텍스트로. 다른 장 참조 없이 그 화면에 풀어 씀
#   · 한 화면 = 한 SB. 전체 캡처를 맨 위부터 스크롤하듯 이어 자르고, 장마다 보이는 요소를 위에서 아래로 설명
#   · 캡처에 없는 상태(재고 부족 · 0개 · 팝업 등)는 그 항목 바로 뒤에, 잘린 화면 아래 같은 배율로
#   · 번호는 「영역 번호-순서」(예: 4-12)를 넘긴 순서대로 새로 매김 → 항목은 화면에 보이는 순서로 넘길 것
# =========================================================
import re as _re

SCR_IMG_W = 17.6            # Screen 영역 폭(cm)
SCR_TOP, SCR_BOT = 2.84, 18.6
SCR_GAP = 0.45
SCR_BAR_H = 0.62
SCR_PAD = 0.45
SCR_CM_PER_PX = 0.0265      # 캡처 1px ≈ 화면 1px 보다 크게 키우지 않음
SCR_FONT = 9.0              # Description 글자 크기(모든 장 같음)
SCR_SCROLL_PAD = 30         # 화면 캡처 아래 여유(px)

_SCR_FONT_FILE = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'malgun.ttf')
_SCR_MF = None
_SCR_CW = {}


def _scr_cw(ch):
    """글자 폭(em). 맑은 고딕 실제 폭, 폰트가 없으면 한글 0.92 · 그 외 0.5 근사"""
    global _SCR_MF
    if ch in _SCR_CW:
        return _SCR_CW[ch]
    if _SCR_MF is None:
        try:
            from PIL import ImageFont
            _SCR_MF = ImageFont.truetype(_SCR_FONT_FILE, 100)
        except Exception:
            _SCR_MF = False
    _SCR_CW[ch] = (_SCR_MF.getlength(ch) / 100) if _SCR_MF else (0.92 if ord(ch) >= 0x1100 else 0.5)
    return _SCR_CW[ch]


def wrap_lines_ko(seg, width_cm, size):
    """PowerPoint와 같은 단어 단위 줄바꿈(글자에 ko-KR 지정 시). 한 단어가 한 줄보다 길 때만 글자 단위"""
    em = size * 0.0353
    lim = width_cm - 0.05
    out, buf, cur = [], '', 0.0
    for word in _re.split(r'(?<= )', seg):
        ww = sum(_scr_cw(c) for c in word) * em
        wt = sum(_scr_cw(c) for c in word.rstrip(' ')) * em
        if buf and cur + wt > lim:
            out.append(buf); buf, cur = '', 0.0
        if not buf and wt > lim:
            for c in word:
                w = _scr_cw(c) * em
                if cur + w > lim and buf:
                    out.append(buf); buf, cur = '', 0.0
                buf += c; cur += w
            continue
        buf += word; cur += ww
    out.append(buf)
    return out


def no_orphan(line, width_cm, size):
    """마지막 줄에 서너 글자(「음.」 · 「하나만.」 등)만 떨어지면 앞쪽 띄어쓰기에서 미리 줄을 바꿈"""
    segs = []
    for seg in str(line).split('\n'):
        ls = wrap_lines_ko(seg, width_cm, size)
        if len(ls) > 1 and len(ls[-1].strip()) <= 4:
            cut = len(seg) - len(ls[-1])
            for i in range(cut - 1, max(0, cut - 25), -1):
                if seg[i] == ' ' and len(seg) - i - 1 >= 8:
                    seg = seg[:i] + '\n' + seg[i + 1:]
                    break
        segs.append(seg)
    return '\n'.join(segs)


def text_h_ko(lines, width_cm, size=SCR_FONT, line_spacing=1.15):
    """줄 수를 실제 글자 폭 · 단어 단위로 세어 높이(cm) 계산"""
    if isinstance(lines, str):
        lines = [lines]
    n = sum(len(wrap_lines_ko(seg, width_cm, size)) for ln in lines for seg in no_orphan(ln, width_cm, size).split('\n'))
    return n * size * 0.0353 * 1.2 * line_spacing + 0.05


def split_sentences(lines):
    """한 줄에 한 문장: 「」 · 괄호 안 마침표는 나누지 않음"""
    out = []
    for x in ([lines] if isinstance(lines, str) else lines):
        buf, depth = '', 0
        for i, c in enumerate(x):
            buf += c
            if c in '「(':
                depth += 1
            elif c in '」)' and depth:
                depth -= 1
            elif c == '.' and depth == 0 and (i + 1 == len(x) or x[i + 1] == ' ') and not (i > 0 and x[i - 1].isdigit() and i + 1 < len(x) and x[i + 1].isdigit()):
                out.append(buf.strip()); buf = ''
        if buf.strip():
            out.append(buf.strip())
    return out


def _scr_paras(slide, l, t, w, h, lines, size=SCR_FONT, color=DESC_TEXT):
    return add_paras(slide, l, t, w, h, [no_orphan(x, w, size) for x in lines], size=size, color=color)


SCR_BUL_IND = 0.25   # 설명 불릿 내어쓰기(cm)
SCR_SUB_IND = 0.35   # 2단계: 1단계 글자 위치에서 더 들어감
# 단계별 (글자 시작, 불릿, 불릿~글자 간격). 2단계 대시는 1단계 글자 시작선 근처에
SCR_LV = {1: (SCR_BUL_IND, '•', SCR_BUL_IND), 2: (SCR_BUL_IND + SCR_SUB_IND, '–', 0.32)}


def scr_levels(lines):
    """설명 줄 → [(단계, 문장)]. 「- 」로 시작하는 줄은 2단계(바로 위 1단계 문장을 풀어 쓴 문장)"""
    out = []
    for x in lines:
        if isinstance(x, tuple):
            out.append(x)
        elif x.startswith('- '):
            out.append((2 if out else 1, x[2:]))
        else:
            out.append((1, x))
    return out


def scr_desc_h(lines, w, size=SCR_FONT, always=False):
    """항목 설명 높이. 문장이 둘 이상이면 단계별 불릿 + 내어쓰기 폭 기준"""
    lines = scr_levels(lines)
    if not lines:
        return 0
    if len(lines) == 1 and not always:
        return text_h_ko([lines[0][1]], w, size)
    return sum(text_h_ko([x], w - SCR_LV[lv][0], size) - 0.05 for lv, x in lines) + 0.05


def scr_desc(slide, l, t, w, h, lines, size=SCR_FONT, color=DESC_TEXT, always=False):
    """항목 설명: 한 문장이면 그대로, 둘 이상이면 문장마다 앞에 불릿(1단계 •, 2단계 – 더 들여씀). 넘어가는 줄은 글자 위치에 맞춤"""
    lines = scr_levels(lines)
    if not lines or (len(lines) == 1 and not always):   # always=True: 한 줄도 불릿(요약 박스)
        return _scr_paras(slide, l, t, w, h, [x for _, x in lines], size=size, color=color)
    tb = slide.shapes.add_textbox(Cm(l), Cm(t), Cm(w), Cm(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, (lv, ln) in enumerate(lines):
        ind, ch, hang = SCR_LV[lv]
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = 1.15
        _set_bullet(p, char=ch, indent_cm=hang)
        p._p.get_or_add_pPr().set('marL', str(Cm(ind)))
        for j, seg in enumerate(no_orphan(ln, w - ind, size).split('\n')):
            if j:
                p.add_line_break()   # 끝 글자 줄바꿈 방지 줄은 같은 문단(불릿 없이)
            r = p.add_run()
            r.text = seg
            set_font(r, size=size, color=color)
    return tb


def scr_badge(slide, l, t, label, fill=BLUE):
    """번호 배지. 두 글자까지 원, 「4-12」처럼 길면 알약 모양. 반환: 폭(cm)"""
    label = str(label)
    h = 0.48
    w = h if len(label) <= 2 else 0.18 + 0.165 * len(label)
    sh = slide.shapes.add_shape(MSO_SHAPE.OVAL if len(label) <= 2 else MSO_SHAPE.ROUNDED_RECTANGLE, Cm(l), Cm(t), Cm(w), Cm(h))
    if len(label) > 2:
        sh.adjustments[0] = 0.5
    sh.fill.solid(); sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = fill; sh.line.width = Pt(1.0)
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = label
    set_font(r, size=8.5 if len(label) <= 3 else 8, bold=True, color=WHITE)
    return w


def _badge_w(label):
    return 0.48 if len(str(label)) <= 2 else 0.18 + 0.165 * len(str(label))


def _place_badge(slide, label, bl, bt, bw, bh, mode, frame_l, frame_t):
    """박스(bl, bt, bw, bh) 기준 번호 위치. 글자 · 버튼을 덮지 않게 박스 바깥 빈 쪽.
    mode: left(기본: 박스 왼쪽, 키 큰 박스는 위 끝에 맞춤) · above · below · right · tr(오른쪽 위 모서리) · midleft · corner"""
    w = _badge_w(label)
    if mode in (None, 'auto', 'left'):
        if bl - frame_l >= w + 0.1:
            px, py = bl - w - 0.06, (bt + 0.02) if bh > 1.0 else (bt + bh / 2 - 0.24)
        elif bt - frame_t >= 0.55:
            px, py = bl, bt - 0.52
        else:
            px, py = bl - 0.24, bt - 0.24
    elif mode == 'above':
        px, py = bl, bt - 0.52
    elif mode == 'below':
        px, py = bl + bw / 2 - w / 2, bt + bh + 0.04
    elif mode == 'right':
        px, py = bl + bw + 0.06, bt + bh / 2 - 0.24
    elif mode == 'tr':
        px, py = bl + bw - w / 2, bt - 0.24
    elif mode == 'midleft':
        px, py = bl - w / 2, bt + bh / 2 - 0.24
    else:
        px, py = bl - 0.24, bt - 0.24
    scr_badge(slide, max(PAGE_L + 0.05, px), max(frame_t - 0.3, py), label)


def _scr_frame(slide, l, w, label):
    add_rect(slide, l, SCR_TOP, w, SCR_BOT - SCR_TOP, fill=None, line_color=BORDER_GRAY, line_w_pt=0.75)
    add_rect(slide, l, SCR_TOP, w, SCR_BAR_H, fill=BADGE_BG, line_color=BORDER_GRAY, line_w_pt=0.75)
    add_textbox(slide, l + 0.3, SCR_TOP + 0.1, w - 0.6, SCR_BAR_H - 0.15, label, size=10, bold=True, color=BLUE_DARK)


def _quiet_rows(img):
    """가로 한 줄에서 배경과 다른 점이 몇 개뿐(카드 세로 테두리 정도)이면 빈 줄"""
    try:
        import numpy as np
        g = np.asarray(img.convert('L'), dtype=np.int16)
        med = np.median(g, axis=1, keepdims=True)
        return list((np.abs(g - med) > 10).sum(axis=1) <= max(8, g.shape[1] // 150))
    except ImportError:
        g = img.convert('L'); W, H = g.size; px = g.load(); out = []
        for y in range(H):
            row = sorted(px[x, y] for x in range(0, W, 2)); med = row[len(row) // 2]
            out.append(sum(1 for v in row if abs(v - med) > 10) <= max(4, W // 300))
        return out


def _quiet_cut(q, y, lo, hi, prefer_up=False):
    """y 근처에서 앞뒤 5줄이 비어 있는 줄로 자르는 위치를 옮김(글자 · 카드 중간을 자르지 않게)"""
    lo, hi = max(0, int(lo)), min(len(q) - 1, int(hi))
    cand = [r for r in range(lo, hi + 1) if all(q[k] for k in range(max(0, r - 2), min(len(q), r + 3)))]
    if not cand:
        return y
    if prefer_up:
        up = [r for r in cand if r <= y]
        return max(up) if up else y
    return min(cand, key=lambda r: abs(r - y))


def _shot_size(sp):
    with Image.open(sp['file']) as im:
        pw, ph = im.size
    d = sp.get('dpr', 1)            # 2배 해상도 캡처(현행 사이트 등)는 화면 px로 환산해 같은 배율
    return pw / d, ph / d


def _shot_rows(shots, sc, IW, IH):
    """한 열로 쌓되, 화면 칸 폭의 절반보다 좁은 캡처가 이어지면(같은 요소의 상태) 같은 배율로 옆에 붙임"""
    rows = []
    for sp in shots:
        pw, ph = _shot_size(sp)
        w = min(IW, pw * sc, IH * pw / ph)
        h = w * ph / pw
        narrow = w < IW / 2
        if rows and narrow and rows[-1]['narrow'] and rows[-1]['w'] + 0.5 + w <= IW:
            rows[-1]['items'].append((sp, w, h)); rows[-1]['w'] += 0.5 + w; rows[-1]['h'] = max(rows[-1]['h'], h)
        else:
            rows.append(dict(items=[(sp, w, h)], w=w, h=h, narrow=narrow))
    return rows


def _shots_h(shots, sc, IW, IH):
    rows = _shot_rows(shots, sc, IW, IH)
    return sum(r['h'] for r in rows) + 0.5 * max(0, len(rows) - 1)


def build_screen_sb(prs, sb_id, title, status_text, areas, full=None, summary=None, ref_px=None, work_dir=None):
    """
    화면 하나를 고정 배치(Screen · Description)로 여러 장에 이어 그린다.

    full: 전체 화면 캡처 {'file': 경로, 'dpr': 1} (선택). 화면 맨 위부터 장마다 이어서 잘라 보여 줌.
          헤더처럼 한 줄짜리 화면도 그 캡처를 full로 넘기면 됨.
    summary: 화면 공통 요약 [문장, ...] — 첫 장 Description 맨 위 요약 박스.
    areas: [{'title': '재주문 체크' | None, 'summary': [...], 'items': [item, ...]}, ...]
      item = {
        'title': '수량', 'desc': ['문장.', ...],       # 화면 문구는 「」로 그대로 인용
        'box': (x, y, w, h),                          # full 캡처 px 기준 박스(그 요소가 전체 캡처에 보일 때)
        'badge': 'left' | 'above' | 'below' | 'right' | 'tr' | 'midleft' | 'corner',
        'shots': [{'file': 경로, 'boxes': [(x, y, w, h), ...], 'badge': 'left'|'above'|'right', 'anchor': 0, 'dpr': 1}],
                                                      # 캡처에 없는 상태 화면. boxes가 없으면 캡처 전체가 그 상태(번호는 모서리)
        'new_page': True,                             # 다른 덩어리(구매 패널 등)가 시작하는 항목: 새 장에서
        'key': 'price_hide',                          # 다른 항목 설명에서 [[price_hide]]로 번호를 가리킬 때
      }
      · box도 shots도 없는 항목은 번호 없이 그 영역 요약 박스에 「제목: 설명」으로 들어감(가리킬 곳이 없으므로)
      · 번호: 제목 있는 영역이 있으면 「영역 순번-항목 순번」, 없으면 1부터. 넘긴 순서 = 번호 순서 = 화면에 보이는 순서
      · 한 영역 · 한 덩어리는 끝까지 이어서 넘길 것(중간에 다른 덩어리를 끼우지 않음)
    ref_px: 상태 캡처 배율 기준 폭(px). 기본 = full 캡처 폭(없으면 1240)
    work_dir: 잘라 낸 캡처 저장 폴더(기본: full 캡처 옆 _crops)
    반환: {key 또는 제목: 번호}
    """
    IL, IT = PAGE_L + SCR_PAD, SCR_TOP + SCR_BAR_H + SCR_PAD
    IW, IH = SCR_IMG_W - 2 * SCR_PAD, SCR_BOT - SCR_TOP - SCR_BAR_H - 2 * SCR_PAD
    D_L = PAGE_L + SCR_IMG_W + SCR_GAP
    D_W = PAGE_L + PAGE_W - D_L
    DX, DW = D_L + SCR_PAD, D_W - 2 * SCR_PAD
    DY0, DYMAX = SCR_TOP + SCR_BAR_H + 0.35, SCR_BOT - 0.3
    size = SCR_FONT
    src = q = None
    W = H = 0
    if full:
        src = Image.open(full['file'])
        W, H = src.size
        d = full.get('dpr', 1)
        if d != 1:
            src = src.resize((int(W / d), int(H / d)))
            W, H = src.size
        q = _quiet_rows(src)
        work_dir = work_dir or os.path.join(os.path.dirname(os.path.abspath(full['file'])), '_crops')
        os.makedirs(work_dir, exist_ok=True)
    ref = ref_px or (W if full else 1240)
    sc = min(IW / ref, SCR_CM_PER_PX)

    # 1) 번호 매기기 · 가리킬 곳 없는 항목은 영역 요약으로
    titled = any(a.get('title') for a in areas)
    labels, secs, an = {}, [], 0
    for a in areas:
        if a.get('title'):
            an += 1
        part = f"{an} {a['title']}" if a.get('title') else None
        s_ = list(a.get('summary') or [])
        its, k = [], 0
        for it in a.get('items', []):
            if not it.get('box') and not it.get('shots'):
                s_.append(f"{it['title']}: " + '. '.join(x.rstrip('.') for x in split_sentences(it.get('desc', []))))
                continue
            k += 1
            lab = f'{an}-{k}' if titled else str(len(labels) + 1)
            if it.get('key'):
                labels[it['key']] = lab
            else:
                labels.setdefault(it['title'], lab)   # 같은 제목이 여러 번이면 첫 번째. 정확히 가리키려면 key
            its.append(dict(it, label=lab))
        secs.append(dict(part=part, summary=s_, items=its))

    def fill(txt):
        return _re.sub(r'\[\[([^\]]+)\]\]', lambda m: labels.get(m.group(1), m.group(0)), txt)

    # 2) 블록: H(영역 제목 + 요약) · I(항목)
    blocks = []
    if summary:
        blocks.append(dict(kind='H', part=None, summary=list(summary)))
    for s_ in secs:
        if s_['part'] or s_['summary']:
            blocks.append(dict(kind='H', part=s_['part'], summary=s_['summary']))
        for it in s_['items']:
            blocks.append(dict(kind='I', it=it, desc=[('- ' if x.startswith('- ') and not y.startswith('- ') else '') + fill(y)
                                                      for x in ([it['desc']] if isinstance(it.get('desc'), str) else it.get('desc', []))
                                                      for z in split_sentences(x) for y in z.split('\n') if y.strip()]))

    def sum_h(lines):
        return scr_desc_h(lines, DW - 0.5, size, always=True) + 0.5   # 요약 박스도 불릿 + 내어쓰기

    def bh(b):
        if b['kind'] == 'H':
            return (0.62 if b['part'] else 0) + (sum_h(b['summary']) + 0.35 if b['summary'] else (0.25 if b['part'] else 0))
        return 0.58 + scr_desc_h(b['desc'], DW, size) + 0.3

    def win_need(page, first):
        bx = [b['it']['box'] for b in page if b['kind'] == 'I' and b['it'].get('box')]
        h = 0.0
        if bx and full:
            y0 = 0 if first else max(0, min(r[1] for r in bx) - 60)
            y1 = max(r[1] + r[3] for r in bx) + SCR_SCROLL_PAD
            h += (y1 - y0) * sc + 0.6
        shots = [sp for b in page if b['kind'] == 'I' for sp in b['it'].get('shots', [])]
        if shots:
            h += _shots_h(shots, sc, IW, IH)
        return h

    # 3) 장 나누기: 설명 높이 · 화면 높이 · 새 덩어리 · 상태 캡처 뒤에 다시 화면 박스가 오면 새 장(번호가 거꾸로 보이지 않게)
    pages, cur, used, k = [], [], 0.0, 0
    while k < len(blocks):
        b = blocks[k]
        pair = [b] + ([blocks[k + 1]] if b['kind'] == 'H' and k + 1 < len(blocks) else [])
        need = sum(bh(x) for x in pair)
        trial = cur + pair
        brk = False
        if cur:
            nb = next((x for x in pair if x['kind'] == 'I'), None)
            if cur[-1]['kind'] == 'H':   # 방금 영역 제목만 놓였으면 그 영역 첫 항목은 같은 장에(제목만 남는 장 방지)
                nb = None
            had_shot = any(x['kind'] == 'I' and x['it'].get('shots') for x in cur)
            brk = (used + need > DYMAX - DY0 or win_need(trial, not pages) > IH
                   or (nb is not None and nb['it'].get('new_page'))
                   or (nb is not None and nb['it'].get('box') and had_shot))
        if brk:
            pages.append(cur); cur, used = [], 0.0
            continue
        cur.append(b); used += bh(b); k += 1
    if cur:
        pages.append(cur)

    # 4) 그리기
    for pi, page in enumerate(pages):
        s = new_slide(prs)
        add_header(s, sb_id, title, status_text)
        _scr_frame(s, PAGE_L, SCR_IMG_W, 'Screen')
        _scr_frame(s, D_L, D_W, 'Description')
        top = IT
        bxs = [b for b in page if b['kind'] == 'I' and b['it'].get('box')]
        shots = []
        for b in page:
            if b['kind'] == 'I':
                for sp in b['it'].get('shots', []):
                    shots.append(dict(sp, label=b['it']['label']))
        if bxs and full:
            ys = [b['it']['box'][1] for b in bxs]
            room = IH - ((_shots_h(shots, sc, IW, IH) + 0.6) if shots else 0)
            win = room / sc
            y0 = 0 if pi == 0 else max(0, min(ys) - 60)
            y1 = y0 + win
            if y1 > H:
                y1, y0 = H, max(0, H - win)
            need_end = max(b['it']['box'][1] + b['it']['box'][3] for b in bxs) + SCR_SCROLL_PAD
            y1 = max(y1, min(H, need_end))
            if pi != 0:
                y0 = _quiet_cut(q, int(y0), int(y0) - 70, min(ys) - 6)
            y1 = min(y1, max(int(need_end), int(y0 + win)))
            if y1 < H:
                y1 = _quiet_cut(q, int(y1), max(int(need_end), int(y1) - 80), int(y1), prefer_up=True)
            y0, y1 = int(y0), int(min(H, y1))
            fn = os.path.join(work_dir, f"{os.path.splitext(os.path.basename(full['file']))[0]}_{y0}_{y1}.png")
            src.crop((0, y0, W, y1)).save(fn)
            w, h = W * sc, (y1 - y0) * sc
            s.shapes.add_picture(fn, Cm(IL), Cm(IT), Cm(w), Cm(h))
            add_rect(s, IL, IT, w, h, fill=None, line_color=BORDER_GRAY, line_w_pt=0.75)
            for b in bxs:
                rx, ry, rw, rh = b['it']['box']
                rh2 = min(rh, y1 - ry)
                bl, bt = IL + max(0, rx) * sc, IT + (ry - y0) * sc
                bw = min(rw, W - max(0, rx)) * sc
                add_rect(s, bl, bt, bw, rh2 * sc, fill=None, line_color=BLUE, line_w_pt=1.5)
            for b in bxs:     # 박스를 다 그린 뒤 번호(가림 방지)
                rx, ry, rw, rh = b['it']['box']
                rh2 = min(rh, y1 - ry)
                _place_badge(s, b['it']['label'], IL + max(0, rx) * sc, IT + (ry - y0) * sc,
                             min(rw, W - max(0, rx)) * sc, rh2 * sc, b['it'].get('badge'), IL, IT)
            top = IT + h + 0.6
        if shots:
            y = top
            for row in _shot_rows(shots, sc, IW, IH):
                x = IL
                for sp, w, h in row['items']:
                    s.shapes.add_picture(sp['file'], Cm(x), Cm(y), Cm(w), Cm(h))
                    add_rect(s, x, y, w, h, fill=None, line_color=BORDER_GRAY, line_w_pt=0.75)
                    k_ = w / _shot_size(sp)[0]
                    bxl = sp.get('boxes') or []
                    for bx_, by_, bw_, bh_ in bxl:
                        add_rect(s, x + bx_ * k_, y + by_ * k_, bw_ * k_, bh_ * k_, fill=None, line_color=BLUE, line_w_pt=1.5)
                    if bxl:
                        a = bxl[sp.get('anchor', 0)]
                        _place_badge(s, sp['label'], x + a[0] * k_, y + a[1] * k_, a[2] * k_, a[3] * k_, sp.get('badge'), x, y)
                    else:   # 캡처 전체가 그 상태
                        scr_badge(s, x - 0.24, y - 0.24, sp['label'])
                    x += w + 0.5
                y += row['h'] + 0.5
            if y - 0.5 > IT + IH + 0.05:
                print(f'  !! {sb_id} {pi + 1}장 Screen 넘침 {y - 0.5 - IT:.1f}/{IH:.1f}cm')
        y = DY0
        for b in page:
            if b['kind'] == 'H':
                if b['part']:
                    add_textbox(s, DX, y, DW, 0.5, b['part'], size=11, bold=True, color=BLUE_DARK)
                    add_rect(s, DX, y + 0.5, DW, 0.02, fill=BLUE, line_color=None)
                    y += 0.62
                if b['summary']:
                    shh = sum_h(b['summary'])
                    add_rect(s, DX, y, DW, shh, fill=SUMMARY_BG, line_color=SUMMARY_BORDER, line_w_pt=0.75)
                    scr_desc(s, DX + 0.25, y + 0.2, DW - 0.5, shh - 0.4, b['summary'], size=size, color=BODY_TEXT, always=True)
                    y += shh + 0.35
                elif b['part']:
                    y += 0.25
            else:
                bw = scr_badge(s, DX, y, b['it']['label'])
                add_textbox(s, DX + bw + 0.18, y, DW - bw - 0.18, 0.5, b['it']['title'], size=10.5, bold=True, color=BLUE_DARK)
                th = scr_desc_h(b['desc'], DW, size)
                if b['desc']:
                    scr_desc(s, DX, y + 0.58, DW, th, b['desc'], size=size)
                y += 0.58 + th + 0.3
        if y - 0.3 > DYMAX + 0.05:
            print(f'  !! {sb_id} {pi + 1}장 Description 넘침 {y:.1f}cm')
    return labels


def finish_presentation(prs):
    """저장 직전에 호출: 모든 글자를 한국어(ko-KR)로 지정해 PowerPoint가 단어 단위로 줄을 바꾸게 하고(지정이 없으면
    한글을 글자 단위로 끊어 「오/더」처럼 단어가 쪼개짐 · eaLnBrk 속성으로는 안 바뀜), 쉼표 · 마침표가 줄 첫머리로 가지 않게 함"""
    for sl in prs.slides:
        for p in sl.shapes._spTree.iter(qn('a:p')):
            ppr = p.find(qn('a:pPr'))
            if ppr is None:
                ppr = p.makeelement(qn('a:pPr'), {})
                p.insert(0, ppr)
            ppr.set('hangingPunct', '1')
            for r in p.iter(qn('a:r'), qn('a:fld')):
                rpr = r.find(qn('a:rPr'))
                if rpr is None:
                    rpr = r.makeelement(qn('a:rPr'), {})
                    r.insert(0, rpr)
                rpr.set('lang', 'ko-KR'); rpr.set('altLang', 'en-US')


def audit_sb(pptx_path, verbose=True):
    """
    완성된 SB 전 장 기계 검사. 반환: [(장 번호, 문제), ...]
      · Screen 번호 ↔ Description 번호 양쪽 일치(설명만 있거나 화면에만 있는 번호)
      · 화면 번호가 보이는 순서(이미지 위→아래, 이미지 안 위→아래 · 같은 줄 왼→오른)대로 오르는지
        (같은 요소의 상태 캡처로 번호가 다시 나오는 것은 허용. 같은 줄 나란한 번호는 「확인」으로만 표시)
    렌더 눈 확인(render_slides_to_png)과 함께 쓴다.
    """
    prs = Presentation(pptx_path)
    split = Cm(PAGE_L + SCR_IMG_W + SCR_GAP / 2)
    lab_re = _re.compile(r'^\d+(-\d+)?$')
    key = lambda l: tuple(int(x) for x in l.split('-'))
    out = []
    for i, sl in enumerate(prs.slides, 1):
        imgs, L, R = [], [], set()
        for sh in sl.shapes:
            if sh.shape_type == 13 and sh.left < split:
                imgs.append((sh.left / 360000, sh.top / 360000, sh.width / 360000, sh.height / 360000))
            elif sh.has_text_frame and lab_re.match(sh.text_frame.text.strip()) and sh.width < Cm(1.6):
                t = sh.text_frame.text.strip()
                (L.append((t, sh.left / 360000 + 0.24, sh.top / 360000 + 0.24)) if sh.left < split else R.add(t))
        if not imgs or not (L or R):
            continue
        Ls = {t for t, _, _ in L}
        if R - Ls:
            out.append((i, f'설명만 있고 화면 번호 없음 {sorted(R - Ls, key=key)}'))
        if Ls - R:
            out.append((i, f'화면 번호만 있고 설명 없음 {sorted(Ls - R, key=key)}'))

        def owner(x, y):
            return min(range(len(imgs)), key=lambda k: max(imgs[k][0] - x, 0, x - imgs[k][0] - imgs[k][2])
                       + max(imgs[k][1] - y, 0, y - imgs[k][1] - imgs[k][3]))
        order = sorted(range(len(imgs)), key=lambda k: (round(imgs[k][1] / 0.3), imgs[k][0]))
        rank = {k: r for r, k in enumerate(order)}
        ent = sorted(((rank[owner(x, y)], y, x, t) for t, x, y in L), key=lambda e: (e[0], e[1]))
        rows = []
        for e in ent:
            if rows and rows[-1][0][0] == e[0] and abs(e[1] - rows[-1][0][1]) <= 0.45:
                rows[-1].append(e)
            else:
                rows.append([e])
        seq = [e[3] for r in rows for e in sorted(r, key=lambda e: e[2])]
        seq = [x for k, x in enumerate(seq) if k == 0 or x != seq[k - 1]]
        seen = set()
        for a, b in zip(seq, seq[1:]):
            if key(b) < key(a) and b not in seen:
                same_row = any(a in [e[3] for e in r] and b in [e[3] for e in r] for r in rows)
                out.append((i, f"{'확인(같은 줄)' if same_row else '번호 거꾸로'} {a} 다음에 {b}"))
            seen.add(a)
    if verbose:
        for i, m in out:
            print(f'  {i}장: {m}')
        print(f'audit_sb: {len(out)}건')
    return out


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


def add_sections(prs, groups):
    """PowerPoint 구역(섹션) 추가. SB가 20장을 넘으면 화면 단위로 나눠 둘 것.
    groups: [(구역 이름, 첫 SB 번호 int, 첫 구역은 None=표지 · 버전 포함)] 순서대로.
    각 슬라이드 헤더 배지의 SB 번호('SB-07')로 구역을 판정한다. prs.save 직전에 호출.
    맨 뒤 참고 장(배지 「참고 1」 등, SB 번호 없음)은 groups 끝에 ("참고", "참고") 를 두면 그 구역으로 모인다."""
    import re as _re, uuid
    from lxml import etree
    P14 = 'http://schemas.microsoft.com/office/powerpoint/2010/main'
    sb_of = []
    for sl in prs.slides:
        m = None
        for sh in sl.shapes:
            if sh.has_text_frame and _re.fullmatch(r'SB-\d\d', sh.text_frame.text.strip()):
                m = int(sh.text_frame.text.strip()[3:]); break
            if sh.has_text_frame and sh.text_frame.text.strip().startswith('참고') and sh.top < Cm(3):
                m = '참고'; break   # 상단 배지만(표지 목차의 「참고」 글자는 제외)
        sb_of.append(m)
    starts = [g[1] for g in groups]
    buckets = [[] for _ in groups]
    gi = 0
    for sl, n in zip(prs.slides._sldIdLst, sb_of):
        if n == '참고' and '참고' in starts:
            gi = starts.index('참고')
        else:
            while gi + 1 < len(groups) and isinstance(n, int) and isinstance(starts[gi + 1], int) and n >= starts[gi + 1]:
                gi += 1
        buckets[gi].append(sl.get('id'))
    pres = prs.part._element
    ext_lst = pres.find('{http://schemas.openxmlformats.org/presentationml/2006/main}extLst')
    if ext_lst is None:
        ext_lst = etree.SubElement(pres, '{http://schemas.openxmlformats.org/presentationml/2006/main}extLst')
    for e in list(ext_lst):
        if e.get('uri') == '{521415D9-36F7-43E2-AB2F-B90AF26B5E84}':
            ext_lst.remove(e)
    ext = etree.SubElement(ext_lst, '{http://schemas.openxmlformats.org/presentationml/2006/main}ext', uri='{521415D9-36F7-43E2-AB2F-B90AF26B5E84}')
    lst = etree.SubElement(ext, '{%s}sectionLst' % P14, nsmap={'p14': P14})
    for (name, _), ids in zip(groups, buckets):
        sec = etree.SubElement(lst, '{%s}section' % P14, name=name, id='{%s}' % str(uuid.uuid4()).upper())
        il = etree.SubElement(sec, '{%s}sldIdLst' % P14)
        for i in ids:
            etree.SubElement(il, '{%s}sldId' % P14, id=i)

