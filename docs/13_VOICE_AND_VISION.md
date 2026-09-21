# 13 — Voice and Vision

## Voice

### Current State (Functional)

Voice is fully implemented and working for conversational interaction. The gap is that it routes to `fast_chat()` instead of the full agent loop.

**Components:**
- `tools/voice_io.py::VoiceIO` — singleton orchestrating STT + TTS
- STT: `faster-whisper small.en` — local, offline, CPU by default (~500MB)
- TTS: Windows SAPI via `pyttsx3` + `win32com` — interruptible with "stop"
- Barge-in: background thread monitors mic for "stop" words during TTS playback
- Hallucination filter: rejects common Whisper artifacts ("thanks for watching", etc.)
- Transcript correction: fixes known Whisper mishears (e.g. "immortality" for "Immortility")

**Activation:** `/talk` or `/voice` in CLI; mic button in HUD

### The Gap

```python
# current main.py run_speech_to_speech():
reply = await asyncio.to_thread(
    fast_chat,               # ← WRONG: fast chat only
    heard,
    history=hist,
    max_output_tokens=get_config().speech_max_tokens,
)

# target:
route = await classify_route(heard)
strategy = classify_strategy(heard, category=route)
# then dispatch to full action path
```

This is a ~20 line change. The full agent capability is already there. It just needs to be wired to the voice input path.

### Full Voice → Agent Pipeline (Phase 4)

```
User speaks: "JARVIS, run the tests in the immortality project"
    ↓
VoiceIO.listen() → Whisper → "run the tests in the immortality project"
    ↓
classify_route() → ACTION
classify_strategy() → AGENT
    ↓
Task.create(goal="run the tests in the immortality project")
    ↓
[execute_action() or coding_engine.run_coding_loop()]
    → git_status
    → run_command(pytest tests/)
    → read test output
    → DONE with summary
    ↓
VoiceIO.speak("I ran the tests. 3 passed, 2 failed. The failures are in test_hermes_backend.py...")
    ↓
HUD shows full task timeline
```

### Progress Narration

For long-running tasks, Immortality should verbally acknowledge and provide updates:

```python
async def voice_acknowledge(message: str) -> None:
    """Brief spoken acknowledgment during execution."""
    brief = voice.spoken_brief(message, max_chars=60)
    await asyncio.to_thread(voice.speak, brief)

# Called at task creation:
await voice_acknowledge("Working on it. Running the tests now.")

# Called at key milestones:
await voice_acknowledge("Tests ran. Investigating 2 failures.")

# Called at completion:
await voice_acknowledge("Done. Found the issue and fixed it. 5 tests now passing.")
```

### Voice Routing Design

```python
# Proposed: main.py run_speech_to_speech()
async def _process_voice_turn(heard: str, state: AgentState) -> str:
    # Fast path: short acknowledgments, questions about status
    if _looks_like_fast_chat(heard):
        return await asyncio.to_thread(fast_chat, heard, ...)
    
    # Short acknowledgment before potentially long work
    await voice_acknowledge("On it.")
    
    # Full routing path
    route = await classify_route(heard)
    strategy = classify_strategy(heard, category=route)
    
    if strategy == MODE_FAST:
        return await asyncio.to_thread(fast_chat, heard, ...)
    elif strategy == MODE_AGENT:
        return await execute_action(heard, ...)
    elif route == "PROJECT":
        return await _handle_project_query(heard, memory)
    else:
        return await execute_workflow(heard, memory)
```

### Wake Word (Future)

Future: Passive listening for a wake phrase ("Hey Immortality" or "JARVIS").

Implementation options:
- `pvporcupine` (Porcupine) — local wake word detection, very low CPU
- `openwakeword` — open source alternative
- Custom keyword using Whisper (higher CPU, runs ~1s per window)

Not yet implemented. For now, the user activates voice explicitly with `/talk`.

---

## Vision

### Current State (Not Implemented)

Vision capability is architecturally defined but has no active model:
- `config/models.yaml` — vision spec exists, `model_id` empty (OLLAMA_VISION_MODEL unset)
- `models/router.py` — `ROLE_VISION = "vision"` with VRAM-aware selection
- `models/manager.py` — heavy model eviction handles brain→vision switch
- `core/capabilities.py` — reports `vision: unavailable` when no model is configured

**No screenshot tool exists in the tool registry.** There is no `screen_analyze()` function.

### Vision Model Configuration

**Step 1:** Pull a vision model
```powershell
ollama pull qwen2.5vl:7b-instruct-q4_K_M
```

**Step 2:** Set in `.env`
```
OLLAMA_VISION_MODEL=qwen2.5vl:7b-instruct-q4_K_M
```

**Step 3:** After Phase 3 implementation, `capability_available("vision")` returns True.

### Vision Input Format

OpenAI-compat multimodal message format (supported by Qwen-VL via Ollama):
```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "What is visible on this screen?"},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{base64_screenshot}"
            }
        }
    ]
}]
```

### Vision Use Cases for JARVIS

1. **Screen understanding:** "What application is in focus?" → screenshot + vision model
2. **Error reading:** "What does that error dialog say?" → screenshot + OCR/vision
3. **UI navigation fallback:** "Click the red button" when accessibility tree fails → vision locates button → click coordinates
4. **Verification:** "Did the file save correctly?" → screenshot + vision confirms
5. **World state update:** Periodic screenshots → vision builds/updates APPLICATION nodes in world model

### Vision Tool Design (Phase 3)

```python
# tools/screen_tool.py

import mss
import base64
from PIL import Image
import io

class ScreenTool:
    @staticmethod
    def _capture_region(region: dict | None = None) -> bytes:
        with mss.mss() as sct:
            if region:
                shot = sct.grab(region)
            else:
                shot = sct.grab(sct.monitors[1])  # primary monitor
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    
    @staticmethod
    def _encode_to_base64(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("utf-8")
    
    @staticmethod
    def analyze(prompt: str, region: dict | None = None) -> str:
        """Take a screenshot and query the vision model."""
        from core.llm import chat
        from models.router import capability_available
        
        if not capability_available("vision"):
            return "Vision capability is not available. Set OLLAMA_VISION_MODEL."
        
        image_bytes = ScreenTool._capture_region(region)
        b64 = ScreenTool._encode_to_base64(image_bytes)
        
        response = chat(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                ]
            }],
            role="vision",
        )
        return response["message"]["content"]
    
    @staticmethod
    def read_text(region: dict | None = None) -> str:
        """Extract text from screen via OCR using vision model."""
        return ScreenTool.analyze("Read all visible text on screen exactly as written.", region)
    
    @staticmethod
    def describe(region: dict | None = None) -> str:
        """Describe what is visible on screen."""
        return ScreenTool.analyze(
            "Describe what you see on screen. Focus on: what application is open, "
            "what the main content shows, any visible errors or dialogs, "
            "and what the user appears to be working on.",
            region
        )
```

### VRAM Management for Vision

The vision model is heavy (~6.8GB). When a vision task is requested:

1. `models/manager.py::ensure_loaded("vision")` detects the brain is resident
2. Brain is evicted via `_http_json(ollama_root + "/api/generate", {"model": brain_id, "keep_alive": 0})`
3. Vision model loads on first inference request
4. Vision task executes
5. Next non-vision LLM call triggers brain eviction of vision model and brain reload

This is a ~15-30 second overhead for the first vision call after a non-vision session. Subsequent vision calls within the same session are fast.

### OCR Alternative

For text extraction from screenshots, an OCR library (tesseract/pytesseract) can be used instead of the vision model. It is faster (CPU-only) but less capable (cannot understand context, just extract text).

```python
# Optional: add pytesseract as a fallback when vision model is unavailable
try:
    import pytesseract
    from PIL import Image
    text = pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes)))
except ImportError:
    text = "[OCR not available: install pytesseract]"
```

---

## Voice + Vision Combined

The full JARVIS interaction model combines both:

```
User says: "JARVIS, what's on my screen?"
    → Whisper: "what's on my screen"
    → classify_route() → ACTION
    → execute_action()
        → screen_describe()
            → mss screenshot
            → vision model analyzes
            → returns description
    → VoiceIO.speak(description[:160])  # brief spoken version
    → HUD shows full description + screenshot thumbnail
```

```
User says: "JARVIS, that error dialog - what does it say?"
    → execute_action()
        → screen_analyze("Read the error dialog text")
        → vision model reads it
    → VoiceIO.speak("The error says: connection refused at port 8642")
```

---

## Privacy Considerations

Screenshots may contain sensitive information:
- Personal files visible on desktop
- Private emails or messages on screen
- Passwords visible (e.g., in terminal)

The system must handle this carefully:

1. **Screenshots are never stored permanently** unless the user explicitly requests a save
2. **Screenshots are never sent to cloud APIs** — vision model must be local (Ollama)
3. **Screenshots are not indexed** by TurboVec
4. **The user can disable screen capture** via `IMMORTILITY_SCREEN_CAPTURE=0` in `.env`
5. **Screenshots in task observations** are stored as references to temporary files, not inline
