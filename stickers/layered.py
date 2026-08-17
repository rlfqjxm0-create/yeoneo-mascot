"""Windows 레이어드 창 렌더링 — PNG를 픽셀 알파 그대로 데스크탑에 표시.

UpdateLayeredWindow 기반: 투명 픽셀은 보이지 않고 클릭도 통과한다.
64비트 핸들 truncation 방지를 위해 함수 프로토타입을 명시한다.
"""
import ctypes
from ctypes import wintypes

# ctypes.windll.user32 는 프로세스가 공유하는 하나뿐인 객체다. 거기에 대고
# argtypes를 정하면 같은 함수를 쓰는 다른 코드까지 그 규격에 묶인다.
# 실제로 마스코트의 그림자(ShadowLayer)가 자기 구조체로 UpdateLayeredWindow를
# 부르다가 'expected LP_SIZE' 오류로 죽었다. 그래서 여기서는 공유본을 쓰지 않고
# 이 묶음만의 핸들을 따로 연다 (WinDLL을 직접 만들면 함수 캐시가 분리된다).
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020   # 마우스 완전 통과(잠금)
WS_EX_TOOLWINDOW  = 0x00000080   # 작업표시줄/Alt-Tab 숨김
WS_EX_NOACTIVATE  = 0x08000000
_ULW_ALPHA        = 0x00000002
_AC_SRC_OVER      = 0x00
_AC_SRC_ALPHA     = 0x01


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


# 64비트에서 HWND/HDC가 32비트 int로 잘리지 않도록 — 없으면 UpdateLayeredWindow가 조용히 실패
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.GetParent.restype = wintypes.HWND
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.SetWindowLongW.restype = wintypes.LONG
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
    ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD,
]


def hwnd_of(tk_window) -> int:
    """tkinter Toplevel의 최상위 HWND."""
    tk_window.update_idletasks()
    return _user32.GetParent(wintypes.HWND(tk_window.winfo_id()))


def set_sticker_exstyle(hwnd, locked: bool):
    """레이어드 + 툴윈도우 + 비활성. 잠금 시 마우스 완전 통과."""
    ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if locked:
        ex |= WS_EX_TRANSPARENT
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


def _to_premultiplied_bgra(img) -> bytes:
    """RGBA PIL 이미지 → premultiplied BGRA 바이트."""
    ba = bytearray(img.tobytes("raw", "RGBA"))
    for i in range(0, len(ba), 4):
        a = ba[i + 3]
        r, g, b = ba[i], ba[i + 1], ba[i + 2]
        if a != 255:
            r = r * a // 255
            g = g * a // 255
            b = b * a // 255
        ba[i] = b
        ba[i + 1] = g
        ba[i + 2] = r
    return bytes(ba)


def paint(hwnd, img, x: int, y: int):
    """레이어드 창에 RGBA 이미지를 (x, y) 화면 좌표로 그린다."""
    w, h = img.size
    bits = _to_premultiplied_bgra(img)

    screen_dc = _user32.GetDC(0)
    mem_dc = _gdi32.CreateCompatibleDC(screen_dc)

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h          # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    ppv = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
    ctypes.memmove(ppv, bits, len(bits))
    old = _gdi32.SelectObject(mem_dc, hbmp)

    size = wintypes.SIZE(w, h)
    src = wintypes.POINT(0, 0)
    dst = wintypes.POINT(int(x), int(y))
    blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)

    _user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
                                mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), _ULW_ALPHA)

    _gdi32.SelectObject(mem_dc, old)
    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(mem_dc)
    _user32.ReleaseDC(0, screen_dc)
