"""Profile remember-this vs HUD face-beautify steal."""

from __future__ import annotations

from memory.user_profile import (
    UserProfile,
    wants_remember_about_me,
)
from tools.hud_agent import _wants_face_beautify

RESUME = """
Full-Stack Developer & AI/ML Engineer - shipped a YOLO11L CV pipeline (86.8% mAP@50)
and full-stack systems in FastAPI/React
Built Immortility, an agentic AI assistant, and Mirage
React/TypeScript frontend for image upload, bounding-box visualization
improving results from ~42% baseline confidence to 83.2% precision
this is about me , remember this
"""


def test_resume_is_remember_not_face_beautify():
    assert wants_remember_about_me(RESUME) is True
    assert _wants_face_beautify(RESUME) is False


def test_explicit_hud_face_still_matches():
    assert _wants_face_beautify("beautify your face, make the HUD less scary") is True
    assert _wants_face_beautify("redesign the hud face") is True


def test_frontend_improve_alone_is_not_face():
    msg = "improve the React frontend in InventoryVerification"
    assert _wants_face_beautify(msg) is False
    assert wants_remember_about_me(msg) is False


def test_remember_this_file_is_not_bio():
    assert wants_remember_about_me("remember this file rag/indexer.py please") is False


def test_profile_persists_about_me(tmp_path):
    path = tmp_path / "user_profile.json"
    profile = UserProfile(filepath=path)
    reply = profile.remember_about_me(RESUME)
    assert "Saved" in reply
    assert "YOLO11L" in profile.about_me
    again = UserProfile(filepath=path)
    assert "Mirage" in again.about_me
