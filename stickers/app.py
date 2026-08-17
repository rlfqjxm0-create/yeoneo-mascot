"""바탕화면 스티커 — 타이머 안에서 도는 판.

- 투명 PNG를 바탕화면 위에 픽셀 알파 그대로 표시 (투명 영역 클릭 통과)
- 스티커: 왼쪽 드래그 이동 · 휠 크기 조절 · 우클릭 메뉴(편집/잠금/삭제)
- 관리 창: 파일 추가 · 클립보드 붙여넣기 · 목록에서 편집/삭제 · 슬롯(프리셋)
- 이미지는 images/ 폴더에 복사본으로 관리, 배치는 stickers.json에 저장

타이머에 얹으면서 바뀐 곳 (원래는 자기가 루트인 독립 프로그램이었다):
  - 저장 위치를 밖에서 정한다(set_base). 캐릭터 폴더에 두면 자동 업데이트가
    통째로 갈아엎어 스티커가 사라진다.
  - 스티커 창의 부모는 '관리 창'이 아니라 타이머 본체다. 관리 창을 부모로
    삼으면 관리 창을 닫는 순간 스티커가 전부 같이 사라진다.
    (타이머를 끄면 스티커도 함께 사라지는 건 의도한 대로다.)
  - '종료'는 타이머를 죽이지 않는다. 관리 창만 닫는다.
"""
import json
import math
import os
import platform
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageGrab

from . import layered
from .editor import StickerEditor
from .textmaker import TextStickerDialog

_DIR = os.path.dirname(os.path.abspath(__file__))
_IMAGES = os.path.join(_DIR, "images")
_CONFIG = os.path.join(_DIR, "stickers.json")


def set_base(base_dir):
    """스티커 이미지·배치를 저장할 폴더를 정한다.

    자동 업데이트가 건드리지 않는 곳(설정·타이머 기록이 있는 폴더)을 넘긴다.
    """
    global _IMAGES, _CONFIG
    os.makedirs(base_dir, exist_ok=True)
    _IMAGES = os.path.join(base_dir, "images")
    _CONFIG = os.path.join(base_dir, "stickers.json")

# ENA 라이트 팔레트 (타이머와 동일 톤)
BG, BG_SOFT, TEXT, MUTED, BORDER = "#ffffff", "#f4f4f5", "#3f3f46", "#a1a1aa", "#e4e4e7"
ACTIVE, ACTIVE_BG, DANGER = "#8892b5", "#eef0f8", "#dc2626"
UI_FONT = "Malgun Gothic" if platform.system() == "Windows" else "sans-serif"

MAX_SRC_PX = 1024   # 추가 시 원본을 이 크기 이하로 축소 저장 (메모리·디스크 절약)


class StickerWin:
    """바탕화면 스티커 1장 = 레이어드 Toplevel."""

    SRC_KEEP = 6.0     # 원본을 들고 있는 시간(초) — 크기 조절이 끝나면 놓는다
    HR = 9             # 손잡이 반지름(px)
    MARGIN = 16        # 손잡이가 잘리지 않게 그림 둘레에 두는 여백

    def __init__(self, app: "StickerApp", meta: dict):
        self.app = app
        self.id = meta["id"]
        self.file = meta["file"]
        self.x = int(meta.get("x", 200))
        self.y = int(meta.get("y", 200))
        self.w = int(meta.get("w", 220))
        self.angle = float(meta.get("angle", 0)) % 360
        self.locked = bool(meta.get("locked", False))
        self._src: Image.Image | None = None
        self._src_used = 0.0
        self._release_id = None
        # (폭, 각도) → 그린 판. 드래그 중에 다시 만들지 않으려고 들고 있는다
        self._cache: tuple[tuple, Image.Image] | None = None
        self._drag = None
        self.selected = False       # 눌러서 고른 상태 — 손잡이가 보인다
        self._mode = None           # move / size / turn
        self._start = None

        # 부모는 타이머 본체다. 관리 창을 부모로 두면 관리 창을 닫을 때
        # 스티커가 전부 같이 사라진다.
        self.win = tk.Toplevel(app.master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"+{self.x}+{self.y}")
        self._hwnd = layered.hwnd_of(self.win)
        layered.set_sticker_exstyle(self._hwnd, self.locked)

        self.win.bind("<Button-1>", self._press)
        self.win.bind("<B1-Motion>", self._dragging)
        self.win.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<MouseWheel>", self._wheel)
        self.win.bind("<Button-3>", self._menu)

        self.reload()

    # ── 이미지 ──
    def reload(self):
        self._src = None            # 다음에 필요할 때 다시 읽는다
        self._cache = None
        self.render()

    def _source(self) -> Image.Image | None:
        """원본 — 크기를 바꿀 때만 필요하다. 평소에는 들고 있지 않는다.

        1024px 원본은 4MB인데 화면에는 보통 220px로 보여 준다. 계속 쥐고
        있으면 장당 4.4MB라 몇 장만 붙여도 수십 MB가 된다. 크기를 바꾸는
        동안만 들고 있다가 잠잠해지면 놓는다.
        """
        if self._src is None:
            try:
                self._src = Image.open(
                    os.path.join(_IMAGES, self.file)).convert("RGBA")
            except Exception:
                return None
        self._src_used = time.time()
        if self._release_id is None:
            self._release_id = self.win.after(
                int(self.SRC_KEEP * 1000), self._release_src)
        return self._src

    def _release_src(self):
        """크기 조절이 끝났으면 원본을 놓는다 (화면에 그릴 판은 그대로 둔다)."""
        self._release_id = None
        if not self._alive():
            return
        left = self.SRC_KEEP - (time.time() - self._src_used)
        if left > 0.05:                     # 방금 또 썼다 — 조금 더 들고 있는다
            self._release_id = self.win.after(int(left * 1000),
                                              self._release_src)
            return
        self._src = None

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _scaled(self) -> Image.Image | None:
        """크기와 각도를 반영한 그림 (선택 표시는 아직 없다)."""
        key = (self.w, round(self.angle))
        if self._cache and self._cache[0] == key:
            return self._cache[1]           # 그릴 판이 이미 있으면 원본이 필요 없다
        src = self._source()
        if src is None:
            return None
        sw, sh = src.size
        h = max(1, round(self.w * sh / sw))
        img = src.resize((max(1, self.w), h), Image.LANCZOS)
        if round(self.angle) % 360:
            # expand=True — 돌리면 모서리가 잘리지 않게 판이 커진다
            img = img.rotate(-self.angle, expand=True, resample=Image.BICUBIC)
        self._cache = (key, img)
        return img

    def _decorated(self, img: Image.Image) -> Image.Image:
        """고른 상태 표시 — 테두리 + 손잡이 두 개(크기·회전).

        손잡이도 그림의 일부로 그린다. 레이어드 창은 불투명한 픽셀에서만
        마우스를 받으므로, 그려 넣어야 누를 수 있다.
        """
        m = self.MARGIN
        out = Image.new("RGBA", (img.width + m * 2, img.height + m * 2),
                        (0, 0, 0, 0))
        out.alpha_composite(img, (m, m))
        d = ImageDraw.Draw(out)
        d.rectangle([m - 1, m - 1, m + img.width, m + img.height],
                    outline=(136, 146, 181, 210), width=2)
        r = self.HR
        # 오른쪽 아래 = 크기, 위쪽 가운데 = 회전
        sx, sy = self._handle_xy("size", img)
        tx, ty = self._handle_xy("turn", img)
        d.line([m + img.width // 2, m, tx, ty], fill=(136, 146, 181, 190), width=2)
        for (hx, hy), col in (((sx, sy), (136, 146, 181)), ((tx, ty), (240, 160, 190))):
            d.ellipse([hx - r, hy - r, hx + r, hy + r],
                      fill=col + (255,), outline=(255, 255, 255, 255), width=2)
        return out

    def _handle_xy(self, which, img):
        """손잡이 중심 (여백 포함 판 기준)."""
        m = self.MARGIN
        if which == "size":
            return m + img.width, m + img.height
        return m + img.width // 2, max(self.HR + 1, m - 6)

    def render(self, keep_center=False):
        """화면에 올린다. keep_center면 판 크기가 바뀌어도 가운데를 붙잡는다
        (크기·회전을 바꾸면 판이 커졌다 작아졌다 하는데, 왼쪽 위를 고정하면
        스티커가 옆으로 기어간다)."""
        img = self._scaled()
        if img is None:
            return
        show = self._decorated(img) if self.selected else img
        if keep_center:
            old = getattr(self, "_shown_wh", None)
            if old:
                self.x += (old[0] - show.width) // 2
                self.y += (old[1] - show.height) // 2
        self._shown_wh = (show.width, show.height)
        self.win.geometry(f"{show.width}x{show.height}+{self.x}+{self.y}")
        try:
            layered.paint(self._hwnd, show, self.x, self.y)
        except Exception:
            pass

    def set_selected(self, on: bool):
        if bool(on) == self.selected:
            return
        self.selected = bool(on)
        self.render(keep_center=True)

    def set_locked(self, locked: bool):
        self.locked = locked
        if locked and self.selected:
            self.set_selected(False)     # 잠근 채로 손잡이가 남으면 못 없앤다
        layered.set_sticker_exstyle(self._hwnd, locked)
        self.app.save_config()
        self.app.refresh_list()

    # ── 상호작용 ──
    def _hit(self, ex, ey):
        """누른 자리가 어느 손잡이인가 (고른 상태일 때만)."""
        if not self.selected:
            return "move"
        img = self._scaled()
        if img is None:
            return "move"
        grab = self.HR + 6
        for which in ("size", "turn"):
            hx, hy = self._handle_xy(which, img)
            if (ex - hx) ** 2 + (ey - hy) ** 2 <= grab * grab:
                return which
        return "move"

    def _center(self):
        w, h = getattr(self, "_shown_wh", (self.w, self.w))
        return self.x + w / 2.0, self.y + h / 2.0

    def _press(self, e):
        was = self.selected                 # 누르기 '전'의 상태를 기억해 둔다
        self._mode = self._hit(e.x, e.y)
        self.app.select(self.id)            # 누르면 이 스티커를 고른다
        cx, cy = self._center()
        self._start = {"was": was,
                       "mx": e.x_root, "my": e.y_root,
                       "x": self.x, "y": self.y, "w": self.w,
                       "angle": self.angle,
                       "cx": cx, "cy": cy,
                       "r0": math.hypot(e.x_root - cx, e.y_root - cy) or 1.0,
                       "a0": math.degrees(math.atan2(e.y_root - cy,
                                                     e.x_root - cx)),
                       "moved": False}
        self._drag = True

    def _dragging(self, e):
        s = self._start
        if not self._drag or s is None:
            return
        dx, dy = e.x_root - s["mx"], e.y_root - s["my"]
        if abs(dx) > 2 or abs(dy) > 2:
            s["moved"] = True
        if self._mode == "move":
            self.x, self.y = s["x"] + dx, s["y"] + dy
            img = self._scaled()
            show = self._decorated(img) if (self.selected and img) else img
            if show is not None:
                self.win.geometry(f"+{self.x}+{self.y}")
                try:                        # 캐시 사용 — 이동은 붙여넣기만
                    layered.paint(self._hwnd, show, self.x, self.y)
                except Exception:
                    pass
            return
        if self._mode == "size":
            # 가운데에서 얼마나 멀어졌는지의 비율로 키운다
            r = math.hypot(e.x_root - s["cx"], e.y_root - s["cy"])
            self.w = max(40, min(1400, round(s["w"] * r / s["r0"])))
        else:                               # turn — 가운데를 축으로 각도
            a = math.degrees(math.atan2(e.y_root - s["cy"], e.x_root - s["cx"]))
            self.angle = (s["angle"] + (a - s["a0"])) % 360
            if e.state & 0x0001:            # 시프트를 누르면 15도 단위
                self.angle = round(self.angle / 15.0) * 15 % 360
        self.render(keep_center=True)

    def _release(self, _e):
        s = self._start or {}
        moved, was = bool(s.get("moved")), bool(s.get("was"))
        self._drag = None
        self._mode = None
        self._start = None
        if not moved and was:
            # 이미 골라 놓은 것을 끌지 않고 다시 눌렀다 — 조절을 끝낸다.
            # (아직 안 골랐던 경우는 누를 때 이미 골랐으니 그대로 둔다)
            self.app.select(None)
        self.app.save_config()

    def _wheel(self, e):
        step = 1.1 if e.delta > 0 else 1 / 1.1
        self.w = max(40, min(1400, round(self.w * step)))
        self.render(keep_center=True)
        self.app.save_config()

    def _menu(self, e):
        m = tk.Menu(self.win, tearoff=0)
        m.add_command(label="편집", command=lambda: self.app.edit_sticker(self.id))
        m.add_command(label="조절 끝내기" if self.selected else "크기·회전 조절",
                      command=lambda: self.app.select(
                          None if self.selected else self.id))
        m.add_command(label="똑바로 세우기", command=self._upright)
        m.add_command(label="잠금 해제" if self.locked else "잠금 (클릭 통과)",
                      command=lambda: self.set_locked(not self.locked))
        m.add_separator()
        m.add_command(label="삭제", command=lambda: self.app.remove_sticker(self.id))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _upright(self):
        self.angle = 0.0
        self.render(keep_center=True)
        self.app.save_config()

    def to_meta(self) -> dict:
        return {"id": self.id, "file": self.file, "x": self.x, "y": self.y,
                "w": self.w, "angle": round(self.angle, 1),
                "locked": self.locked}

    def set_visible(self, show: bool):
        """전부 숨기기/보이기 — 창만 감춘다(자리·크기는 그대로)."""
        try:
            if show:
                self.win.deiconify()
                self.render()            # 숨겼다 켜면 다시 그려 줘야 나온다
            else:
                self.win.withdraw()
        except Exception:
            pass

    def destroy(self):
        if self._release_id is not None:
            try:
                self.win.after_cancel(self._release_id)   # 죽은 창을 부르지 않게
            except Exception:
                pass
            self._release_id = None
        self._src = self._cache = None
        try:
            self.win.destroy()
        except Exception:
            pass


class StickerApp:
    """스티커 묶음. 관리 창은 필요할 때만 띄우고, 스티커는 계속 살아 있다.

    master = 타이머 본체(Tk 루트). 스티커 창의 부모이자 이 묶음의 수명 기준.
    """

    def __init__(self, master, visible=True, on_close=None):
        self.master = master
        self.on_close = on_close
        self.root = None                     # 관리 창 (열 때 만든다)
        # 파일 고르기·이름 묻기 창을 어디 위에 띄울지. 환경설정 안에서 쓸 때는
        # 그 창을 넘겨받는다 (안 그러면 설정 창 뒤로 숨어 안 보인다).
        self.dialog_parent = None
        self.wins: dict[str, StickerWin] = {}
        self.presets: dict[str, list] = {}   # 이름 → 스티커 구성 스냅샷
        self.last_slot = ""                  # 마지막으로 쓴 슬롯 이름
        self.win_pos_saved: dict[str, list] = {}   # 창별로 마지막에 둔 자리
        self.visible = bool(visible)
        self._load_config()

    # ── 관리 창 ──
    def open_manager(self):
        """관리 창을 띄운다 (이미 떠 있으면 앞으로)."""
        if self.root is not None and self.root.winfo_exists():
            self.root.deiconify()
            self.root.lift()
            return
        self.root = tk.Toplevel(self.master)
        self._setup_window()
        self._build_ui()
        self._refresh_presets()
        self.refresh_list()

    def _setup_window(self):
        self.root.title("바탕화면 스티커")
        self.root.configure(bg=BG)
        pos = self.win_pos("manager", (80, 80))
        self.root.geometry(f"350x500+{int(pos[0])}+{int(pos[1])}")
        self.root.minsize(320, 380)
        self.root.attributes("-topmost", True)
        try:
            self.root.iconbitmap(os.path.join(_DIR, "sticker_icon.ico"))
        except Exception:
            pass
        # X = 관리 창만 닫는다. 스티커는 그대로 남는다.
        self.root.protocol("WM_DELETE_WINDOW", self.close_manager)

    def close_manager(self):
        self.save_win_pos("manager", self.root)
        self.save_config()
        w, self.root = self.root, None
        try:
            if w is not None:
                w.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close()

    def _build_ui(self):
        f10 = (UI_FONT, 10)
        f10b = (UI_FONT, 10, "bold")

        outer = tk.Frame(self.root, bg=BG, padx=16, pady=14)
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text="바탕화면 스티커", font=(UI_FONT, 12, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(outer, text="드래그 이동 · 휠 크기 · 우클릭 편집/잠금/삭제",
                 font=(UI_FONT, 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(outer, bg=BG)
        row.pack(fill=tk.X, pady=(0, 8))
        tk.Button(row, text="+ 파일 추가", font=f10b, bd=0, padx=12, pady=6,
                  bg=ACTIVE, fg="#ffffff", cursor="hand2",
                  command=self.add_files).pack(side=tk.LEFT)
        tk.Button(row, text="붙여넣기", font=f10, bd=0, padx=12, pady=6,
                  bg=ACTIVE_BG, fg=TEXT, cursor="hand2",
                  command=self.paste_clipboard).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(row, text="텍스트", font=f10, bd=0, padx=12, pady=6,
                  bg=ACTIVE_BG, fg=TEXT, cursor="hand2",
                  command=self.add_text).pack(side=tk.LEFT, padx=(6, 0))

        # ── 프리셋(슬롯): 현재 배치를 이름으로 저장/전환 ──
        pr = tk.Frame(outer, bg=BG)
        pr.pack(fill=tk.X, pady=(0, 8))
        tk.Label(pr, text="프리셋", font=(UI_FONT, 9), bg=BG, fg=MUTED).pack(side=tk.LEFT)
        self.preset_combo = ttk.Combobox(pr, state="readonly", font=(UI_FONT, 9), width=10)
        self.preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        for label, cmd in [("저장", self.preset_save), ("적용", self.preset_apply), ("삭제", self.preset_delete)]:
            tk.Button(pr, text=label, font=(UI_FONT, 9), bd=0, padx=8, pady=3,
                      bg=BG_SOFT, fg=TEXT, cursor="hand2", command=cmd).pack(side=tk.LEFT, padx=(0, 3))

        # 목록
        lst_frame = tk.Frame(outer, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        lst_frame.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(lst_frame, font=f10, bd=0, highlightthickness=0,
                                  bg=BG, fg=TEXT, selectbackground=ACTIVE_BG,
                                  selectforeground=TEXT, activestyle="none")
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.edit_selected())

        act = tk.Frame(outer, bg=BG)
        act.pack(fill=tk.X, pady=(8, 0))
        for label, cmd in [("편집", self.edit_selected), ("잠금", self.toggle_selected_lock), ("삭제", self.remove_selected)]:
            tk.Button(act, text=label, font=f10, bd=0, padx=12, pady=5,
                      bg=BG_SOFT, fg=TEXT, cursor="hand2", command=cmd).pack(side=tk.LEFT, padx=(0, 6))
        # '종료'가 아니라 '닫기'다 — 여기서 타이머를 죽이면 안 된다
        tk.Button(act, text="닫기", font=f10, bd=0, padx=12, pady=5,
                  bg=BG_SOFT, fg=TEXT, cursor="hand2",
                  command=self.close_manager).pack(side=tk.RIGHT)

    # ── 설정 ──
    def _ui_ready(self) -> bool:
        """관리 창이 떠 있는가 — 스티커는 관리 창 없이도 살아 있다."""
        try:
            return self.root is not None and self.root.winfo_exists()
        except Exception:
            return False

    def _parent(self):
        """곁창을 띄울 부모 — 관리 창 > 환경설정 창 > 타이머 본체 순."""
        if self._ui_ready():
            return self.root
        p = self.dialog_parent
        try:
            if p is not None and p.winfo_exists():
                return p
        except Exception:
            pass
        return self.master

    def _load_config(self):
        try:
            with open(_CONFIG, encoding="utf-8") as f:
                data = json.load(f)
            metas = data.get("stickers", [])
            self.presets = data.get("presets", {}) or {}
            self.win_pos_saved = data.get("win_pos", {}) or {}
            self.last_slot = str(data.get("last_slot", "") or "")
        except Exception:
            metas = []
            self.presets = {}
            self.win_pos_saved = {}
        for m in metas:
            if os.path.exists(os.path.join(_IMAGES, m.get("file", ""))):
                w = StickerWin(self, m)
                if not self.visible:
                    w.set_visible(False)
                self.wins[m["id"]] = w
        self._refresh_presets()
        self.refresh_list()

    def select(self, sid):
        """한 번에 하나만 고른다 (손잡이가 여러 개 뜨면 헷갈린다)."""
        for k, w in self.wins.items():
            w.set_selected(k == sid and not w.locked)

    # ── 창 자리 기억 ──
    def win_pos(self, key, default=None):
        """지난번에 옮겨 둔 창 자리. 화면 밖이면 버린다."""
        p = (self.win_pos_saved or {}).get(key)
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            return default
        try:
            x, y = int(p[0]), int(p[1])
        except (TypeError, ValueError):
            return default
        sw = self.master.winfo_screenwidth()
        sh = self.master.winfo_screenheight()
        # 모니터 구성이 바뀌었을 수 있다 — 완전히 벗어났으면 기본값으로
        if x < -4000 or y < -4000 or x > sw + 4000 or y > sh + 4000:
            return default
        return (x, y)

    @staticmethod
    def _geo_xy(win):
        """창 자리를 geometry 기준으로 읽는다.

        winfo_rootx/rooty는 테두리 '안쪽'을 가리키는데 geometry("+x+y")는
        테두리 바깥을 기준으로 잡는다. 그대로 주고받으면 열 때마다 제목
        표시줄 높이만큼 오른쪽 아래로 밀린다.
        """
        g = win.geometry()                  # "WxH+X+Y"
        parts = g.split("+")
        return int(parts[1]), int(parts[2])

    def save_win_pos(self, key, win):
        try:
            if win is not None and win.winfo_exists():
                self.win_pos_saved[key] = list(self._geo_xy(win))
                self.save_config()
        except Exception:
            pass

    # ── 밖(타이머)에서 부르는 것들 ──
    def set_visible(self, show: bool):
        """스티커 전체 보이기/숨기기."""
        self.visible = bool(show)
        for w in self.wins.values():
            w.set_visible(self.visible)

    def set_all_locked(self, locked: bool):
        """전부 잠그기/풀기 — 잠그면 클릭이 스티커를 통과한다."""
        for w in self.wins.values():
            w.locked = bool(locked)
            layered.set_sticker_exstyle(w._hwnd, w.locked)
        self.save_config()
        self.refresh_list()

    def count(self) -> int:
        return len(self.wins)

    def shutdown(self):
        """타이머가 닫힐 때 — 자리를 저장하고 창을 거둔다."""
        self.save_config()
        for w in list(self.wins.values()):
            w.destroy()
        self.wins = {}
        if self._ui_ready():
            try:
                self.root.destroy()
            except Exception:
                pass
        self.root = None

    def save_config(self):
        try:
            with open(_CONFIG, "w", encoding="utf-8") as f:
                json.dump({"stickers": [w.to_meta() for w in self.wins.values()],
                           "presets": self.presets,
                           "win_pos": self.win_pos_saved,
                           "last_slot": self.last_slot},
                          f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    # ── 프리셋 ──
    def _refresh_presets(self):
        if not self._ui_ready():
            return
        names = sorted(self.presets.keys())
        self.preset_combo.configure(values=names)
        if names and not self.preset_combo.get():
            self.preset_combo.set(names[0])

    def _combo_name(self) -> str:
        """관리 창이 있을 때만 콤보에서 읽는다 (환경설정에서 쓸 땐 없다)."""
        if self._ui_ready():
            try:
                return self.preset_combo.get() or ""
            except Exception:
                pass
        return self.last_slot or ""

    def preset_save(self):
        name = simpledialog.askstring("슬롯 저장", "슬롯 이름:",
                                      initialvalue=self._combo_name(),
                                      parent=self._parent())
        if not name or not name.strip():
            return
        name = name.strip()
        self.presets[name] = [w.to_meta() for w in self.wins.values()]
        self.last_slot = name
        self.save_config()
        self._refresh_presets()
        if self._ui_ready():
            self.preset_combo.set(name)

    def preset_apply_named(self, name):
        """이름으로 슬롯 적용 — 환경설정에서 목록을 눌렀을 때."""
        self.last_slot = name
        self._apply_metas(self.presets.get(name))

    def preset_apply(self):
        self._apply_metas(self.presets.get(self._combo_name()))

    def _apply_metas(self, metas):
        if metas is None:
            return
        for w in list(self.wins.values()):
            w.destroy()
        self.wins = {}
        for m in metas:
            if os.path.exists(os.path.join(_IMAGES, m.get("file", ""))):
                w = StickerWin(self, dict(m))
                if not self.visible:
                    w.set_visible(False)
                self.wins[m["id"]] = w
        self.save_config()
        self.refresh_list()

    def preset_delete(self, name=None):
        name = name or self._combo_name()
        if name not in self.presets:
            return
        if self._ui_ready() and not messagebox.askyesno(
                "슬롯 삭제",
                f"'{name}' 슬롯을 지울까요?\n(화면의 스티커는 그대로 둡니다)",
                parent=self._parent()):
            return
        del self.presets[name]
        if self.last_slot == name:
            self.last_slot = ""
        if self._ui_ready():
            self.preset_combo.set("")
        self.save_config()
        self._refresh_presets()
        self._cleanup_orphans()

    def _referenced_files(self) -> set:
        refs = {w.file for w in self.wins.values()}
        for metas in self.presets.values():
            refs.update(m.get("file", "") for m in metas)
        return refs

    def _cleanup_orphans(self):
        """어떤 스티커/프리셋도 참조하지 않는 이미지 파일 정리."""
        refs = self._referenced_files()
        try:
            for fname in os.listdir(_IMAGES):
                if fname not in refs:
                    os.remove(os.path.join(_IMAGES, fname))
        except Exception:
            pass

    # ── 추가 ──
    def _import_image(self, img: Image.Image, name_hint: str = "sticker"):
        os.makedirs(_IMAGES, exist_ok=True)
        img = img.convert("RGBA")
        if max(img.size) > MAX_SRC_PX:
            k = MAX_SRC_PX / max(img.size)
            img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
        sid = f"stk_{int(time.time() * 1000)}"
        fname = f"{sid}.png"
        img.save(os.path.join(_IMAGES, fname))
        n = len(self.wins)
        meta = {"id": sid, "file": fname,
                "x": 240 + (n % 5) * 40, "y": 200 + (n % 5) * 40, "w": 220, "locked": False}
        w = StickerWin(self, meta)
        if not self.visible:
            # 숨김 상태에서 새로 넣었다 — 보이게 켜 준다(안 그러면 감감무소식)
            self.visible = True
        self.wins[sid] = w
        self.save_config()
        self.refresh_list()
        return sid

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="스티커 이미지 선택",
            filetypes=[("이미지", "*.png *.webp *.gif *.jpg *.jpeg"), ("모든 파일", "*.*")])
        for p in paths:
            try:
                self._import_image(Image.open(p), os.path.basename(p))
            except Exception:
                messagebox.showwarning("추가 실패", f"이미지를 열 수 없습니다:\n{p}")

    def paste_clipboard(self):
        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            data = None
        if isinstance(data, Image.Image):
            self._import_image(data)
        elif isinstance(data, list):   # 파일 복사 붙여넣기
            for p in data:
                try:
                    self._import_image(Image.open(p))
                except Exception:
                    pass
        else:
            messagebox.showinfo("붙여넣기", "클립보드에 이미지가 없습니다.")

    # ── 목록/선택 동작 ──
    def refresh_list(self):
        self._order = list(self.wins.keys())
        if not self._ui_ready():
            return
        self.listbox.delete(0, tk.END)
        for sid in self._order:
            w = self.wins[sid]
            lock = " [잠금]" if w.locked else ""
            self.listbox.insert(tk.END, f"{w.file}{lock}")

    def _selected_id(self):
        if not self._ui_ready():
            return None
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self._order[sel[0]]

    def edit_selected(self):
        sid = self._selected_id()
        if sid:
            self.edit_sticker(sid)

    def toggle_selected_lock(self):
        sid = self._selected_id()
        if sid:
            self.wins[sid].set_locked(not self.wins[sid].locked)

    def remove_selected(self):
        sid = self._selected_id()
        if sid:
            self.remove_sticker(sid)

    # ── 공용 동작 ──
    def edit_sticker(self, sid: str):
        w = self.wins.get(sid)
        if not w:
            return
        path = os.path.join(_IMAGES, w.file)
        StickerEditor(self._parent(), path, on_save=w.reload,
                      place=(lambda: self.win_pos("editor"),
                             lambda win: self.save_win_pos("editor", win)))

    def remove_sticker(self, sid: str):
        w = self.wins.pop(sid, None)
        if not w:
            return
        w.destroy()
        # 프리셋이 참조 중인 이미지는 남겨둠 (프리셋 적용 시 필요)
        if w.file not in self._referenced_files():
            try:
                os.remove(os.path.join(_IMAGES, w.file))
            except Exception:
                pass
        self.save_config()
        self.refresh_list()

    def add_text(self):
        TextStickerDialog(self._parent(),
                          on_submit=lambda img: self._import_image(img, "text"))
