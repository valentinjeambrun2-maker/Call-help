"""Métriques de conversation calculées en direct à partir de la transcription."""

from __future__ import annotations

from .models import Metrics, Speaker
from .scenario import normalize

QUESTION_STARTS = (
    "comment", "pourquoi", "combien", "quel", "quelle", "quels", "quelles", "est-ce",
    "qu'est-ce", "ou ", "quand", "qui ", "how", "what", "why", "when", "where", "who",
    "do you", "are you", "is it", "can you",
)

# Deux segments de l'utilisateur séparés de moins de ce délai = même monologue.
MONOLOGUE_GAP = 1.5


def is_question(text: str) -> bool:
    t = normalize(text).strip()
    return t.endswith("?") or t.startswith(QUESTION_STARTS)


class ConversationMetrics:
    def __init__(self) -> None:
        self.words = {Speaker.USER: 0, Speaker.PROSPECT: 0}
        self.talk_time = {Speaker.USER: 0.0, Speaker.PROSPECT: 0.0}
        self.questions_asked = 0
        self.monologue_start: float | None = None
        self.last_user_end = 0.0
        self.last_end = 0.0

    def add_segment(self, speaker: Speaker, text: str, start: float, end: float) -> None:
        duration = max(0.0, end - start)
        self.words[speaker] += len(text.split())
        self.talk_time[speaker] += duration
        self.last_end = max(self.last_end, end)

        if speaker is Speaker.USER:
            if is_question(text):
                self.questions_asked += 1
            if self.monologue_start is None or start - self.last_user_end > MONOLOGUE_GAP:
                self.monologue_start = start
            self.last_user_end = end
        else:
            self.monologue_start = None

    def wpm(self, speaker: Speaker) -> float:
        minutes = self.talk_time[speaker] / 60
        return round(self.words[speaker] / minutes, 1) if minutes > 0 else 0.0

    def current_monologue(self) -> float:
        if self.monologue_start is None:
            return 0.0
        return round(self.last_user_end - self.monologue_start, 1)

    def snapshot(self, now: float) -> Metrics:
        total = self.talk_time[Speaker.USER] + self.talk_time[Speaker.PROSPECT]
        return Metrics(
            elapsed=round(max(now, self.last_end), 1),
            user_wpm=self.wpm(Speaker.USER),
            prospect_wpm=self.wpm(Speaker.PROSPECT),
            user_talk_ratio=round(self.talk_time[Speaker.USER] / total, 2) if total else 0.0,
            current_monologue=self.current_monologue(),
            questions_asked=self.questions_asked,
        )
