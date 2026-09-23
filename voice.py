"""The transcription of a voice note, shared by the server and the terminal client: faster-whisper on the CPU, the
model named in the config's ``[voice]`` table, its weights under ``models/`` beside the desk, the glossary as the
initial prompt. ``status`` says whether a note spoken now will get its words."""
import os
import threading
from pathlib import Path

_MODEL = {"m": None, "lock": threading.Lock()}


def placement(cfg):
    """Where the model runs and how: ``(device, compute_type, kwargs)`` from the config's ``device`` (``auto`` takes
    a CUDA device CTranslate2 can see, else the CPU), ``compute_type`` (float16 on CUDA, int8 on the CPU) and
    ``cpu_threads`` (half the cores, at most eight, so a note does not starve the rest of the machine)."""
    v = cfg.get("voice") or {}
    dev = v.get("device", "auto")
    if dev == "auto":
        try:
            import ctranslate2
            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:                                       # noqa: BLE001 -- no CTranslate2, no CUDA: the CPU
            dev = "cpu"
    ct = v.get("compute_type") or ("float16" if dev == "cuda" else "int8")
    kw = {} if dev == "cuda" else {"cpu_threads": int(v.get("cpu_threads", max(1, min(8, (os.cpu_count() or 2) // 2))))}
    return dev, ct, kw


def status(cfg, models_dir):
    """Whether voice notes can be transcribed here: ``{"ok": bool, "reason": str, "model": name, "device": where}``."""
    name = (cfg.get("voice") or {}).get("model", "small")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return dict(ok=False, model=name, device=None, reason="faster-whisper is not installed (pip install faster-whisper); notes are stored and desk.py transcribe recovers them once it is")
    dev, ct, kw = placement(cfg)
    where = f"{dev}/{ct}" + (f", {kw['cpu_threads']} threads" if kw else "")
    have = any(Path(models_dir).glob(f"models--*whisper*{name}*")) if Path(models_dir).exists() else False
    return dict(ok=True, model=name, device=where, reason="" if have else f"the {name} model downloads on first use")


def load(cfg, models_dir):
    """The model, loaded once (its weights fetched to ``models_dir`` when absent) where ``placement`` puts it; a CUDA
    device that fails to load (a library missing) falls back to the CPU and says so."""
    from faster_whisper import WhisperModel
    name = (cfg.get("voice") or {}).get("model", "small")
    with _MODEL["lock"]:
        if _MODEL["m"] is None:
            dev, ct, kw = placement(cfg)
            try:
                _MODEL["m"] = WhisperModel(name, device=dev, compute_type=ct, download_root=str(models_dir), **kw)
            except Exception as e:                              # noqa: BLE001 -- the device, not the model, is what failed
                if dev == "cpu":
                    raise
                print(f"voice: {dev}/{ct} failed ({type(e).__name__}: {str(e)[:80]}); falling back to the CPU", flush=True)
                cpu_cfg = dict(cfg, voice=dict(cfg.get("voice") or {}, device="cpu"))
                dev, ct, kw = placement(cpu_cfg)
                _MODEL["m"] = WhisperModel(name, device=dev, compute_type=ct, download_root=str(models_dir), **kw)
            _MODEL["where"] = f"{dev}/{ct}"
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
