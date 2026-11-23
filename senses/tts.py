"""TTS：首选 edge-tts（晓伊，需联网）；离线降级 pyttsx3 本机 SAPI。

synth(text, out_path) -> bool。剥掉表情标签与星号动作再念。
"""
import asyncio
import re

VOICE = "zh-CN-XiaoyiNeural"
_STRIP = re.compile(r"\[emo:\w+\]|\*[^*]*\*")


def _clean(text: str) -> str:
    return _STRIP.sub("", text).strip()


def synth(text: str, out_path: str) -> bool:
    text = _clean(text)
    if not text:
        return False
    try:
        import edge_tts

        async def run():
            comm = edge_tts.Communicate(text, VOICE)
            await comm.save(out_path)
        asyncio.run(run())
        return True
    except Exception:
        pass
    try:
        import pyttsx3
        eng = pyttsx3.init()
        eng.save_to_file(text, out_path)
        eng.runAndWait()
        return True
    except Exception:
        return False