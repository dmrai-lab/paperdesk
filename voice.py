"""The transcription of a voice note, shared by the server and the terminal client: faster-whisper on the CPU, the
model named in the config's ``[voice]`` table, its weights under ``models/`` beside the desk, the glossary as the
initial prompt. ``status`` says whether a note spoken now will get its words."""
import threading
from pathlib import Path

_MODEL = {"m": None, "lock": threading.Lock()}


def status(cfg, models_dir):
    """Whether voice notes can be transcribed here: ``{"ok": bool, "reason": str, "model": name}``."""
    name = (cfg.get("voice") or {}).get("model", "small")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return dict(ok=False, model=name, reason="faster-whisper is not installed (pip install faster-whisper); notes are stored and desk.py transcribe recovers them once it is")
    have = any(Path(models_dir).glob(f"models--*whisper*{name}*")) if Path(models_dir).exists() else False
    return dict(ok=True, model=name, reason="" if have else f"the {name} model downloads on first use")


def load(cfg, models_dir):
    """The model, loaded once (its weights fetched to ``models_dir`` when absent)."""
    from faster_whisper import WhisperModel
    with _MODEL["lock"]:
        if _MODEL["m"] is None:
            _MODEL["m"] = WhisperModel((cfg.get("voice") or {}).get("model", "small"), device="cpu", compute_type="int8",
                                       download_root=str(models_dir), cpu_threads=8)
    return _MODEL["m"]


def transcribe(path, cfg, models_dir):
    """The words in a voice note, or ``None`` when no model can be had here."""
    if not status(cfg, models_dir)["ok"]:
        return None
    m = load(cfg, models_dir)
    segs, _info = m.transcribe(str(path), vad_filter=True, beam_size=5, initial_prompt=(cfg.get("voice") or {}).get("glossary") or None)
    return " ".join(sg.text.strip() for sg in segs).strip()


def words_into(target, text, model_name):
    """Write a transcription (or its absence) into a comment or reply record."""
    if text:
        target["text"] = text; target["transcribed"] = "faster-whisper " + model_name
    else:
        target["text"] = target.get("dictation") or ("(voice note, no words recognised)" if text == "" else
                                                     "(voice note, not transcribed: no transcriber on the server; desk.py transcribe recovers it)")
        target["transcribed"] = "none available" if text is None else "empty"


def pending(items):
    """Every (comment, reply index or None) whose voice note still has no words from the model."""
    out = []
    for c in items:
        if c.get("audio") and c.get("transcribed") in (None, "none available"):
            out.append((c, None))
        for i, r in enumerate(c.get("replies", [])):
            if r.get("audio") and r.get("transcribed") in (None, "none available"):
                out.append((c, i))
    return out
