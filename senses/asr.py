"""ASR：faster-whisper small/int8，CPU 推理（32G 内存够，不抢 8G 显存）。"""
import os

_MODEL = None
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "models", "whisper-small")


def _get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel("small", device="cpu", compute_type="int8",
                              download_root=MODEL_DIR)
    return _MODEL


def _decode(path: str):
    """av 19 移除了 metadata_errors 参数，faster-whisper 自带解码不兼容；
    自行重采样到 16k 单声道 float32 再喂模型。"""
    import av
    import numpy as np
    with av.open(path) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(stream):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().flatten())
    return np.concatenate(chunks).astype("float32")


def transcribe(audio_path: str) -> str:
    """音频文件 → 中文文本。失败返回空串，调用方降级为文字输入。"""
    try:
        model = _get_model()
        audio = _decode(audio_path)
        segments, _info = model.transcribe(audio, language="zh",
                                           beam_size=1, vad_filter=True)
        return "".join(s.text for s in segments).strip()
    except Exception:
        return ""