"""
Speech-to-text (STT) using faster-whisper — fully local, free, zero API cost.

Model: large-v3 on RTX 5060 (8GB VRAM) with float16
  - Near-perfect transcription accuracy
  - ~1.5GB VRAM usage — leaves plenty for other tasks
  - Real-time factor ~0.1x (10s audio transcribes in ~1s on RTX 5060)

Requires CUDA 12.x:
  Download from https://developer.nvidia.com/cuda-downloads
  (installs cublas64_12.dll and other required CUDA runtime libraries)

Install faster-whisper:
  pip install faster-whisper --break-system-packages

Models (downloaded automatically to ~/.cache/huggingface/ on first use):
  tiny        ~40MB    fastest, less accurate
  base        ~150MB   good for casual use
  small       ~500MB   great balance
  medium      ~1.5GB   excellent
  large-v3    ~3.1GB   best accuracy, recommended for RTX 5060  ← default
  distil-large-v3  ~1.5GB  90% accuracy of large-v3 at 6x speed (alternative)
"""
from pathlib import Path

_whisper_model = None
_whisper_model_size = None

def _register_nvidia_dlls(verbose: bool = False) -> None:
    """
    On Windows, explicitly add pip-installed NVIDIA package DLL directories
    to the search path. The DLLs are in nvidia/<package>/bin/ not lib/.
    """
    import os, sys
    if sys.platform != "win32":
        return

    import site
    registered = []
    for site_dir in site.getsitepackages():
        nvidia_root = Path(site_dir) / "nvidia"
        if nvidia_root.exists():
            for pkg_dir in nvidia_root.iterdir():
                for subdir in ("bin", "lib"):
                    dll_dir = pkg_dir / subdir
                    if dll_dir.exists():
                        try:
                            os.add_dll_directory(str(dll_dir))
                            registered.append(str(dll_dir))
                        except OSError:
                            pass
    if verbose:
        if registered:
            for d in registered:
                print(f"[stt] DLL dir registered: {d}")
        else:
            print("[stt] WARNING: No NVIDIA DLL dirs found — cublas may not load")


_register_nvidia_dlls()  # run once at module import


DEFAULT_MODEL   = "large-v3"   # best accuracy; fits RTX 5060 8GB comfortably
DEFAULT_DEVICE  = "cuda"       # RTX 5060
DEFAULT_COMPUTE = "float16"    # full precision on GPU — fastest + most accurate


def _get_model(model_size: str = DEFAULT_MODEL):
    global _whisper_model, _whisper_model_size

    if _whisper_model is not None and _whisper_model_size == model_size:
        return _whisper_model

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper not installed.\n"
            "Run: pip install faster-whisper --break-system-packages"
        )

    print(f"[stt] Loading Whisper '{model_size}' on {DEFAULT_DEVICE} ({DEFAULT_COMPUTE})...")
    print(f"[stt] First run downloads ~3.1GB model to ~/.cache/huggingface/")

    # Re-register DLLs right before loading (in case module was imported before venv activated)
    _register_nvidia_dlls(verbose=True)

    try:
        _whisper_model = WhisperModel(
            model_size,
            device=DEFAULT_DEVICE,
            compute_type=DEFAULT_COMPUTE,
        )
        print(f"[stt] Whisper '{model_size}' ready on GPU ✓")
    except Exception as e:
        print(f"[stt] GPU load failed ({e}), falling back to CPU int8...")
        _whisper_model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
        )
        print(f"[stt] Whisper '{model_size}' ready on CPU (slower)")

    _whisper_model_size = model_size
    return _whisper_model


def transcribe(audio_path: str | Path, model_size: str = DEFAULT_MODEL) -> str:
    """
    Transcribe an audio file (ogg, wav, mp3, m4a, webm, etc.) to text.
    Returns the transcribed string, or raises RuntimeError on failure.
    """
    # Re-register NVIDIA DLLs in this thread — os.add_dll_directory() is
    # per-thread on Windows, so executor threads need their own registration.
    _register_nvidia_dlls()

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise RuntimeError(f"Audio file not found: {audio_path}")

    model = _get_model(model_size)

    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        language="en",               # English — change to None for auto-detect
        condition_on_previous_text=False,
        vad_filter=True,             # skip silent parts — speeds up transcription
        vad_parameters=dict(
            min_silence_duration_ms=500,  # silence threshold
        ),
    )

    text = " ".join(seg.text.strip() for seg in segments).strip()

    if not text:
        return "(no speech detected)"

    print(f"[stt] ({info.language}, {audio_path.name}): {text[:120]}")
    return text
