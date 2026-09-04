# -*- coding: utf-8 -*-
"""유튜브 음악 재생기 — 타이머와 따로 도는 작은 프로그램.

■ 왜 프로세스를 나누는가
pywebview는 창을 반드시 메인 스레드에서 열어야 한다(메인 스레드가 아니면
webview가 스스로 예외를 던진다). 그 자리는 이미 tkinter 타이머가 쓰고 있어서
같은 프로세스 안에는 넣을 수 없다. 그래서 이 파일을 자식으로 띄우고 파이프로
이야기한다. 덤으로, 음악을 끄면 프로세스째 사라져 메모리가 0으로 돌아온다.

■ 대화 방식
부모 → 자식 : 표준입력에 한 줄 JSON.  {"c":"load","v":"영상ID"} 처럼.
자식 → 부모 : 표준출력에 `@YT {json}` 한 줄. 그 밖의 출력(엔진 경고 등)은
              부모가 전부 버린다.
부모가 죽으면 표준입력이 닫히므로 자식도 스스로 끝난다. 고아가 남지 않는다.

■ 왜 작은 웹서버를 같이 띄우는가
유튜브 IFrame API는 페이지의 origin을 본다. 파일(file://)이나 문자열로 띄우면
origin이 null이라 postMessage가 막혀 조종이 안 된다. 127.0.0.1에 임시 서버를
하나 띄우면 제대로 된 http origin이 생겨 공식 API가 그대로 동작한다.

■ 창을 왜 '숨기지 않고 화면 밖에' 두는가  (실측으로 갈랐다)
크로미움은 감춘 창(SW_HIDE)에서는 재생을 시작하지 못한다. 실측하면 상태가
'받는 중(3)'에 붙어 재생 위치가 0.0에서 안 움직인다. 반면 화면 밖 좌표에
'보이는' 창으로 두면 정상 재생된다. 한 번 재생이 시작된 뒤에는 숨겨도 계속
나오지만, 곡을 바꿀 때마다 다시 시작해야 하므로 그냥 계속 화면 밖에 둔다.
작업표시줄·Alt+Tab에 안 뜨도록 도구창 속성을, 포커스를 빼앗지 않도록 비활성
속성을 준다. 다만 로그인할 때는 글자를 쳐야 하므로 둘 다 잠시 푼다.

■ 로그인
`--profile <폴더>`를 주면 그 폴더에 브라우저 자료(쿠키 포함)를 남긴다.
한 번 유튜브에 로그인해 두면 다음부터는 저절로 로그인된 채로 뜨고, 프리미엄
계정이면 광고 없이 나온다. 그 폴더에는 구글 세션이 들어 있으니 절대 공개
저장소에 올리지 말 것 (.gitignore에 넣어 두었다).
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

# WebView2가 뜨기 전에 정해 두어야 하는 값들.
#  - autoplay-policy : 사람이 클릭하지 않으면 소리 나는 재생을 막는 기본 정책.
#                      우리 창은 사람이 볼 수 없으니 이걸 풀어야 재생된다.
#  - CalculateNativeWinOcclusion : 이걸 안 끄면 화면 밖 창을 '가려졌다'고 보고
#                      렌더링을 멈춘다. 화면 밖에 두는 우리 방식과 충돌한다.
#  - GPU 끄기 : 영상을 볼 일이 없으니 그림 가속이 필요 없다. 실측 55MB 절약.
#  - low-end-device-mode : 크로미움의 저사양 모드. 실측 10MB쯤 더 줄었다.
#  - 프로세스 격리(site isolation)는 끄지 않는다. 조금 더 줄지만 이 프로필에
#    구글 로그인이 들어 있어, 사이트끼리 벽을 허무는 건 값이 너무 비싸다.
#  - mute-audio 아님 : 소리가 목적이므로 절대 넣지 않는다.
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--autoplay-policy=no-user-gesture-required "
    "--disable-features=CalculateNativeWinOcclusion "
    "--disable-gpu --disable-gpu-compositing "
    "--enable-low-end-device-mode")

# 화면 배율(DPI)을 아는 프로그램으로 선언한다. 안 하면 윈도우가 좌표를 배율만큼
# 접어서 넘겨, 부모가 알려 준 로그인 창 자리가 엉뚱한 곳이 된다(150% 화면에서
# 1.5배 어긋났다). 창을 만들기 전에 해야 한다.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # 모니터별 인식
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 모든 모니터 바깥이면서, 최소화한 창이 쓰는 마법의 자리(-32000)는 피한 좌표.
OFF_X, OFF_Y = -30000, -30000
LOGIN_URL = ("https://accounts.google.com/ServiceLogin"
             "?continue=https%3A%2F%2Fwww.youtube.com%2F&hl=ko")

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
# 끼워 넣을 때 벗길 창틀 — 제목 줄·테두리·크기 조절·단추
WS_FRAME = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00030000
_STYLE0 = None                    # 끼우기 전 스타일 (뗄 때 되돌린다)
WS_EX_TOOLWINDOW = 0x00000080     # 작업표시줄·Alt+Tab 에 안 뜨게
WS_EX_NOACTIVATE = 0x08000000     # 포커스를 빼앗지 않게

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#000;overflow:hidden;
width:100%;height:100%}
#p{display:block;border:0}</style>
</head><body>
<div id="p"></div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
var pl = null, ready = false, lastErr = 0, pending = null;
// 소리만 쓸 때는 가장 낮은 화질로 둔다 (디코딩을 아낀다). 영상을 보여
// 줄 때만 fit 을 켜서 창을 꽉 채우고 화질을 유튜브에 맡긴다.
var fit = false;

function onYouTubeIframeAPIReady() {
  pl = new YT.Player('p', {
    height: '180', width: '320',
    playerVars: {autoplay: 0, controls: 0, disablekb: 1, fs: 0,
                 modestbranding: 1, rel: 0, playsinline: 1,
                 origin: location.origin},
    events: {
      onReady: function () {
        ready = true;
        if (!fit) { try { pl.setPlaybackQuality('small'); } catch (e) {} }
        if (pending) { var q = pending; pending = null; q(); }
      },
      onError: function (e) { lastErr = e.data || 0; },
      onStateChange: function (e) {
        // 화질은 재생이 시작될 때마다 유튜브가 되돌리므로 다시 낮춘다.
        // 소리만 들으니 제일 낮은 화질이면 충분하다 (디코딩 비용 절약).
        if (e.data === 1 && !fit) {
          try { pl.setPlaybackQuality('small'); } catch (x) {}
        }
      }
    }
  });
}

function later(fn) { if (ready) { fn(); return true; } pending = fn; return false; }

window.ytLoad = function (vid, list, vol) {
  lastErr = 0;
  return later(function () {
    try { pl.setVolume(vol); } catch (e) {}
    if (list) { pl.loadPlaylist({list: list, listType: 'playlist', index: 0}); }
    else { pl.loadVideoById(vid); }
  });
};
window.ytPlay  = function () { return later(function () { pl.playVideo(); }); };
window.ytPause = function () { return later(function () { pl.pauseVideo(); }); };
window.ytVol   = function (v) { return later(function () { pl.setVolume(v); }); };

window.ytFit = function (on) {
  // 창을 꽉 채운다. setSize 는 CSS 픽셀이라 화면 배율만큼 커져 잘린다 —
  // 100% 로 두고 브라우저에 맡긴다.
  fit = !!on;
  var el = document.getElementById('p');
  if (el) {
    el.removeAttribute('width');
    el.removeAttribute('height');
    el.style.width = fit ? '100%' : '320px';
    el.style.height = fit ? '100%' : '180px';
  }
  return later(function () {
    if (!fit) { try { pl.setPlaybackQuality('small'); } catch (e) {} }
  });
};

window.ytStatus = function () {
  var o = {ready: ready, state: -9, pos: 0, dur: 0, title: '', vid: '',
           err: lastErr};
  if (!ready || !pl) return o;
  try { o.state = pl.getPlayerState(); } catch (e) {}
  try { o.pos = pl.getCurrentTime() || 0; } catch (e) {}
  try { o.dur = pl.getDuration() || 0; } catch (e) {}
  try {
    var d = pl.getVideoData() || {};
    o.title = d.title || '';
    o.vid = d.video_id || '';
  } catch (e) {}
  return o;
};
</script></body></html>
"""


_MAIN = None


def _main_window():
    """진짜 창 하나만 골라 낸다 (한 번 찾으면 기억한다).

    이 프로세스에는 IME·GDI+ 같은 보이지 않는 도우미 창이 여럿 딸려 있다.
    전부를 대상으로 ShowWindow를 부르면 그 숨은 창들까지 '보임'으로 바뀐다.
    WinForms 프레임이면서 제목이 있는 창이 우리 창이다.
    """
    global _MAIN
    u = ctypes.WinDLL("user32", use_last_error=True)
    if _MAIN and u.IsWindow(_MAIN):
        return u, _MAIN
    me = ctypes.WinDLL("kernel32").GetCurrentProcessId()
    found = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)

    def cb(h, _):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value != me:
            return 1
        cls = ctypes.create_unicode_buffer(200)
        u.GetClassNameW(h, cls, 200)
        if cls.value.startswith("WindowsForms10.Window") \
                and u.GetWindowTextLengthW(h) > 0:
            found.append(h)
        return 1

    u.EnumWindows(CB(cb), 0)
    _MAIN = found[0] if found else None
    return u, _MAIN


def _park_offscreen():
    """창을 화면 밖으로 옮기고, 작업표시줄에 안 뜨는 도구창으로 바꾼다.

    작업표시줄 단추는 창을 숨겼다 다시 보일 때만 다시 계산된다. 보이는 채로
    도구창 속성만 바꾸면 단추가 그대로 남는다 — 로그인을 마치고 돌아왔을 때
    작업표시줄에 아이콘이 남던 원인이 이것이다.
    """
    try:
        u, h = _main_window()
        if not h:
            return False
        ex = u.GetWindowLongW(h, GWL_EXSTYLE)
        want = ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if want != ex and u.IsWindowVisible(h):
            u.ShowWindow(h, 0)                 # SW_HIDE
            u.SetWindowLongW(h, GWL_EXSTYLE, want)
            u.ShowWindow(h, 8)                 # SW_SHOWNA (활성화하지 않고)
        else:
            u.SetWindowLongW(h, GWL_EXSTYLE, want)
        # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        u.SetWindowPos(h, 0, OFF_X, OFF_Y, 0, 0, 0x1 | 0x4 | 0x10)
        return True
    except Exception:
        return False


def _i32(v):
    """SetWindowLongW 에 넘길 수 있게 부호 있는 32비트로."""
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


def _round_rgn(u, wh, w, h, r):
    """창을 둥글게 오려 낸다 (카드 모서리와 맞추려고).

    핸들은 반드시 형을 정해 받는다 — 64비트에서 잘리면 조용히 실패한다
    (지뢰 23).
    """
    try:
        if r <= 0:
            return
        g = ctypes.WinDLL("gdi32", use_last_error=True)
        g.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        g.CreateRoundRectRgn.restype = ctypes.c_void_p
        u.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_int]
        u.SetWindowRgn.restype = ctypes.c_int
        rgn = g.CreateRoundRectRgn(0, 0, int(w) + 1, int(h) + 1,
                                   int(r) * 2, int(r) * 2)
        if rgn:
            u.SetWindowRgn(wh, rgn, 1)      # 소유권이 창으로 넘어간다
    except Exception:
        pass


def _embed(parent, x, y, w, h, r=0):
    """부모 창(다른 프로세스여도 된다) 안에 자식 창으로 끼워 넣는다.

    좌표는 부모의 **클라이언트 영역** 기준이다. 끼워 넣으면 부모를 끌거나
    크기를 바꿀 때 저절로 따라오고 밖으로 나간 부분은 잘린다.
    """
    global _STYLE0
    try:
        u, wh = _main_window()
        if not wh or not parent:
            return False
        u.SetParent.argtypes = [wt.HWND, wt.HWND]
        u.SetParent.restype = wt.HWND
        st = u.GetWindowLongW(wh, GWL_STYLE) & 0xFFFFFFFF
        if _STYLE0 is None:
            _STYLE0 = st
        # 창틀을 통째로 벗긴다 — 안 벗기면 제목 줄과 단추가 같이 들어온다
        u.SetWindowLongW(wh, GWL_STYLE,
                         _i32((st & ~WS_POPUP & ~WS_FRAME) | WS_CHILD))
        ex = u.GetWindowLongW(wh, GWL_EXSTYLE) & 0xFFFFFFFF
        # 도구창 속성은 뗀다 (자식 창에는 뜻이 없다). 포커스는 계속 안 뺏는다.
        u.SetWindowLongW(wh, GWL_EXSTYLE,
                         _i32((ex | WS_EX_NOACTIVATE) & ~WS_EX_TOOLWINDOW))
        u.SetParent(wh, wt.HWND(int(parent)))
        # SWP_SHOWWINDOW | SWP_NOACTIVATE | SWP_NOZORDER | SWP_FRAMECHANGED
        u.SetWindowPos(wh, 0, int(x), int(y), int(w), int(h),
                       0x40 | 0x10 | 0x4 | 0x20)
        _round_rgn(u, wh, w, h, r)
        u.ShowWindow(wh, 8)                 # SW_SHOWNA
        return True
    except Exception:
        return False


def _vidbox(x, y, w, h, r=0):
    """끼워 넣은 채로 자리·크기만."""
    try:
        u, wh = _main_window()
        if not wh:
            return False
        u.SetWindowPos(wh, 0, int(x), int(y), int(w), int(h), 0x10 | 0x4)
        _round_rgn(u, wh, w, h, r)
        return True
    except Exception:
        return False


def _unembed():
    """떼어 내 다시 화면 밖으로 (부모 창이 사라지기 전에 반드시)."""
    try:
        u, wh = _main_window()
        if not wh:
            return False
        u.SetParent.argtypes = [wt.HWND, wt.HWND]
        u.SetParent.restype = wt.HWND
        u.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_int]
        u.SetWindowRgn(wh, None, 0)        # 둥글게 오린 것을 되돌린다
        st = u.GetWindowLongW(wh, GWL_STYLE) & 0xFFFFFFFF
        want = _STYLE0 if _STYLE0 is not None else ((st & ~WS_CHILD) | WS_POPUP)
        u.SetWindowLongW(wh, GWL_STYLE, _i32(want))
        u.SetParent(wh, wt.HWND(0))
        # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
        u.SetWindowPos(wh, 0, 0, 0, 0, 0, 0x2 | 0x1 | 0x4 | 0x20)
        return _park_offscreen()
    except Exception:
        return False


def _bring_out(x, y, w, h):
    """로그인할 때 — 평범한 창으로 되돌리고 화면 안으로 꺼낸다.

    비활성(WS_EX_NOACTIVATE) 속성을 반드시 풀어야 한다. 안 그러면 창이
    포커스를 못 받아 아이디를 칠 수가 없다.
    """
    try:
        u, wh = _main_window()
        if not wh:
            return False
        ex = u.GetWindowLongW(wh, GWL_EXSTYLE)
        u.SetWindowLongW(wh, GWL_EXSTYLE,
                         ex & ~WS_EX_TOOLWINDOW & ~WS_EX_NOACTIVATE)
        u.SetWindowPos(wh, 0, int(x), int(y), int(w), int(h), 0x4)
        u.ShowWindow(wh, 5)              # SW_SHOW
        u.SetForegroundWindow(wh)
        return True
    except Exception:
        return False


def _serve():
    """127.0.0.1에 페이지 하나만 내주는 임시 서버. (포트는 OS가 고른다)"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    body = PAGE.encode("utf-8")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            # 경로가 무엇이든 이 한 장만 내준다. 파일 시스템은 건드리지 않는다.
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):       # 표준오류를 어지럽히지 않게
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class Player:
    def __init__(self, window, page):
        self.win = window
        self.page = page
        self.vol = 60
        self.want_play = False
        # 곡이 끝나면 **자식이 되감을 것인가**. 목록으로 트는 동안에는
        # 부모가 다음 곡을 정하므로 꺼 둔다 — 둘이 같이 정하면 자식이
        # 먼저 되감아 부모가 'ended' 를 못 보고 같은 곡이 반복된다.
        self.loop = True
        self.mode = "play"           # play | login | watch(임베드 막힘 폴백)
        self.fit = False             # 부모 창 안에 끼워 넣어 영상을 보이는가
        self.signed = None           # 로그인 상태를 확인했는가 (모르면 None)
        self.cur = ("", "")          # 지금 틀어 둔 (영상, 재생목록)
        self.resume = False          # 로그인에서 돌아와 다시 틀어야 하는가
        self._last = None
        self._sent = 0.0
        self._replay = 0.0
        self._watch_at = 0.0         # watch 폴백을 시작한 시각
        self._watch_kick = 0.0       # watch 에서 마지막으로 재생을 민 시각
        self._watch_vol = -1         # watch 페이지에 적용해 둔 볼륨

    # ── 페이지에 말 걸기 ──────────────────────────────────────────────────
    def js(self, expr):
        """창이 죽었거나 스크립트가 터져도 재생기가 통째로 멈추지는 않게."""
        try:
            return self.win.evaluate_js(expr)
        except Exception:
            return None

    def load(self, vid, lst):
        self.cur = (vid, lst)
        self.js("ytLoad(%s,%s,%d)" % (json.dumps(vid), json.dumps(lst),
                                      self.vol))

    def cmd(self, msg):
        c = msg.get("c")
        if c == "load":
            self.want_play = True
            # 안 보내는 옛 부모와도 맞물리게 기본은 '되감는다'
            self.loop = bool(int(msg.get("loop", 1) or 0))
            if self.mode == "login":     # 로그인 끝나면 이 곡으로 시작한다
                self.cur = (str(msg.get("v") or ""), str(msg.get("list") or ""))
                self.resume = True
                return
            if self.mode == "watch":
                # 임베드로 돌아가 새 곡을 튼다 — 새 곡이 또 막히면
                # 상태 폴링이 다시 폴백한다
                self.end_watch(str(msg.get("v") or ""),
                               str(msg.get("list") or ""))
                return
            self.load(str(msg.get("v") or ""), str(msg.get("list") or ""))
        elif c == "play":
            self.want_play = True
            if self.mode == "watch":
                self._watch_js_do("playVideo()", "v.play()")
            else:
                self.js("ytPlay()")
        elif c == "pause":
            self.want_play = False
            if self.mode == "watch":
                self._watch_js_do("pauseVideo()", "v.pause()")
            else:
                self.js("ytPause()")
        elif c == "vol":
            try:
                self.vol = max(0, min(100, int(msg.get("v", 60))))
            except (TypeError, ValueError):
                return
            if self.mode == "watch":
                self._watch_vol = -1         # 다음 바퀴에 새 볼륨을 건다
            else:
                self.js("ytVol(%d)" % self.vol)
        elif c == "embed":
            # 영상 보기 — 부모 창 안으로 들어간다 (요청)
            if _embed(msg.get("p"), msg.get("x", 0), msg.get("y", 0),
                      msg.get("w", 320), msg.get("h", 180),
                      msg.get("r", 0)):
                self.fit = True
                self.js("ytFit(1)")
        elif c == "vidbox":
            if self.fit:
                _vidbox(msg.get("x", 0), msg.get("y", 0),
                        msg.get("w", 320), msg.get("h", 180),
                        msg.get("r", 0))
                self.js("ytFit(1)")
        elif c == "unembed":
            self.fit = False
            self.js("ytFit(0)")
            _unembed()
        elif c == "login":
            self.begin_login(msg.get("x", 200), msg.get("y", 120))
        elif c == "login_done":
            self.end_login(None)

    # ── 임베드 막힘 폴백 (watch 페이지) ─────────────────────────────────
    # 임베드 차단(101·150)은 iframe 에만 걸린다 — 본 페이지(watch)는 늘
    # 재생된다. 막힌 곡만 같은 창을 본 페이지로 갈아 끼워 틀고, 다음 곡
    # load 가 오면 임베드 페이지로 돌아온다 (본 페이지는 메모리를 두 배
    # 쓴다 — 실측 USS 190 → 400MB — 그래서 머무는 시간을 최소로 한다).
    # 부모에게는 err=0 인 보통 재생으로 보고한다 — 부모(옛 판 포함)가
    # '막힌 곡'으로 알고 건너뛰지 않게. 부모 수정 없이 재생기 혼자 끝낸다.
    WATCH_JS = (
        "(function(){"
        "var p=document.getElementById('movie_player');"
        "if(p&&p.getPlayerState){try{var d=p.getVideoData()||{};"
        "return {ok:1,state:p.getPlayerState(),"
        "pos:p.getCurrentTime()||0,dur:p.getDuration()||0,"
        "title:d.title||'',vid:d.video_id||''};}catch(e){}}"
        "var v=document.querySelector('video');"
        "if(!v)return {ok:0};"
        "return {ok:1,state:(v.ended?0:(v.paused?2:1)),"
        "pos:v.currentTime||0,dur:v.duration||0,"
        "title:document.title.replace(/ - YouTube$/,''),vid:''};"
        "})()")

    def _watch_js_do(self, api_call, video_stmt):
        """movie_player API 를 먼저, 없으면 video 요소로."""
        self.js("(function(){var p=document.getElementById('movie_player');"
                "if(p&&p.%s){try{p.%s;return}catch(e){}}"
                "var v=document.querySelector('video');"
                "if(v){try{%s}catch(e){}}})()"
                % (api_call.split("(")[0], api_call, video_stmt))

    def begin_watch(self, vid):
        self.mode = "watch"
        self._watch_at = time.time()
        self._watch_kick = 0.0
        self._watch_vol = -1
        try:
            self.win.load_url("https://www.youtube.com/watch?v=" + vid)
        except Exception:
            pass

    def end_watch(self, vid, lst):
        """다음 곡이 왔다 — 임베드 페이지로 돌아가 그 곡을 튼다."""
        self.mode = "play"
        self.cur = (vid, lst)
        self.resume = True
        try:
            self.win.load_url(self.page)
        except Exception:
            pass

    def watch_tick(self, s):
        """watch 페이지 살림 — 볼륨 맞추기·재생 밀기·끝나면 되감기."""
        now = time.time()
        if not s.get("ready"):
            return
        if self._watch_vol != self.vol:
            self._watch_vol = self.vol
            self._watch_js_do("setVolume(%d)" % self.vol,
                              "v.volume=%f" % (self.vol / 100.0))
        st = int(s.get("state", -9))
        if self.want_play and st in (-1, 2, 5) and now - self._watch_kick > 3.0:
            # 자동재생이 안 걸렸으면 민다 (정책 플래그는 이미 풀려 있다)
            self._watch_kick = now
            self._watch_js_do("playVideo()", "v.play()")
        if (self.loop and self.want_play and st == 0
                and now - self._replay > 3.0):
            self._replay = now
            self._watch_js_do("playVideo()", "v.play()")
        if not self.want_play and st == 1:
            self._watch_js_do("pauseVideo()", "v.pause()")

    # ── 로그인 ───────────────────────────────────────────────────────────
    def begin_login(self, x, y):
        self.mode = "login"
        try:
            self.win.load_url(LOGIN_URL)
        except Exception:
            pass
        _bring_out(x, y, 560, 720)

    def end_login(self, signed):
        """로그인 화면을 접고 재생 페이지로 돌아간다."""
        if signed is not None:
            self.signed = bool(signed)
        self.mode = "play"
        self.resume = bool(self.cur[0] or self.cur[1])
        try:
            self.win.load_url(self.page)
        except Exception:
            pass
        _park_offscreen()

    def check_login(self):
        """로그인이 끝났는지 본다 — 유튜브로 돌아왔고 계정이 붙었으면 끝."""
        href = self.js("location.href") or ""
        if "youtube.com" not in href:
            return
        got = self.js("(function(){try{"
                      "return !!(window.ytcfg && ytcfg.get('LOGGED_IN'));"
                      "}catch(e){return false}})()")
        if got:
            self.end_login(True)

    # ── 상태 알리기 ──────────────────────────────────────────────────────
    def status(self):
        base = {"mode": self.mode, "signed": self.signed}
        if self.mode == "watch":
            o = self.js(self.WATCH_JS)
            if isinstance(o, dict) and o.get("ok"):
                st = int(o.get("state", -9))
                base.update({
                    "ready": True, "state": st, "playing": st == 1,
                    "buffering": st == 3, "ended": st == 0,
                    "title": str(o.get("title") or ""),
                    "vid": str(o.get("vid") or ""),
                    "pos": float(o.get("pos") or 0.0),
                    "dur": float(o.get("dur") or 0.0),
                    # 부모에게는 보통 재생으로 보인다 — 건너뛰지 않게
                    "err": 0})
            else:
                base.update({"ready": False, "state": 3, "playing": False,
                             "buffering": True, "ended": False, "title": "",
                             "vid": "", "pos": 0.0, "dur": 0.0, "err": 0})
            return base
        o = self.js("ytStatus()") if self.mode == "play" else None
        if not isinstance(o, dict):
            base.update({"ready": False, "state": -9, "playing": False,
                         "buffering": False, "ended": False, "title": "",
                         "vid": "", "pos": 0.0, "dur": 0.0, "err": 0})
            return base
        st = int(o.get("state", -9))
        base.update({"ready": bool(o.get("ready")), "state": st,
                     "playing": st == 1, "buffering": st == 3, "ended": st == 0,
                     "title": str(o.get("title") or ""),
                     "vid": str(o.get("vid") or ""),
                     "pos": float(o.get("pos") or 0.0),
                     "dur": float(o.get("dur") or 0.0),
                     "err": int(o.get("err") or 0)})
        return base

    def resume_if_needed(self, s):
        """로그인에서 돌아와 페이지가 다시 준비되면 틀던 곡을 이어 튼다."""
        if not (self.resume and s["ready"]):
            return
        self.resume = False
        self.load(*self.cur)
        if self.want_play:
            self.js("ytPlay()")

    def loop_if_ended(self, s):
        """곡이 끝나면 다시 튼다 — 한 곡짜리는 처음부터, 목록은 첫 곡부터.

        작업하는 내내 소리가 이어지라는 뜻이다. 사람이 멈춘 것(want_play가
        거짓)일 때는 건드리지 않는다.
        """
        if not self.loop or self.mode != "play":
            return                   # 목록 재생 중 — 다음 곡은 부모가 정한다
        now = time.time()
        if s.get("ended") and self.want_play and now - self._replay > 3.0:
            self._replay = now
            self.js("ytPlay()")

    def emit(self, s):
        """달라졌을 때만 보낸다. 다만 2초에 한 번은 살아있음을 알린다.

        재생 위치(pos)는 계속 바뀌므로 비교에서 뺀다. 안 그러면 0.4초마다
        한 줄씩 나가 부모가 읽을 것만 늘어난다.
        """
        key = (s["ready"], s["state"], s["title"], s["vid"], s["err"],
               s["mode"], s["signed"])
        now = time.time()
        if key == self._last and now - self._sent < 2.0:
            return
        self._last, self._sent = key, now
        try:
            sys.stdout.write("@YT " + json.dumps(s, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def _profile_arg():
    a = sys.argv[1:]
    if "--profile" in a:
        i = a.index("--profile")
        if i + 1 < len(a):
            return a[i + 1]
    return None


def main():
    """실패해도 조용히 물러난다.

    굳힌 exe(같은 실행 파일이 --yt-player로 다시 뜬 자식)에서 webview가
    .NET을 못 얹으면(다운로드 차단 표식이 남은 zip, 오래된 프레임워크 등)
    여기서 터지는데, 예외가 그대로 나가면 PyInstaller가 '스크립트 실행
    실패' 오류 창을 띄워 본체가 죽은 것처럼 보인다 (준사 제보). 부모는
    자식이 죽으면 '음악을 켤 수 없어요'라고 이미 말해 주므로, 여기서는
    상태 한 줄만 남기고 조용히 끝낸다.
    """
    try:
        _main()
    except Exception as e:
        # **왜 못 떴는지 두 갈래로 남긴다.**
        # ① 부모의 stderr — 그 자리는 .yt_err.txt 다. 굳힌 창 없는 exe 는
        #    sys.stderr 가 None 일 수 있으므로 서술자 2 번에 직접 쓴다.
        # ② 부모에게 한 줄 보고 — 예전에는 예외 **이름만** 보냈고 부모가
        #    그것을 읽지도 않아, 기록에 '준비 못 하고 죽음' 한 줄만 남고
        #    이유가 없었다 (멸종·젖소 도로롱 제보).
        try:
            import traceback
            os.write(2, ("재생기가 못 떴습니다\n"
                         + traceback.format_exc()).encode("utf-8", "replace"))
        except Exception:
            pass
        try:
            sys.stdout.write("@YT " + json.dumps(
                {"ready": False,
                 "fatal": "%s: %s" % (type(e).__name__, e)}) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def _main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import webview

    prof = _profile_arg()
    if prof:
        try:
            os.makedirs(prof, exist_ok=True)
        except OSError:
            prof = None
    srv, port = _serve()
    page = "http://127.0.0.1:%d/" % port
    # 테두리 있는 평범한 창으로 만든다 — 로그인할 때 사람이 써야 하므로.
    # 평소에는 화면 밖 도구창이라 어차피 보이지 않는다.
    win = webview.create_window("ENA 음악", page, width=320, height=180,
                                x=OFF_X, y=OFF_Y, hidden=True, on_top=False)
    pl = Player(win, page)
    stop = threading.Event()

    def on_closing():
        # 로그인 창을 그냥 닫으면 재생기까지 죽는다. 닫기를 취소하고
        # 재생 페이지로 되돌린다 (로그인은 안 한 것으로 둔다).
        if pl.mode == "login":
            pl.end_login(False)
            return False
        return True

    try:
        win.events.closing += on_closing
    except Exception:
        pass

    def reader():
        """부모의 명령을 읽는다. 파이프가 닫히면(=부모가 죽으면) 끝낸다."""
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("c") == "quit":
                    break
                try:
                    pl.cmd(msg)
                except Exception:
                    pass
        except Exception:
            pass
        stop.set()

    def pump():
        """상태를 주기적으로 부모에게 알린다."""
        threading.Thread(target=reader, daemon=True).start()
        # 화면 밖으로 치우고 도구창으로 바꾼 뒤에 보이게 한다. 순서가 중요하다 —
        # 먼저 보이면 한 프레임이나마 화면에 나타나고 포커스도 빼앗는다.
        _park_offscreen()
        try:
            win.show()
        except Exception:
            pass
        _park_offscreen()                # show가 자리를 되돌릴 수 있어 한 번 더
        # 창을 만들자마자 상태를 물으면 아직 페이지가 없다. 조금 기다린다.
        while not stop.wait(0.4):
            if pl.fit:
                # 끼워 넣은 채로 부모 창이 사라지면 우리 창도 같이 죽는다.
                # 그대로 두면 '프로세스는 살아 있는데 소리가 안 나는' 상태가
                # 되므로, 부모가 알아채게 여기서 끝낸다.
                try:
                    u9, h9 = _main_window()
                    if not h9 or not u9.IsWindow(h9):
                        os.write(2, b"embed: parent window gone\n")
                        break
                except Exception:
                    pass
            try:
                if pl.mode == "login":
                    pl.check_login()
                s = pl.status()
                # 임베드 차단(101·150) — 본 페이지로 갈아 끼워 튼다.
                # 단일 곡일 때만 (재생목록은 유튜브가 알아서 다음 곡으로
                # 넘어가고, 어느 곡이 막혔는지도 확실치 않다).
                if (pl.mode == "play" and pl.want_play and pl.cur[0]
                        and not pl.cur[1]
                        and int(s.get("err") or 0) in (101, 150)):
                    pl.begin_watch(pl.cur[0])
                    s = pl.status()
                if pl.mode == "watch":
                    pl.watch_tick(s)
                pl.resume_if_needed(s)
                pl.loop_if_ended(s)
            except Exception:
                continue
            pl.emit(s)
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    webview.start(pump, gui="edgechromium",
                  private_mode=not prof, storage_path=prof or None)


if __name__ == "__main__":
    main()
