"""Voice chat correctness: transcript repair, garbage filtering, voice approvals.

These run offline — no mic, no Whisper weights needed.
"""

import pytest

from core.pending_action import is_approval, is_rejection
from tools.voice_io import VoiceIO
from tools.voice_vocab import hotwords_string, initial_prompt, repair_transcript


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("open you tube", "YouTube"),
        ("open leet code", "LeetCode"),
        ("open lead code", "LeetCode"),
        ("open net flix", "Netflix"),
        ("open netflicks", "Netflix"),
        ("open git hub", "GitHub"),
        ("open yotube", "YouTube"),
        ("open utube", "YouTube"),
        ("hello immortality", "Immortility"),
        ("ask quen a question", "Qwen"),
        ("check wikipeda", "Wikipedia"),
        ("open whats app", "WhatsApp"),
        ("analyze my vector data base", "vector database"),
    ],
)
def test_repair_fixes_domain_mishears(heard, expected):
    assert expected in repair_transcript(heard)


@pytest.mark.parametrize(
    "phrase",
    [
        "tell me about the cold weather today",
        "what is the weather like",
        "read the knowledge folder",
        "i want to hear a story about the old man",
        "stop talking",
        "fix the login bug in auth dot py",
    ],
)
def test_repair_leaves_ordinary_speech_alone(phrase):
    assert repair_transcript(phrase) == phrase


def test_repair_preserves_sentence_casing():
    # Only brands with internal capitals get re-cased
    assert repair_transcript("Open the folder") == "Open the folder"
    assert repair_transcript("open you tube") == "open YouTube"


def test_repair_handles_empty():
    assert repair_transcript("") == ""
    assert repair_transcript("   ") == ""


def test_whisper_prompt_is_short_and_whole_words():
    prompt = initial_prompt()
    # Whisper truncates long prompts; a half-word tail biases decoding badly
    assert 0 < len(prompt) <= 224
    assert not prompt.endswith(("-", "_"))
    assert "Immortility" in prompt


def test_hotwords_include_key_brands():
    hot = hotwords_string()
    for brand in ("Immortility", "YouTube", "LeetCode", "Netflix", "Wikipedia"):
        assert brand in hot


@pytest.mark.parametrize("answer", ["yes", "yes.", "no", "ok", "okay", "yeah", "stop"])
def test_short_answers_survive_garbage_filter(answer):
    """Dropping these would make pending confirmations unanswerable by voice."""
    assert not VoiceIO._is_garbage_transcript(answer)


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "you",
        "thank you for watching",
        "Thanks for watching!",
        "X-Men X-Men X-Men X-Men X-Men X-Men X-Men",
    ],
)
def test_hallucinations_are_dropped(junk):
    assert VoiceIO._is_garbage_transcript(junk)


@pytest.mark.parametrize("said", ["yes", "yes.", "yeah", "yep", "okay!", "sure", "go ahead"])
def test_spoken_approvals_confirm(said):
    assert is_approval(said)
    assert not is_rejection(said)


@pytest.mark.parametrize("said", ["no", "nope", "nah", "cancel.", "abort"])
def test_spoken_rejections_reject(said):
    assert is_rejection(said)
    assert not is_approval(said)


@pytest.mark.parametrize(
    "said",
    ["yes delete it", "ok go ahead and wipe", "yes fix that file", "no idea what that is"],
)
def test_multiword_answers_never_auto_confirm(said):
    """Safety: a sentence containing 'yes' must not approve a pending mutation."""
    assert not is_approval(said)


def test_known_mishear_maps_to_audible_question():
    assert VoiceIO._correct_transcript("Bye Audible!").lower() == "am i audible"


def test_correct_transcript_normalizes_brand_and_name():
    assert "Immortility" in VoiceIO._correct_transcript("Immortality status report")


def test_stop_word_detection():
    assert VoiceIO._transcript_is_stop("stop")
    assert VoiceIO._transcript_is_stop("shut up")
    assert VoiceIO._transcript_is_stop("that's enough")
    assert not VoiceIO._transcript_is_stop("open youtube")
    assert not VoiceIO._transcript_is_stop("")


def test_speech_stripping_removes_markdown_and_code():
    spoken = VoiceIO.strip_for_speech(
        "Here is `code`:\n```python\nprint('x')\n```\nSee [docs](http://x.com) **now**"
    )
    assert "```" not in spoken
    assert "print" not in spoken
    assert "http" not in spoken
    assert "**" not in spoken


def test_spoken_brief_caps_length():
    long_text = "This is a sentence. " * 40
    brief = VoiceIO.spoken_brief(long_text, max_chars=160)
    assert 0 < len(brief) <= 161
    assert brief.endswith((".", "!", "?"))
