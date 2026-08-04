"""Launch Immortility face+chat popup (red Jarvis-style app window)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.hud_state import get_hud_state, set_face, update_hud

logger = logging.getLogger(__name__)

HUD_HTML = Path(__file__).resolve().parent.parent / "frontend" / "immortility_hud.html"
HUD_PORT = 8765
HUD_WIDTH = 1000
HUD_HEIGHT = 780

_chat_history: list[dict[str, str]] = []
_history_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_GREETING = "Hello Reyansh. Immortility online — ask me anything here, or tap the mic to speak."
_last_chat_key = ""
_last_chat_reply = ""
_last_chat_at = 0.0
_chat_dedupe_lock = threading.Lock()
_hud_token: str = ""

__all__ = [
    "open_hud",
    "start_hud_server",
    "stop_hud_server",
    "set_face",
    "update_hud",
    "seed_greeting",
    "get_hud_token",
]


def get_hud_token() -> str:
    global _hud_token
    if _hud_token:
        return _hud_token
    env = (os.environ.get("IMMORTILITY_HUD_TOKEN") or "").strip()
    if env:
        _hud_token = env
        return _hud_token
    _hud_token = secrets.token_urlsafe(24)
    return _hud_token


def _term(msg: str, style: str = "cyan") -> None:
    try:
        from rich.console import Console

        Console().print(f"[bold {style}][HUD][/bold {style}] {msg}")
    except Exception:
        logger.info("HUD %s", msg)


def _check_token(handler: BaseHTTPRequestHandler) -> bool:
    expected = get_hud_token()
    got = (
        handler.headers.get("X-Immortility-Token")
        or handler.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    return bool(got) and secrets.compare_digest(got, expected)


class _HudHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("HUD %s", fmt % args)

    def _cors(self) -> None:
        # Same-origin local HUD only
        self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{HUD_PORT}")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Immortility-Token, Authorization",
        )
        self.send_header("Cache-Control", "no-store")

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
            # Client aborted (voice cancel / refresh) — not a server failure
            logger.debug("HUD client gone during response: %s", exc)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/hud", "/immortility_hud.html"):
            if not HUD_HTML.is_file():
                self.send_error(404, "HUD file missing")
                return
            html = HUD_HTML.read_text(encoding="utf-8")
            token = get_hud_token()
            inject = (
                f'<script>window.HUD_TOKEN={json.dumps(token)};'
                f'window.HUD_ORIGIN="http://127.0.0.1:{HUD_PORT}";</script>'
            )
            if "</head>" in html:
                html = html.replace("</head>", inject + "\n</head>", 1)
            else:
                html = inject + html
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/hud/status":
            if not _check_token(self):
                self._json(401, {"error": "unauthorized"})
                return
            state = get_hud_state()
            try:
                from rag.vector_store import VectorStore

                state = {**state, "rag_chunks": VectorStore().count()}
            except Exception:
                state = {**state, "rag_chunks": None}
            self._json(200, state)
            return

        if path == "/hud/history":
            if not _check_token(self):
                self._json(401, {"error": "unauthorized"})
                return
            with _history_lock:
                hist = list(_chat_history)
            self._json(200, {"messages": hist, "greeting": _GREETING})
            return

        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {
            "/hud/chat",
            "/hud/listen",
            "/hud/listen/cancel",
            "/hud/face",
            "/hud/confirm",
        }:
            self.send_error(404)
            return
        if not _check_token(self):
            self._json(401, {"error": "unauthorized — restart Immortility HUD"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return

        if path == "/hud/chat":
            self._handle_chat(data)
            return
        if path == "/hud/confirm":
            self._handle_confirm(data)
            return
        if path == "/hud/listen":
            self._handle_listen(data)
            return
        if path == "/hud/listen/cancel":
            self._handle_listen_cancel()
            return
        if path == "/hud/face":
            mode = str(data.get("face") or data.get("mode") or "idle")
            set_face(mode)
            self._json(200, {"ok": True, "face": get_hud_state().get("face")})
            return

    def _handle_confirm(self, data: dict[str, Any]) -> None:
        decision = str(data.get("decision") or "").strip().lower()
        if decision in {"yes", "y", "ok", "confirm", "proceed", "approve"}:
            msg = "yes"
        elif decision in {"no", "n", "cancel", "reject", "deny"}:
            msg = "no"
        else:
            self._json(400, {"error": "decision must be yes or no"})
            return
        self._handle_chat({"message": msg, "speak": bool(data.get("speak", True))})

    def _handle_chat(self, data: dict[str, Any]) -> None:
        import time

        global _last_chat_key, _last_chat_reply, _last_chat_at

        message = str(data.get("message") or "").strip()
        if not message:
            self._json(400, {"error": "empty message"})
            return

        key = message.lower()
        with _chat_dedupe_lock:
            now = time.time()
            if key == _last_chat_key and (now - _last_chat_at) < 4.0 and _last_chat_reply:
                self._json(200, {"reply": _last_chat_reply, "deduped": True})
                return

        _term(f"User: {message[:200]}", "white")
        with _history_lock:
            _chat_history.append({"role": "user", "content": message})
            history_snapshot = [
                m for m in _chat_history[:-1] if m.get("content")
            ][-16:]

        try:
            from core.agent_state import AgentState

            AgentState().append_message("user", message)
        except Exception:
            pass

        set_face("thinking")
        try:
            from tools.hud_agent import handle_hud_request

            reply = handle_hud_request(message, history=history_snapshot)
        except Exception as exc:
            logger.exception("HUD chat failed")
            reply = f"Sorry Reyansh — I hit an error: {exc}"
            set_face("idle")
            _term(f"Error: {exc}", "red")
            self._json(200, {"reply": reply, "error": str(exc)})
            return

        needs_confirm = False
        confirm_prompt = ""
        pending_tool = ""
        try:
            from core.agent_state import AgentState

            pending = AgentState().pending_action
            if pending and "Proceed?" in (reply or ""):
                needs_confirm = True
                confirm_prompt = reply
                pending_tool = str(pending.get("tool") or "")
                _term(f"Awaiting confirm for {pending_tool}", "yellow")
        except Exception:
            pass

        with _history_lock:
            _chat_history.append({"role": "assistant", "content": reply})

        try:
            from core.chat_thread import update_focus_after_turn

            update_focus_after_turn(message, reply, history_snapshot)
        except Exception:
            pass

        try:
            from core.agent_state import AgentState

            AgentState().append_message("assistant", reply)
        except Exception:
            pass

        with _chat_dedupe_lock:
            _last_chat_key = key
            _last_chat_reply = reply
            _last_chat_at = time.time()

        _term(f"Reply ({len(reply)} chars): {reply[:180]}…", "green")

        speak = bool(data.get("speak", True))
        wait_tts = bool(data.get("wait_tts", False))
        from_voice = bool(data.get("from_voice", wait_tts))
        if speak and needs_confirm:
            prompt = (
                f"I need your confirmation for {pending_tool or 'this action'}. "
                "Tap Yes or No on the HUD."
            )
            if wait_tts or from_voice:
                _speak_reply(prompt, barge_in=True, voice_mode=True)
            else:
                threading.Thread(
                    target=_speak_reply,
                    args=(prompt,),
                    kwargs={"barge_in": True, "voice_mode": True},
                    name="hud-tts-confirm",
                    daemon=True,
                ).start()
        elif speak:
            if wait_tts or from_voice:
                _speak_reply(reply, barge_in=True, voice_mode=True)
            else:
                threading.Thread(
                    target=_speak_reply,
                    args=(reply,),
                    kwargs={"barge_in": True, "voice_mode": from_voice},
                    name="hud-tts",
                    daemon=True,
                ).start()
        else:
            set_face("idle")

        self._json(
            200,
            {
                "reply": reply,
                "needs_confirm": needs_confirm,
                "confirm_prompt": confirm_prompt,
                "pending_tool": pending_tool,
            },
        )

    def _handle_listen_cancel(self) -> None:
        try:
            from tools.voice_io import get_voice

            get_voice().cancel_listen()
        except Exception:
            pass
        set_face("idle")
        self._json(200, {"ok": True, "cancelled": True})

    def _handle_listen(self, data: dict[str, Any]) -> None:
        try:
            from tools.voice_io import get_voice

            get_voice().stop_speaking()
        except Exception:
            pass
        set_face("listening", status="LISTENING // SPEAK NOW")
        _term("Listening…", "magenta")
        try:
            from tools.voice_io import get_voice

            max_sec = float(data.get("max_seconds") or 12)
            voice = get_voice()
            voice.ensure_ready()
            text = voice.listen(
                max_seconds=max_sec,
                speaker_safe=True,
                conversational=True,
            )
            text = (text or "").strip()
            reason = getattr(voice, "_last_listen_reason", "") or (
                "ok" if text else "no_speech"
            )
            if reason == "cancelled":
                text = ""
            set_face("idle")
            if text:
                _term(f"Heard: {text}", "magenta")
            else:
                _term(f"No speech ({reason})", "yellow")
            self._json(
                200,
                {
                    "transcript": text,
                    "ok": bool(text),
                    "reason": reason,
                },
            )
        except Exception as exc:
            # Client often aborts listen when stopping voice — don't double-fault
            if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                logger.debug("HUD listen client aborted: %s", exc)
                set_face("idle")
                return
            logger.exception("HUD listen failed")
            set_face("idle")
            try:
                self._json(
                    200,
                    {"transcript": "", "ok": False, "error": str(exc), "reason": "error"},
                )
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                pass


def _speak_reply(text: str, *, barge_in: bool = False, voice_mode: bool = False) -> None:
    try:
        set_face("speaking")
        from tools.voice_io import get_voice

        voice = get_voice()
        spoken = text
        if voice_mode and len(voice.strip_for_speech(text)) > 280:
            spoken = _voice_summarize(text)
        voice.speak(spoken, brief=True, barge_in=barge_in)
        voice.wait_until_done_speaking(timeout=60.0)
    except Exception as exc:
        logger.debug("HUD TTS failed: %s", exc)
    finally:
        set_face("idle")


def _voice_summarize(text: str) -> str:
    """Compress long answers for speech — short spoken reply."""
    try:
        from core.llm import fast_chat

        summary = fast_chat(
            (
                "Summarize the following Immortility answer for spoken voice. "
                "Max 2 short sentences. No markdown, no lists, no code. "
                "Address Reyansh. Keep the key fact only.\n\n"
                f"{text[:4000]}"
            ),
            history=[],
            system=(
                "You write ultra-short spoken summaries for a voice assistant. "
                "Never invent facts. Never exceed ~40 words."
            ),
            max_output_tokens=90,
        )
        summary = (summary or "").strip()
        if summary:
            return summary
    except Exception as exc:
        logger.debug("voice summarize failed: %s", exc)
    try:
        from tools.voice_io import get_voice

        return get_voice().spoken_brief(text, max_chars=160)
    except Exception:
        return text[:160]


def seed_greeting() -> None:
    with _history_lock:
        if _chat_history:
            return
        _chat_history.append({"role": "assistant", "content": _GREETING})


def _auto_index_if_empty() -> None:
    try:
        from rag.vector_store import VectorStore
        from tools.self_inspect import try_index_self

        chunks = VectorStore().count()
        if chunks > 0:
            _term(f"RAG ready — {chunks} chunks indexed", "green")
            return
        _term("RAG empty — indexing Immortility codebase…", "yellow")
        info = try_index_self()
        vs = (info.get("stats") or {}).get("vector_store") or {}
        _term(
            f"Indexed Immortility — chunks={vs.get('total_chunks', info.get('chunks'))}",
            "green",
        )
    except Exception as exc:
        logger.warning("Auto-index skipped: %s", exc)
        _term(f"Auto-index failed: {exc}", "red")


def start_hud_server(port: int = HUD_PORT) -> str:
    global _server
    if _server is not None:
        return f"http://127.0.0.1:{_server.server_address[1]}"

    seed_greeting()
    token = get_hud_token()
    server = ThreadingHTTPServer(("127.0.0.1", port), _HudHandler)
    _server = server
    thread = threading.Thread(target=server.serve_forever, name="immortility-hud", daemon=True)
    thread.start()
    try:
        from core.llm import warm_llm

        threading.Thread(target=warm_llm, name="llm-warm", daemon=True).start()
    except Exception:
        pass
    try:
        from tools.voice_io import get_voice

        threading.Thread(
            target=lambda: get_voice().warm_stt(),
            name="whisper-warm",
            daemon=True,
        ).start()
    except Exception:
        pass
    threading.Thread(target=_auto_index_if_empty, name="rag-auto-index", daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    _term(f"Serving at {url} (token auth on)", "cyan")
    logger.info("Immortility HUD serving at %s token=%s…", url, token[:6])
    return url


def _find_app_browser() -> list[str] | None:
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = [
        Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    which = shutil.which("msedge") or shutil.which("chrome")
    if which:
        return [which]
    for p in candidates:
        if p.is_file():
            return [str(p)]
    return None


def open_hud(*, port: int = HUD_PORT, greet: bool = True) -> str:
    """Open red face+chat popup; optionally speak Hello Reyansh."""
    url = start_hud_server(port)
    seed_greeting()
    browser = _find_app_browser()
    if browser:
        try:
            subprocess.Popen(
                [
                    *browser,
                    f"--app={url}",
                    f"--window-size={HUD_WIDTH},{HUD_HEIGHT}",
                    "--window-position=60,40",
                    "--disable-features=TranslateUI",
                    "--no-first-run",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.warning("App-window launch failed (%s); browser fallback", exc)
            webbrowser.open(url)
    else:
        webbrowser.open(url)

    if greet:
        threading.Thread(
            target=_speak_reply,
            args=(_GREETING,),
            kwargs={"barge_in": False, "voice_mode": True},
            name="hud-greet",
            daemon=True,
        ).start()
    return url


def stop_hud_server() -> None:
    global _server
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None
