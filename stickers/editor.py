"""스티커 편집기 — 워크스페이스 편집기와 동일한 도구 세트 (tkinter + PIL).

배경 지우기(마법봉) · 지우개 · 자르기 · 회전 · 되돌리기(5단계) · 초기화.
모든 픽셀 연산은 PIL(C 구현)로 수행 — 512px 기준 즉시.
"""
import platform
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageChops, ImageDraw, ImageTk

# ENA 라이트 팔레트
BG, BG_SOFT, TEXT, MUTED, BORDER = "#ffffff", "#f4f4f5", "#3f3f46", "#a1a1aa", "#e4e4e7"
PRIMARY, ACTIVE = "#52525b", "#8892b5"

MAX_EDIT_PX = 1024
MAX_UNDO = 5
VIEW_W, VIEW_H = 720, 520

UI_FONT = "Malgun Gothic" if platform.system() == "Windows" else "sans-serif"


def flood_erase(img: Image.Image, x: int, y: int, tol: int) -> bool:
    """클릭 지점과 색이 비슷한 '이어진 영역'을 투명화. 변화가 있으면 True.

    PIL floodfill(C 속도)로 영역을 찾고, 바뀐 픽셀만 알파 0 처리.
    tol: 0~90 (웹 편집기와 동일 스케일)
    """
    if img.getpixel((x, y))[3] == 0:
        return False
    rgb = img.convert("RGB")
    filled = rgb.copy()
    # thresh는 채널 절대차 합 기준 → 웹(유클리드)과 유사 체감으로 스케일
    ImageDraw.floodfill(filled, (x, y), (255, 0, 254), thresh=int(tol * 7.65))
    diff = ImageChops.difference(rgb, filled).convert("L")
    mask = diff.point(lambda v: 255 if v > 0 else 0)
    if mask.getbbox() is None:
        return False
    alpha = img.getchannel("A")
    zero = Image.new("L", img.size, 0)
    img.putalpha(Image.composite(zero, alpha, mask))
    return True


def make_checker(w: int, h: int, cell: int = 14) -> Image.Image:
    """투명 표시용 체커보드."""
    bg = Image.new("RGB", (w, h), (240, 240, 243))
    d = ImageDraw.Draw(bg)
    for yy in range(0, h, cell):
        for xx in range(0, w, cell):
            if (xx // cell + yy // cell) % 2 == 0:
                d.rectangle((xx, yy, xx + cell - 1, yy + cell - 1), fill=(214, 214, 220))
    return bg


class StickerEditor(tk.Toplevel):
    def __init__(self, parent, image_path: str, on_save=None, place=None):
        """place = (자리 돌려주는 함수, 자리 저장하는 함수).

        창을 열 때마다 화면 한가운데로 돌아가면, 옮겨 놓고 쓰던 사람이
        매번 다시 옮겨야 한다. 마지막 자리를 기억했다가 거기서 연다.
        """
        super().__init__(parent)
        self.title("스티커 편집")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()

        self.path = image_path
        self.on_save = on_save
        self._place = place

        src = Image.open(image_path).convert("RGBA")
        if max(src.size) > MAX_EDIT_PX:
            k = MAX_EDIT_PX / max(src.size)
            src = src.resize((max(1, round(src.width * k)), max(1, round(src.height * k))), Image.LANCZOS)
        self.img = src
        self.initial = src.copy()          # 초기화용
        self.undo_stack: list[Image.Image] = []

        self.tool = "wand"
        self.tolerance = 28
        self.brush = 28
        self.angle = 0
        self.rot_base: Image.Image | None = None
        self.rot_pushed = False
        self._crop_start = None
        self._crop_rect_id = None
        self._last_pt = None

        self._build_ui()
        self._render()
        self._restore_pos()
        # 어떻게 닫든(저장·취소·X) 자리를 남긴다
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._on_destroy)

    def _restore_pos(self):
        if not self._place:
            return
        try:
            self.update_idletasks()
            got = self._place[0]()
            if got:
                self.geometry(f"+{int(got[0])}+{int(got[1])}")
        except Exception:
            pass

    def _save_pos(self):
        if not self._place:
            return
        try:
            self._place[1](self)
        except Exception:
            pass

    def _on_destroy(self, e):
        if e.widget is self:
            self._save_pos()

    def _close(self):
        self._save_pos()
        self.destroy()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        f10 = (UI_FONT, 10)
        f10b = (UI_FONT, 10, "bold")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill=tk.X, padx=12, pady=(12, 6))
        self._tool_btns = {}
        for key, label in [("wand", "배경 지우기"), ("eraser", "지우개"), ("crop", "자르기"), ("rotate", "회전")]:
            b = tk.Button(bar, text=label, font=f10, bd=0, padx=10, pady=4, cursor="hand2",
                          command=lambda k=key: self._select_tool(k))
            b.pack(side=tk.LEFT, padx=(0, 4))
            self._tool_btns[key] = b

        self.undo_btn = tk.Button(bar, text="되돌리기", font=f10, bd=0, padx=10, pady=4,
                                  bg=BG_SOFT, fg=TEXT, cursor="hand2", command=self._undo)
        self.undo_btn.pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(bar, text="처음으로", font=f10, bd=0, padx=10, pady=4,
                  bg=BG_SOFT, fg=TEXT, cursor="hand2", command=self._reset).pack(side=tk.RIGHT)

        # 파라미터 줄 (도구별 슬라이더)
        prm = tk.Frame(self, bg=BG)
        prm.pack(fill=tk.X, padx=12)
        self.param_label = tk.Label(prm, text="", font=(UI_FONT, 9), bg=BG, fg=MUTED, width=16, anchor="w")
        self.param_label.pack(side=tk.LEFT)
        self.scale = tk.Scale(prm, from_=2, to=90, orient=tk.HORIZONTAL, showvalue=False,
                              bg=BG, troughcolor=BG_SOFT, highlightthickness=0, bd=0,
                              command=self._on_scale)
        self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
        self.rot_l = tk.Button(prm, text="⟲90", font=(UI_FONT, 9), bd=0, padx=8, bg=BG_SOFT, fg=TEXT,
                               cursor="hand2", command=lambda: self._rotate_by(-90))
        self.rot_r = tk.Button(prm, text="90⟳", font=(UI_FONT, 9), bd=0, padx=8, bg=BG_SOFT, fg=TEXT,
                               cursor="hand2", command=lambda: self._rotate_by(90))

        # 캔버스
        self.canvas = tk.Canvas(self, width=VIEW_W, height=VIEW_H, bg=BG_SOFT,
                                bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.canvas.pack(padx=12, pady=8)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        tk.Label(self, text="배경 지우기: 지울 색 클릭 · 지우개: 드래그 · 자르기: 영역 드래그 · 회전: 슬라이더/90°",
                 font=(UI_FONT, 9), bg=BG, fg=MUTED).pack(padx=12, anchor="w")

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill=tk.X, padx=12, pady=(6, 12))
        tk.Button(foot, text="저장", font=f10b, bd=0, padx=18, pady=6, bg=ACTIVE, fg="#ffffff",
                  cursor="hand2", command=self._save).pack(side=tk.RIGHT)
        tk.Button(foot, text="취소", font=f10, bd=0, padx=14, pady=6, bg=BG_SOFT, fg=MUTED,
                  cursor="hand2", command=self.destroy).pack(side=tk.RIGHT, padx=(0, 6))

        self._select_tool("wand")

    def _select_tool(self, key):
        # 회전 진입/이탈 처리
        if key == "rotate":
            self.rot_base = self.img.copy()
            self.rot_pushed = False
            self.angle = 0
        else:
            self.rot_base = None

        self.tool = key
        for k, b in self._tool_btns.items():
            b.configure(bg=ACTIVE if k == key else BG_SOFT, fg="#ffffff" if k == key else TEXT)

        if key == "wand":
            self.param_label.configure(text=f"허용치 {self.tolerance}")
            self.scale.configure(from_=2, to=90)
            self.scale.set(self.tolerance)
            self._show_rot_buttons(False)
            self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
        elif key == "eraser":
            self.param_label.configure(text=f"브러시 {self.brush}px")
            self.scale.configure(from_=6, to=90)
            self.scale.set(self.brush)
            self._show_rot_buttons(False)
            self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
        elif key == "crop":
            self.param_label.configure(text="드래그로 남길 영역")
            self.scale.pack_forget()
            self._show_rot_buttons(False)
        else:  # rotate
            self.param_label.configure(text="각도 0°")
            self.scale.configure(from_=0, to=360)
            self.scale.set(0)
            self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
            self._show_rot_buttons(True)

    def _show_rot_buttons(self, show: bool):
        if show:
            self.rot_l.pack(side=tk.LEFT, padx=(0, 2))
            self.rot_r.pack(side=tk.LEFT)
        else:
            self.rot_l.pack_forget()
            self.rot_r.pack_forget()

    # ── 렌더 ─────────────────────────────────────────────────────────────────
    def _view_scale(self) -> float:
        return min(VIEW_W / self.img.width, VIEW_H / self.img.height, 1.0)

    def _render(self):
        k = self._view_scale()
        vw = max(1, round(self.img.width * k))
        vh = max(1, round(self.img.height * k))
        view = self.img.resize((vw, vh), Image.LANCZOS) if k < 1 else self.img
        board = make_checker(vw, vh)
        board.paste(view, (0, 0), view)
        self._photo = ImageTk.PhotoImage(board)
        self.canvas.delete("all")
        self._img_x = (VIEW_W - vw) // 2
        self._img_y = (VIEW_H - vh) // 2
        self.canvas.create_image(self._img_x, self._img_y, anchor=tk.NW, image=self._photo)
        self.undo_btn.configure(state=tk.NORMAL if self.undo_stack else tk.DISABLED)

    def _to_img(self, ex, ey):
        """캔버스 좌표 → 이미지 픽셀 좌표 (범위 밖이면 None)."""
        k = self._view_scale()
        x = (ex - self._img_x) / k
        y = (ey - self._img_y) / k
        if x < 0 or y < 0 or x >= self.img.width or y >= self.img.height:
            return None
        return int(x), int(y)

    # ── undo/reset ───────────────────────────────────────────────────────────
    def _push_undo(self):
        self.undo_stack.append(self.img.copy())
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)

    def _undo(self):
        if not self.undo_stack:
            return
        self.img = self.undo_stack.pop()
        if self.tool == "rotate":
            self.rot_base = self.img.copy()
            self.rot_pushed = False
            self.angle = 0
            self.scale.set(0)
            self.param_label.configure(text="각도 0°")
        self._render()

    def _reset(self):
        self.img = self.initial.copy()
        self.undo_stack = []
        if self.tool == "rotate":
            self._select_tool("rotate")
        self._render()

    # ── 도구 동작 ────────────────────────────────────────────────────────────
    def _on_scale(self, val):
        v = int(float(val))
        if self.tool == "wand":
            self.tolerance = v
            self.param_label.configure(text=f"허용치 {v}")
        elif self.tool == "eraser":
            self.brush = v
            self.param_label.configure(text=f"브러시 {v}px")
        elif self.tool == "rotate":
            self._apply_rotation(v)

    def _apply_rotation(self, deg):
        if self.rot_base is None:
            return
        if not self.rot_pushed:
            self._push_undo()
            self.rot_pushed = True
        self.angle = deg
        self.param_label.configure(text=f"각도 {deg}°")
        # PIL rotate는 반시계 → 시계 방향 슬라이더에 맞춰 부호 반전
        self.img = self.rot_base.rotate(-deg, expand=True, resample=Image.BICUBIC)
        self._render()

    def _rotate_by(self, delta):
        deg = (self.angle + delta) % 360
        self.scale.set(deg)
        self._apply_rotation(deg)

    def _on_press(self, e):
        p = self._to_img(e.x, e.y)
        if p is None:
            return
        if self.tool == "wand":
            self._push_undo()
            if not flood_erase(self.img, p[0], p[1], self.tolerance):
                self.undo_stack.pop()   # 변화 없음 → 스냅샷 회수
            self._render()
        elif self.tool == "eraser":
            self._push_undo()
            self._last_pt = p
            self._stamp(p)
        elif self.tool == "crop":
            self._crop_start = (e.x, e.y)
            if self._crop_rect_id:
                self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id = self.canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline=ACTIVE, dash=(4, 3), width=2)

    def _on_drag(self, e):
        if self.tool == "eraser" and self._last_pt is not None:
            p = self._to_img(e.x, e.y)
            if p is None:
                return
            # 점 사이 보간 — 빠른 드래그에도 끊김 없이
            x0, y0 = self._last_pt
            dist = max(abs(p[0] - x0), abs(p[1] - y0))
            steps = max(1, dist // max(2, self.brush // 4))
            for i in range(1, steps + 1):
                self._stamp((round(x0 + (p[0] - x0) * i / steps), round(y0 + (p[1] - y0) * i / steps)), render=False)
            self._last_pt = p
            self._render()
        elif self.tool == "crop" and self._crop_start:
            self.canvas.coords(self._crop_rect_id, self._crop_start[0], self._crop_start[1], e.x, e.y)

    def _on_release(self, e):
        if self.tool == "eraser":
            self._last_pt = None
        elif self.tool == "crop" and self._crop_start:
            x0, y0 = self._crop_start
            self._crop_start = None
            if self._crop_rect_id:
                self.canvas.delete(self._crop_rect_id)
                self._crop_rect_id = None
            a = self._to_img(min(x0, e.x), min(y0, e.y))
            b = self._to_img(max(x0, e.x), max(y0, e.y))
            if a is None or b is None:
                return
            w, h = b[0] - a[0], b[1] - a[1]
            if w >= 8 and h >= 8:
                self._push_undo()
                self.img = self.img.crop((a[0], a[1], b[0], b[1]))
                self._render()

    def _stamp(self, p, render=True):
        """지우개 — 반경 내 픽셀 알파 0 (ImageDraw는 픽셀을 직접 대체)."""
        r = self.brush // 2
        d = ImageDraw.Draw(self.img)
        d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=(0, 0, 0, 0))
        if render:
            self._render()

    # ── 저장 ─────────────────────────────────────────────────────────────────
    def _save(self):
        try:
            self.img.save(self.path)   # 같은 파일에 덮어씀 (PNG, 알파 보존)
        except Exception:
            return
        if self.on_save:
            self.on_save()
        self.destroy()
