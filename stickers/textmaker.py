"""텍스트 스티커 — 입력한 문구를 투명 PNG로 렌더링해 스티커로 추가."""
import tkinter as tk
from tkinter import colorchooser

from PIL import Image, ImageDraw, ImageFont

BG, BG_SOFT, TEXT, MUTED, BORDER = "#ffffff", "#f4f4f5", "#3f3f46", "#a1a1aa", "#e4e4e7"
ACTIVE, ACTIVE_BG = "#8892b5", "#eef0f8"
UI_FONT = "Malgun Gothic"

_FONT_REG  = "C:/Windows/Fonts/malgun.ttf"
_FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

_OUTLINES = {"없음": None, "흰색": "#ffffff", "검정": "#1a1a1e"}


def render_text(text: str, size_px: int = 48, color: str = "#3f3f46",
                bold: bool = True, outline: str | None = None) -> Image.Image:
    """여러 줄 텍스트 → 여백·외곽선 포함 투명 PNG."""
    try:
        font = ImageFont.truetype(_FONT_BOLD if bold else _FONT_REG, size_px)
    except Exception:
        font = ImageFont.load_default()
    sw = max(2, size_px // 14) if outline else 0

    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    bbox = probe.multiline_textbbox((0, 0), text, font=font, stroke_width=sw, align="center")
    pad = 10 + sw
    w = max(1, int(bbox[2] - bbox[0]) + pad * 2)
    h = max(1, int(bbox[3] - bbox[1]) + pad * 2)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.multiline_text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=color,
                     stroke_width=sw, stroke_fill=outline, align="center")
    return img


class TextStickerDialog(tk.Toplevel):
    """텍스트 입력 → 투명 PNG 스티커 생성 다이얼로그."""

    def __init__(self, parent, on_submit):
        super().__init__(parent)
        self.title("텍스트 스티커")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()
        self.on_submit = on_submit
        self.color = "#3f3f46"

        f10 = (UI_FONT, 10)
        outer = tk.Frame(self, bg=BG, padx=16, pady=14)
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text="문구", font=f10, bg=BG, fg=MUTED).pack(anchor="w")
        self.text = tk.Text(outer, width=28, height=3, font=(UI_FONT, 11), bd=0,
                            highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor=ACTIVE, wrap="word")
        self.text.pack(fill=tk.X, pady=(2, 10))
        self.text.focus_set()

        row1 = tk.Frame(outer, bg=BG)
        row1.pack(fill=tk.X, pady=(0, 8))
        tk.Label(row1, text="크기", font=f10, bg=BG, fg=MUTED).pack(side=tk.LEFT)
        self.size = tk.Scale(row1, from_=16, to=160, orient=tk.HORIZONTAL, showvalue=True,
                             bg=BG, troughcolor=BG_SOFT, highlightthickness=0, bd=0,
                             font=(UI_FONT, 8))
        self.size.set(48)
        self.size.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        row2 = tk.Frame(outer, bg=BG)
        row2.pack(fill=tk.X, pady=(0, 12))
        self.swatch = tk.Button(row2, text="  색상  ", font=f10, bd=0, padx=10, pady=4,
                                bg=self.color, fg="#ffffff", cursor="hand2",
                                command=self._pick_color)
        self.swatch.pack(side=tk.LEFT)

        self.bold_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row2, text="굵게", variable=self.bold_var, font=f10,
                       bg=BG, fg=TEXT, activebackground=BG,
                       selectcolor=BG_SOFT).pack(side=tk.LEFT, padx=(10, 0))

        tk.Label(row2, text="외곽선", font=f10, bg=BG, fg=MUTED).pack(side=tk.LEFT, padx=(12, 4))
        self.outline_var = tk.StringVar(value="없음")
        om = tk.OptionMenu(row2, self.outline_var, *_OUTLINES.keys())
        om.configure(font=(UI_FONT, 9), bg=BG_SOFT, fg=TEXT, bd=0,
                     highlightthickness=0, activebackground=BG_SOFT)
        om.pack(side=tk.LEFT)

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill=tk.X)
        tk.Button(foot, text="추가", font=(UI_FONT, 10, "bold"), bd=0, padx=18, pady=6,
                  bg=ACTIVE, fg="#ffffff", cursor="hand2",
                  command=self._submit).pack(side=tk.RIGHT)
        tk.Button(foot, text="취소", font=f10, bd=0, padx=14, pady=6,
                  bg=BG_SOFT, fg=MUTED, cursor="hand2",
                  command=self.destroy).pack(side=tk.RIGHT, padx=(0, 6))

    def _pick_color(self):
        rgb = colorchooser.askcolor(color=self.color, parent=self, title="글자 색")
        if rgb and rgb[1]:
            self.color = rgb[1]
            self.swatch.configure(bg=self.color)

    def _submit(self):
        text = self.text.get("1.0", tk.END).strip()
        if not text:
            self.destroy()
            return
        img = render_text(text, int(self.size.get()), self.color,
                          self.bold_var.get(), _OUTLINES[self.outline_var.get()])
        self.destroy()
        self.on_submit(img)
