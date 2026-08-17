"""바탕화면 스티커 — 타이머에 얹어 쓰는 묶음.

원래 따로 돌던 프로그램(desktop-stickers)을 그대로 가져왔다. 화면에 그리는
방식(layered.py)과 편집기(editor.py), 텍스트 스티커(textmaker.py)는 손대지
않았고, 관리 창(app.py)만 타이머 안에서 살 수 있게 고쳤다.

  - 저장 위치를 밖에서 정한다 (set_base) — 자동 업데이트로 지워지지 않는 곳에
  - 관리 창이 자기가 루트인 척하지 않는다 (곁창으로 뜬다)

윈도우 전용이다. 맥에서는 부르지 않는다.
"""
from .app import StickerApp, set_base   # noqa: F401

__all__ = ["StickerApp", "set_base"]
