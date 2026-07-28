"""Offline voice I/O: local Whisper STT + Windows SAPI male TTS (pyttsx3).

Free, no cloud APIs. Whisper model downloads once on first use, then works offline.
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Prefer small.en for accuracy on CPU; override with WHISPER_MODEL=base.en for speed
DEFAULT_WHISPER_MODEL = "small.en"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
# Conversational mic turn-taking (longer windows, patient silence)
DEFAULT_MAX_SECONDS = 12.0
DEFAULT_SILENCE_SECONDS = 1.15
DEFAULT_ENERGY_THRESHOLD = 0.0045
# Speaker-safe: barely above hiss — laptop/headset mics are often quiet
SPEAKER_SAFE_ENERGY = 0.0055
SPEAKER_SAFE_SETTLE = 0.2
SPEAKER_SAFE_SILENCE = 1.1
SPEAKER_SAFE_GATE_CAP = 0.014
SPEAKER_SAFE_NOISE_MULT = 1.8
# Absolute floor — if peak exceeds this, always try Whisper even if VAD missed
ALWAYS_TRY_PEAK = 0.0025

_STOP_INTERRUPT_WORDS = frozenset({
    "stop", "cancel", "enough", "quiet", "silence", "hush",
    "shutup", "wait", "pause",
})

# Approval/rejection answers — never discard these as hallucinations,
# otherwise pending confirmations can't be answered by voice.
_SHORT_ANSWERS = frozenset({
    "yes", "y", "yeah", "yep", "ok", "okay", "confirm", "proceed", "continue",
    "no", "n", "nope", "cancel", "deny", "reject",
})

# Tiny Whisper often invents these on silence / speaker echo
_HALLUCINATION_PHRASES = (
    "thank you for watching",
    "thanks for watching",
    "subscribe",
    "like and subscribe",
    "please subscribe",
    "see you next time",
    "thanks for listening",
    "thank you for listening",
    "please like and subscribe",
    "don't forget to subscribe",
    "you",
    ".",
    "thank you.",
    "thanks.",
    "bye.",
    "okay.",
    "ok.",
    "hmm",
    "uh",
    "um",
    "yeah.",
    "yes.",
    "no.",
    "i'm sorry.",
    "im sorry.",
    "subtitles by",
    "amara.org",
    "bye audible",
    "bye audible!",
    "by audible",
    "buy audible",
)

# Whisper often maps "am I audible" → Audible (audiobook brand) / "bye audible"
_TRANSCRIPT_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*(bye|by|buy|bi)\s+audible[!?.]*\s*$", re.I), "am I audible"),
    (re.compile(r"^\s*am\s+i\s+audible[!?.]*\s*$", re.I), "am I audible"),
    (re.compile(r"^\s*(are\s+you\s+)?audible[!?.]*\s*$", re.I), "am I audible"),
    (re.compile(r"^\s*can\s+you\s+hear\s+me[!?.]*\s*$", re.I), "can you hear me"),
    (re.compile(r"^\s*(hey|hi|hello)\s+immortality[!?.]*\s*$", re.I), "hello Immortility"),
    (re.compile(r"\bimmortality\b", re.I), "Immortility"),
    (re.compile(r"\brag\s+code\b", re.I), "RAG code"),
    (re.compile(r"\bvector\s+data\s*base\b", re.I), "vector database"),
)

# Male Windows voices commonly installed with SAPI
_MALE_VOICE_HINTS = (
    "david",
    "mark",
    "james",
    "george",
    "guy",
    "ryan",
    "christopher",
    "eric",
    "male",
)


class VoiceIO:
    """Singleton-friendly offline speech interface."""

    _instance: VoiceIO | None = None
    _working_mic_device: int | None | str = "unset"
    _working_mic_rate: int = DEFAULT_SAMPLE_RATE

    def __new__(cls) -> VoiceIO:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._ready = False
            cls._instance._whisper = None
            cls._instance._tts = None
            cls._instance._tts_lock = threading.Lock()
            import os as _os

            cls._instance._model_name = (
                _os.environ.get("WHISPER_MODEL") or DEFAULT_WHISPER_MODEL
            ).strip()
            cls._instance._voice_id: str | None = None
            cls._instance._rate = 190
            cls._instance._speak_cancel = threading.Event()
            cls._instance._is_speaking = False
            cls._instance._last_listen_reason = ""
            cls._instance._listen_cancel = threading.Event()
            cls._instance._whisper_device = "cpu"
        return cls._instance

    def ensure_ready(self) -> None:
        """Lazy-load STT/TTS so startup stays fast until /voice is used."""
        if self._ready:
            return
        self._init_tts()
        self._init_whisper()
        self._ready = True

    def ensure_tts(self) -> None:
        """TTS only — used for greetings without loading Whisper."""
        if self._tts is None or not self._voice_id:
            self._init_tts()

    def warm_stt(self) -> None:
        """Preload Whisper so the first HUD mic tap records immediately."""
        try:
            if self._whisper is None:
                self._init_whisper()
            logger.info("Whisper warmed (%s)", self._model_name)
        except Exception as exc:
            logger.warning("Whisper warm failed: %s", exc)

    def _init_tts(self) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
        chosen = None
        for v in voices:
            name = (getattr(v, "name", "") or "").lower()
            vid = (getattr(v, "id", "") or "").lower()
            if any(h in name or h in vid for h in _MALE_VOICE_HINTS):
                chosen = v.id
                break
        if chosen is None and voices:
            # Fall back to first available voice
            chosen = voices[0].id
        if chosen:
            engine.setProperty("voice", chosen)
            self._voice_id = chosen
        engine.setProperty("rate", self._rate)
        engine.setProperty("volume", 1.0)
        self._tts = engine
        logger.info("TTS ready voice=%s", self._voice_id or "default")

    def _init_whisper(self) -> None:
        if self._whisper is not None:
            return
        import os

        from faster_whisper import WhisperModel

        # WHISPER_DEVICE=auto|cuda|cpu — cuda is far more accurate per second,
        # but shares VRAM with Ollama, so cpu stays the safe default.
        want = (os.environ.get("WHISPER_DEVICE") or "cpu").strip().lower()
        attempts: list[tuple[str, str]] = []
        if want in {"cuda", "gpu", "auto"}:
            compute = (os.environ.get("WHISPER_COMPUTE") or "float16").strip()
            attempts.append(("cuda", compute))
        attempts.append(("cpu", (os.environ.get("WHISPER_COMPUTE_CPU") or "int8").strip()))

        last_exc: Exception | None = None
        for device, compute_type in attempts:
            try:
                model = WhisperModel(
                    self._model_name,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=0 if device == "cuda" else 4,
                )
                # CTranslate2 loads lazily, so a bad CUDA/cuBLAS install only
                # explodes on the first real encode. Prove it works now.
                if device == "cuda":
                    self._smoke_test_model(model)
                self._whisper = model
                self._whisper_device = device
                logger.info(
                    "Whisper ready model=%s device=%s compute=%s",
                    self._model_name,
                    device,
                    compute_type,
                )
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Whisper unusable on device=%s compute=%s (%s) — falling back",
                    device,
                    compute_type,
                    exc,
                )
        raise RuntimeError(f"Could not load Whisper model {self._model_name}: {last_exc}")

    @staticmethod
    def _smoke_test_model(model: Any) -> None:
        """Force one encode pass so broken GPU installs fail here, not mid-chat."""
        import numpy as np

        silence = np.zeros(DEFAULT_SAMPLE_RATE, dtype=np.float32)
        segments, _info = model.transcribe(silence, language="en", beam_size=1)
        for _ in segments:
            break

    def list_voices(self) -> list[dict[str, str]]:
        self.ensure_ready()
        import pyttsx3

        engine = pyttsx3.init()
        out = []
        for v in engine.getProperty("voices") or []:
            out.append({"id": v.id, "name": getattr(v, "name", "")})
        return out

    def set_male_voice(self) -> str:
        """Re-select best male SAPI voice; returns voice name/id."""
        if not self._voice_id:
            self._init_tts()
        return self._voice_id or "default"

    @staticmethod
    def strip_for_speech(text: str) -> str:
        """Remove markdown / code fences so TTS sounds natural."""
        if not text:
            return ""
        t = text
        t = re.sub(r"```[\s\S]*?```", " ", t)
        t = re.sub(r"`([^`]+)`", r"\1", t)
        t = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", t)
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
        t = re.sub(r"[#>*_~]{1,}", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        # Cap very long replies for conversational TTS
        if len(t) > 800:
            t = t[:800].rsplit(" ", 1)[0] + "."
        return t

    @staticmethod
    def spoken_brief(text: str, max_chars: int = 160) -> str:
        """Short version for TTS — full answer stays on screen."""
        cleaned = VoiceIO.strip_for_speech(text)
        if not cleaned:
            return ""
        if len(cleaned) <= max_chars:
            return cleaned
        # Prefer first 1–2 sentences
        parts = re.split(r"(?<=[.!?])\s+", cleaned)
        brief = ""
        for p in parts:
            if not p:
                continue
            if not brief:
                brief = p
            elif len(brief) + len(p) + 1 <= max_chars:
                brief = f"{brief} {p}"
            else:
                break
        if len(brief) > max_chars:
            brief = brief[: max_chars - 1].rsplit(" ", 1)[0] + "."
        if not brief.endswith((".", "!", "?")):
            brief += "."
        return brief

    def speak(self, text: str, *, brief: bool = True, barge_in: bool = True) -> None:
        """Speak text aloud. Say 'stop' into the mic to cut off mid-sentence."""
        if brief:
            # Keep voice replies short — full text stays on the HUD
            cleaned = self.spoken_brief(text, max_chars=160)
        else:
            cleaned = self.strip_for_speech(text)
        if not cleaned:
            logger.warning("speak() skipped — empty text after strip")
            return
        try:
            self.ensure_tts()
        except Exception as exc:
            logger.debug("TTS init soft-fail: %s", exc)

        try:
            from tools.hud_state import set_face

            set_face("speaking")
        except Exception:
            pass

        self._speak_cancel.clear()
        self._is_speaking = True
        # Only barge-in when Whisper is already loaded — avoids cold-loading mid-greet
        use_barge = barge_in and self._whisper is not None
        watcher = None
        if use_barge:
            watcher = threading.Thread(
                target=self._watch_for_stop_word,
                name="tts-stop-watch",
                daemon=True,
            )
            watcher.start()
        interrupted = False
        try:
            with self._tts_lock:
                if self._speak_sapi_interruptible(cleaned):
                    interrupted = self._speak_cancel.is_set()
                elif self._speak_powershell_interruptible(cleaned):
                    interrupted = self._speak_cancel.is_set()
                else:
                    self._speak_pyttsx3(cleaned)
                    interrupted = self._speak_cancel.is_set()
        finally:
            self._speak_cancel.set()  # stop watcher
            self._is_speaking = False
            try:
                from tools.hud_state import set_face

                set_face("idle")
            except Exception:
                pass
            if interrupted:
                logger.info("TTS interrupted by stop word")
                try:
                    from rich.console import Console

                    Console().print("[magenta]⏹ Stopped speaking.[/magenta]")
                except Exception:
                    pass

    def stop_speaking(self) -> None:
        """Force-stop current TTS (from barge-in or external cancel)."""
        self._speak_cancel.set()

    def cancel_listen(self) -> None:
        """Abort an in-flight listen/record (HUD cancel / restart)."""
        if not hasattr(self, "_listen_cancel"):
            self._listen_cancel = threading.Event()
        self._listen_cancel.set()
        self.stop_speaking()

    def wait_until_done_speaking(self, timeout: float = 90.0) -> None:
        """Block until TTS finishes (for natural voice turn-taking)."""
        import time

        deadline = time.time() + max(1.0, timeout)
        while self._is_speaking and time.time() < deadline:
            time.sleep(0.05)
        # Tiny gap so speaker echo dies before the next listen
        time.sleep(0.18)

    @staticmethod
    def _probe_input_device(device: int | None, samplerate: int = DEFAULT_SAMPLE_RATE) -> bool:
        """Return True if PortAudio can open this input device briefly."""
        try:
            import sounddevice as sd
        except Exception:
            return False
        kwargs: dict[str, Any] = dict(
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            blocksize=512,
        )
        if device is not None:
            kwargs["device"] = device
        try:
            with sd.InputStream(**kwargs):
                pass
            return True
        except Exception as exc:
            logger.debug("Mic probe failed device=%s sr=%s: %s", device, samplerate, exc)
            return False

    @classmethod
    def _pick_input_device(cls) -> int | None:
        """Pick a mic that actually opens. Prefer default, then headset, then any."""
        try:
            import sounddevice as sd
        except Exception:
            return None
        try:
            devices = sd.query_devices()
        except Exception:
            return None

        candidates: list[tuple[int, int | None]] = []  # (score, device_index|None)

        # System default first (None = let PortAudio choose)
        try:
            default_in = sd.default.device[0] if sd.default.device is not None else None
        except Exception:
            default_in = None
        if isinstance(default_in, (int, float)) and int(default_in) >= 0:
            candidates.append((200, int(default_in)))
        candidates.append((190, None))  # explicit "use host default"

        for i, d in enumerate(devices):
            if int(d.get("max_input_channels") or 0) < 1:
                continue
            name = str(d.get("name") or "").lower()
            score = 10
            # Loopback / playback capture — never
            if any(
                x in name
                for x in (
                    "stereo mix",
                    "what u hear",
                    "wave out",
                    "pc speaker",
                    "loopback",
                    "output",
                    "speakers",
                )
            ):
                continue
            if "mapper" in name or "primary sound capture" in name:
                score += 40  # stable Windows default aliases
            if any(x in name for x in ("hyperx", "cloud", "headset", "wireless", "usb")):
                score += 50
            if "microphone (" in name or name.startswith("microphone"):
                score += 30
            if "array" in name:
                score += 8
            if "realtek" in name and "mic" in name:
                score += 20
            # Prefer WASAPI/MME-friendly names; still just a hint
            if "wasapi" in name:
                score += 5
            candidates.append((score, i))

        # Highest score first; keep unique device ids (None included once)
        seen: set[int | None] = set()
        ordered: list[tuple[int, int | None]] = []
        for score, idx in sorted(candidates, key=lambda x: -x[0]):
            if idx in seen:
                continue
            seen.add(idx)
            ordered.append((score, idx))

        for score, idx in ordered:
            # Prefer the device's native rate first — Windows headsets often
            # reject forced 16 kHz with paInvalidDevice even when listed.
            rates: list[int] = []
            if idx is not None:
                try:
                    native = int(float(devices[idx].get("default_samplerate") or 0))
                    if native > 0:
                        rates.append(native)
                except Exception:
                    pass
            for candidate in (44100, 48000, DEFAULT_SAMPLE_RATE, 22050):
                if candidate not in rates:
                    rates.append(candidate)
            for sr in rates:
                if cls._probe_input_device(idx, sr):
                    label = "default" if idx is None else f"[{idx}] {devices[idx]['name']}"
                    logger.info("Mic device selected: %s (score=%s sr=%s)", label, score, sr)
                    cls._working_mic_device = idx
                    cls._working_mic_rate = sr
                    return idx

        logger.warning("No openable mic device found — will try PortAudio default at record time")
        cls._working_mic_device = None
        cls._working_mic_rate = DEFAULT_SAMPLE_RATE
        return None

    def _resolve_record_device_and_rate(self) -> tuple[int | None, int]:
        """Return (device_index|None, samplerate) that probed successfully."""
        cached_dev = getattr(self.__class__, "_working_mic_device", "unset")
        cached_rate = int(getattr(self.__class__, "_working_mic_rate", DEFAULT_SAMPLE_RATE))
        if cached_dev != "unset":
            if self._probe_input_device(cached_dev, cached_rate):  # type: ignore[arg-type]
                return cached_dev, cached_rate  # type: ignore[return-value]
            # Cache stale (unplugged) — forget and re-pick
            self.__class__._working_mic_device = "unset"  # type: ignore[attr-defined]

        picked = self._pick_input_device()
        rate = int(getattr(self.__class__, "_working_mic_rate", DEFAULT_SAMPLE_RATE))
        return picked, rate

    @staticmethod
    def _transcript_is_stop(text: str) -> bool:
        raw = (text or "").lower().strip()
        if not raw:
            return False
        cleaned = re.sub(r"[^a-z\s]", " ", raw)
        tokens = [t for t in cleaned.split() if t]
        if not tokens:
            return False
        if any(t in _STOP_INTERRUPT_WORDS for t in tokens):
            return True
        joined = " ".join(tokens)
        return any(
            p in joined
            for p in (
                "stop talking",
                "shut up",
                "be quiet",
                "stop speaking",
                "that's enough",
                "that is enough",
            )
        )

    def _watch_for_stop_word(self) -> None:
        """While TTS plays, listen for 'stop' / cancel and interrupt."""
        import time

        import numpy as np

        time.sleep(0.35)
        if self._speak_cancel.is_set() or not self._is_speaking:
            return

        try:
            import sounddevice as sd
        except Exception:
            return

        sr = DEFAULT_SAMPLE_RATE
        chunk_sec = 0.9
        frames = int(sr * chunk_sec)
        energy_gate = 0.025

        while self._is_speaking and not self._speak_cancel.is_set():
            try:
                audio = sd.rec(
                    frames,
                    samplerate=sr,
                    channels=1,
                    dtype="float32",
                    blocking=True,
                )
            except Exception as exc:
                logger.debug("stop-watch mic error: %s", exc)
                break

            if self._speak_cancel.is_set() or not self._is_speaking:
                break

            mono = audio[:, 0] if getattr(audio, "ndim", 1) > 1 else audio.reshape(-1)
            rms = float(np.sqrt(np.mean(mono**2))) if mono.size else 0.0
            if rms < energy_gate:
                continue

            try:
                self.ensure_ready()
                wav = self._write_temp_wav(mono, sr)
                text = self._transcribe(
                    wav,
                    initial_prompt="stop cancel quiet enough shut up",
                    speaker_safe=True,
                )
                try:
                    Path(wav).unlink(missing_ok=True)
                except OSError:
                    pass
            except Exception as exc:
                logger.debug("stop-watch transcribe: %s", exc)
                continue

            if self._transcript_is_stop(text):
                logger.info("Stop word heard during TTS: %r", text)
                self._speak_cancel.set()
                break

    def _write_temp_wav(self, mono_f32, sr: int) -> str:
        import numpy as np

        peak = float(np.max(np.abs(mono_f32))) if mono_f32.size else 0.0
        audio = mono_f32
        if peak > 0.05:
            audio = audio / peak * 0.9
        pcm = (audio * 32767.0).astype(np.int16)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        return tmp_path

    def _speak_sapi_interruptible(self, text: str) -> bool:
        """Async SAPI speak that can be purged when stop is heard."""
        import time

        try:
            import pythoncom
            import win32com.client
        except ImportError:
            return False

        pythoncom.CoInitialize()
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            try:
                speaker.Volume = 100
            except Exception:
                pass
            try:
                speaker.Rate = 1
            except Exception:
                pass
            if self._voice_id:
                try:
                    for v in speaker.GetVoices():
                        try:
                            desc = str(v.GetDescription())
                            token_id = str(getattr(v, "Id", "") or "")
                        except Exception:
                            desc, token_id = "", ""
                        if self._voice_id.lower() in token_id.lower() or "david" in desc.lower():
                            speaker.Voice = v
                            break
                except Exception:
                    pass

            speaker.Speak(text, 1)
            while True:
                if self._speak_cancel.is_set():
                    try:
                        speaker.Speak("", 3)
                    except Exception:
                        try:
                            speaker.Speak("", 2)
                        except Exception:
                            pass
                    logger.info("SAPI purged (interrupted)")
                    return True
                try:
                    running = int(speaker.Status.RunningState)
                except Exception:
                    break
                if running != 2:
                    break
                time.sleep(0.04)
            logger.info("SAPI spoke %d chars", len(text))
            return True
        except Exception as exc:
            logger.warning("SAPI interruptible speak failed: %s", exc)
            return False
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _speak_powershell_interruptible(self, text: str) -> bool:
        """PowerShell TTS as subprocess — killable on stop."""
        import subprocess
        import time

        safe = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Volume = 100; "
            "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male) } catch {}; "
            f"$s.Speak('{safe}')"
        )
        try:
            proc = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            while proc.poll() is None:
                if self._speak_cancel.is_set():
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                    logger.info("PowerShell TTS killed (interrupted)")
                    return True
                time.sleep(0.05)
            logger.info("PowerShell TTS spoke %d chars", len(text))
            return True
        except Exception as exc:
            logger.warning("PowerShell TTS error: %s", exc)
            return False

    def _speak_pyttsx3(self, text: str) -> None:
        import pyttsx3
        import time

        try:
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:
            pass
        engine = None
        try:
            engine = pyttsx3.init("sapi5")
            if self._voice_id:
                engine.setProperty("voice", self._voice_id)
            engine.setProperty("rate", self._rate)
            engine.setProperty("volume", 1.0)
            engine.say(text)

            done = threading.Event()

            def _run() -> None:
                try:
                    engine.runAndWait()
                finally:
                    done.set()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            while not done.is_set():
                if self._speak_cancel.is_set():
                    try:
                        engine.stop()
                    except Exception:
                        pass
                    break
                time.sleep(0.05)
            t.join(timeout=2)
        finally:
            try:
                if engine is not None:
                    engine.stop()
            except Exception:
                pass
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _wait_until_quiet(self, *, settle: float = SPEAKER_SAFE_SETTLE) -> None:
        """Stop TTS and wait briefly for speakers to finish ringing into the mic."""
        import time

        was_speaking = self._is_speaking
        self.stop_speaking()
        deadline = time.time() + 2.0
        while self._is_speaking and time.time() < deadline:
            time.sleep(0.04)
        # Only wait for echo if TTS was actually playing — otherwise arm mic ASAP
        if was_speaking:
            time.sleep(max(0.12, settle))
        else:
            time.sleep(0.05)

    @staticmethod
    def _is_garbage_transcript(text: str) -> bool:
        """Reject silence-hallucinations (e.g. 'X-Men' repeated 40 times)."""
        raw = (text or "").strip()
        if not raw:
            return True
        low = raw.lower().strip().rstrip(".!?,")
        # Confirmation answers must always survive — they drive pending actions
        if low in _SHORT_ANSWERS:
            return False
        if low in _HALLUCINATION_PHRASES:
            return True
        for phrase in _HALLUCINATION_PHRASES:
            if len(phrase) > 4 and low == phrase.rstrip(".!?"):
                return True
            if len(phrase) > 8 and low == phrase:
                return True

        tokens = re.findall(r"[a-z0-9']+", low)
        if not tokens:
            return True
        # Only drop ultra-short filler — keep real one-word commands (stop, open, …)
        if len(tokens) == 1 and tokens[0] in {"you", "the", "a", "uh", "um", "hmm"}:
            return True

        n = len(tokens)
        if n >= 6:
            unique = len(set(tokens))
            if unique / n < 0.35:
                return True
            from collections import Counter

            top_count = Counter(tokens).most_common(1)[0][1]
            if top_count / n >= 0.4:
                return True
            if n >= 10 and unique <= 3:
                return True
        return False

    @staticmethod
    def _correct_transcript(text: str) -> str:
        """Fix known Whisper mishears for Immortility voice chat."""
        t = (text or "").strip()
        if not t:
            return ""
        for pattern, repl in _TRANSCRIPT_FIXES:
            if pattern.pattern.startswith("^"):
                if pattern.match(t):
                    t = repl
                    break
            else:
                t = pattern.sub(repl, t)
        t = re.sub(r"\s+", " ", t).strip()
        # Strip trailing YouTube-style junk
        t = re.sub(
            r"[\s,.-]*(thanks for watching|please subscribe|like and subscribe).*$",
            "",
            t,
            flags=re.I,
        ).strip()
        # Domain vocabulary repair — "leet code" → LeetCode, "immortality" → Immortility
        try:
            from tools.voice_vocab import repair_transcript

            t = repair_transcript(t)
        except Exception as exc:
            logger.debug("vocab repair skipped: %s", exc)
        return t

    @staticmethod
    def _resample_mono_to_16k(audio, sr: int):
        """Linear resample to 16 kHz for Whisper (numpy only)."""
        import numpy as np

        if sr == DEFAULT_SAMPLE_RATE:
            return audio.astype(np.float32, copy=False)
        if audio.size == 0:
            return audio.astype(np.float32, copy=False)
        duration = audio.size / float(sr)
        n_out = max(1, int(round(duration * DEFAULT_SAMPLE_RATE)))
        x_old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
        return np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)

    def listen(
        self,
        *,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        silence_seconds: float = DEFAULT_SILENCE_SECONDS,
        energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        initial_prompt: str | None = None,
        speaker_safe: bool = False,
        conversational: bool = False,
    ) -> str:
        """Record from the default mic and transcribe offline with Whisper.

        speaker_safe / conversational (HUD): patient turn-taking, soft gate,
        always try Whisper if any audio is present.
        """
        self.ensure_ready()
        import time

        # Fresh cancel flag for this listen turn
        if not hasattr(self, "_listen_cancel"):
            self._listen_cancel = threading.Event()
        self._listen_cancel.clear()
        conversational = conversational or speaker_safe
        if conversational:
            self._wait_until_quiet(settle=SPEAKER_SAFE_SETTLE if self._is_speaking else 0.05)
            energy_threshold = min(energy_threshold, SPEAKER_SAFE_ENERGY)
            silence_seconds = max(silence_seconds, SPEAKER_SAFE_SILENCE)
            max_seconds = max(max_seconds, 10.0)
            if initial_prompt is None:
                try:
                    from tools.voice_vocab import initial_prompt as vocab_prompt

                    initial_prompt = vocab_prompt()
                except Exception:
                    initial_prompt = (
                        "Am I audible? Can you hear me? Hello Immortility. "
                        "Open YouTube. Read the knowledge folder. Stop talking."
                    )
        else:
            if self._is_speaking:
                self._wait_until_quiet(settle=0.25)
            else:
                time.sleep(0.05)

        try:
            from tools.hud_state import set_face

            set_face("listening")
        except Exception:
            pass

        wav_path, heard_speech = self._record_wav(
            max_seconds=max_seconds,
            silence_seconds=silence_seconds,
            energy_threshold=energy_threshold,
            calibrate_noise=conversational,
            conversational=conversational,
        )
        try:
            if self._listen_cancel.is_set():
                self._last_listen_reason = "cancelled"
                return ""
            if not heard_speech:
                self._last_listen_reason = "no_speech"
                logger.info("Listen: no usable mic audio")
                return ""
            try:
                from tools.hud_state import set_face

                set_face("thinking", status="TRANSCRIBING // WHISPER")
            except Exception:
                pass
            if self._listen_cancel.is_set():
                self._last_listen_reason = "cancelled"
                return ""
            text = self._transcribe(
                wav_path,
                initial_prompt=initial_prompt,
                speaker_safe=conversational,
                accurate=True,
            )
            if self._listen_cancel.is_set():
                self._last_listen_reason = "cancelled"
                return ""
            text = self._correct_transcript(text)
            if self._is_garbage_transcript(text):
                # One more pass without VAD for quiet speech
                text2 = self._transcribe(
                    wav_path,
                    initial_prompt=initial_prompt,
                    speaker_safe=conversational,
                    force_no_vad=True,
                    accurate=True,
                )
                if self._listen_cancel.is_set():
                    self._last_listen_reason = "cancelled"
                    return ""
                text2 = self._correct_transcript(text2)
                if not self._is_garbage_transcript(text2):
                    text = text2
                else:
                    self._last_listen_reason = "garbage"
                    logger.info("Listen: dropped transcript %r", (text or text2)[:120])
                    return ""
            self._last_listen_reason = "ok"
            logger.info("Listen transcript: %r", text[:160])
            return text
        finally:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except OSError:
                pass
            try:
                from tools.hud_state import set_face

                set_face("idle")
            except Exception:
                pass

    def _record_wav(
        self,
        *,
        max_seconds: float,
        silence_seconds: float,
        energy_threshold: float,
        calibrate_noise: bool = False,
        conversational: bool = False,
    ) -> tuple[str, bool]:
        """Record mic audio. Soft gate + always keep a buffer for Whisper."""
        import time

        import numpy as np
        import sounddevice as sd

        sr = DEFAULT_SAMPLE_RATE
        block = int(sr * 0.1)  # 100ms frames
        frames: list[Any] = []
        all_frames: list[Any] = []  # full stream for fallback
        silent_blocks = 0
        needed_silent = max(1, int(silence_seconds / 0.1))
        max_blocks = max(1, int(max_seconds / 0.1))
        min_listen_blocks = 12 if conversational else 8
        started = False
        preroll: list[Any] = []
        speech_blocks = 0
        gate = float(energy_threshold)
        noise_rms_vals: list[float] = []
        blocks_seen = 0
        calibrate_blocks = 4 if calibrate_noise else 0
        device, preferred_sr = self._resolve_record_device_and_rate()
        sr = int(preferred_sr) if preferred_sr else DEFAULT_SAMPLE_RATE
        block = int(sr * 0.1)

        def callback(indata, _frames, _time, status) -> None:  # type: ignore[no-untyped-def]
            nonlocal silent_blocks, started, speech_blocks, gate, blocks_seen
            if status:
                logger.debug("sounddevice status: %s", status)
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
            rms = float(np.sqrt(np.mean(mono**2))) if mono.size else 0.0
            blocks_seen += 1
            all_frames.append(mono)

            if calibrate_noise and blocks_seen <= calibrate_blocks:
                noise_rms_vals.append(rms)
                if blocks_seen == calibrate_blocks and noise_rms_vals:
                    noise_rms = float(np.median(noise_rms_vals))
                    gate = min(
                        SPEAKER_SAFE_GATE_CAP,
                        max(gate, noise_rms * SPEAKER_SAFE_NOISE_MULT, SPEAKER_SAFE_ENERGY * 0.6),
                    )
                    logger.info("Mic calibrated noise_rms=%.4f gate=%.4f", noise_rms, gate)

            if not started:
                preroll.append(mono)
                if len(preroll) > 20:
                    preroll.pop(0)

            # Soft start — conversational mode uses a lower effective gate
            eff_gate = gate * (0.75 if conversational else 1.0)
            if rms >= eff_gate:
                if not started:
                    frames.extend(preroll[-8:])
                    started = True
                silent_blocks = 0
                speech_blocks += 1
                frames.append(mono)
            elif started:
                silent_blocks += 1
                frames.append(mono)

        def _open_and_capture(dev: int | None, rate: int) -> None:
            stream_kwargs: dict[str, Any] = dict(
                samplerate=rate,
                channels=DEFAULT_CHANNELS,
                dtype="float32",
                blocksize=int(rate * 0.1),
                callback=callback,
            )
            if dev is not None:
                stream_kwargs["device"] = dev
            with sd.InputStream(**stream_kwargs):
                for i in range(max_blocks):
                    if self._listen_cancel.is_set():
                        logger.info("Mic capture cancelled")
                        break
                    time.sleep(0.1)
                    if (
                        started
                        and silent_blocks >= needed_silent
                        and i + 1 >= min_listen_blocks
                    ):
                        break

        # Retry cascade: preferred → default device → common rates
        attempts: list[tuple[int | None, int]] = [
            (device, sr),
            (None, sr),
            (None, DEFAULT_SAMPLE_RATE),
            (None, 44100),
            (None, 48000),
        ]
        # de-dupe while preserving order
        seen_attempt: set[tuple[int | None, int]] = set()
        last_exc: Exception | None = None
        opened = False
        for dev, rate in attempts:
            key = (dev, rate)
            if key in seen_attempt:
                continue
            seen_attempt.add(key)
            try:
                _open_and_capture(dev, rate)
                sr = rate
                device = dev
                self.__class__._working_mic_device = dev
                self.__class__._working_mic_rate = rate
                opened = True
                logger.info("Mic capture OK device=%s sr=%s", dev, rate)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning("Mic open failed device=%s sr=%s: %s", dev, rate, exc)
                # Invalid device index can go stale after unplug / host-API shuffle
                msg = str(exc).lower()
                if "invalid device" in msg or "-9996" in msg or "paerrorcode -9996" in msg:
                    try:
                        sd._terminate()  # type: ignore[attr-defined]
                        sd._initialize()  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self.__class__._working_mic_device = "unset"
                    # Force next attempts onto PortAudio default
                    device = None
                # reset buffers for next attempt
                frames.clear()
                all_frames.clear()
                silent_blocks = 0
                started = False
                preroll.clear()
                speech_blocks = 0
                blocks_seen = 0
                noise_rms_vals.clear()

        if not opened:
            logger.exception("Mic InputStream failed after retries: %s", last_exc)
            raise RuntimeError(
                f"Microphone unavailable ({last_exc}). "
                "Check Windows Settings → Privacy → Microphone is On, "
                "set a default input device, then restart Immortility."
            ) from last_exc
        if self._listen_cancel.is_set():
            self._last_listen_reason = "cancelled"
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(DEFAULT_CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(b"\x00\x00" * 800)
            return tmp_path, False

        heard_speech = started and speech_blocks >= 1
        # Fallback: use whole buffer if quiet speech never crossed the gate
        if not heard_speech and all_frames:
            audio_all = np.concatenate(all_frames)
            peak_all = float(np.max(np.abs(audio_all))) if audio_all.size else 0.0
            if peak_all >= ALWAYS_TRY_PEAK:
                # Keep last ~6s for Whisper
                keep = int(sr * 6.0)
                if audio_all.size > keep:
                    audio_all = audio_all[-keep:]
                frames = [audio_all]
                heard_speech = True
                logger.info("Mic fallback buffer peak=%.4f — sending to Whisper", peak_all)

        logger.info(
            "Mic capture: started=%s speech_blocks=%s gate=%.4f heard=%s",
            started,
            speech_blocks,
            gate,
            heard_speech,
        )
        if not frames:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(DEFAULT_CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(b"\x00\x00" * 1600)
            return tmp_path, False

        audio = np.concatenate(frames)
        # Always feed Whisper 16 kHz mono — native headset rates get resampled
        if sr != DEFAULT_SAMPLE_RATE:
            audio = self._resample_mono_to_16k(audio, sr)
            sr = DEFAULT_SAMPLE_RATE
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.04:
            audio = audio / peak * 0.92
        elif peak > 0:
            # Boost quiet headset speech more aggressively
            audio = audio * min(3.5, 0.55 / peak)

        pcm = (audio * 32767.0).astype(np.int16)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(DEFAULT_CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        return tmp_path, True

    def _transcribe(
        self,
        wav_path: str,
        initial_prompt: str | None = None,
        *,
        speaker_safe: bool = False,
        force_no_vad: bool = False,
        accurate: bool = False,
    ) -> str:
        assert self._whisper is not None
        prompt = initial_prompt
        if prompt is None and not speaker_safe:
            try:
                from core.desktop_scanner import whisper_vocabulary_hint

                prompt = whisper_vocabulary_hint()
            except Exception:
                prompt = "Reyansh Immortility coding assistant."
        if prompt is None:
            prompt = (
                "Am I audible? Can you hear me? Hello Immortility. "
                "Open YouTube. Stop talking."
            )

        use_vad = (not force_no_vad) and (not speaker_safe)
        # accurate=True: slower beam search — much better for short HUD phrases
        beam = 5 if accurate or speaker_safe else 1
        kwargs: dict[str, Any] = dict(
            language="en",
            vad_filter=use_vad,
            beam_size=beam,
            best_of=beam,
            temperature=0.0 if beam == 1 else [0.0, 0.2, 0.4],
            condition_on_previous_text=False,
            no_speech_threshold=0.55 if speaker_safe else 0.5,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            initial_prompt=(prompt or "")[:224],
            word_timestamps=False,
            # Penalize the looping repeats Whisper emits on near-silence
            repetition_penalty=1.15,
            no_repeat_ngram_size=3,
        )
        # Bias decoding toward Immortility's own vocabulary (site + project names)
        try:
            from tools.voice_vocab import hotwords_string

            hot = hotwords_string()
            if hot:
                kwargs["hotwords"] = hot
        except Exception as exc:
            logger.debug("hotwords unavailable: %s", exc)
        if use_vad:
            kwargs["vad_parameters"] = dict(
                min_silence_duration_ms=350,
                speech_pad_ms=400,
            )
            kwargs["hallucination_silence_threshold"] = 2.0

        segments, _info = self._whisper.transcribe(wav_path, **kwargs)
        parts: list[str] = []
        for seg in segments:
            no_speech = getattr(seg, "no_speech_prob", None)
            if no_speech is not None and float(no_speech) > (0.88 if speaker_safe else 0.85):
                continue
            t = (seg.text or "").strip()
            if t:
                parts.append(t)
        text = " ".join(parts).strip()
        text = re.sub(r"\s+", " ", text).strip()
        return text


def get_voice() -> VoiceIO:
    return VoiceIO()
