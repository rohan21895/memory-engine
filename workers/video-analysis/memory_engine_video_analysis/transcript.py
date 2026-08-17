"""Word-level transcript: the interface `moments.py` needs, and an honest null.

THERE IS NO SPEECH MODEL HERE AND NOTHING PRETENDS THERE IS.

`moments.py` uses word timings for one thing above all others, and it is the
thing this project spent a day fixing: NO CUT MAY LAND INSIDE A WORD. Every
snap point that falls strictly inside a word is discarded before the planner
sees it; a fallback boundary that lands inside one is pushed out to the next
whole frame past it; `safe_trim.speech_safe_in/out` are derived from the same
timings and are asserted to agree with the emitted bounds.

All of that machinery is only as good as the word timings. A FABRICATED
transcript — evenly spaced pseudo-words, a voice-activity guess dressed up as
words, an empty word list emitted for audio that plainly contains speech — does
not degrade that guarantee, it INVERTS it: the planner would cut confidently on
positions certified against words that were never spoken, and
`safe_trim.preserve_audio_tail` would positively assert the cut was safe.

So the null backend reports UNAVAILABLE with a reason, and
`moments.py` handles an absent transcript correctly and already does:

  * `stream.words` empty  -> `_speech_snaps` returns nothing, so no speech
    boundary is certified and the planner falls back to motion, audio and shot
    snaps;
  * `_covering_word` finds nothing -> no snap is discarded as mid-word, which
    is correct, because with no transcript nothing is KNOWN to be mid-word;
  * `stream.language` None -> `_transcript` returns None and no
    TranscriptSegment is written. The language field is BCP-47 and required, so
    defaulting it to "en" would mislabel a Hindi moment in the database
    forever.

The honest consequence, stated so it is not discovered later: WITHOUT A
TRANSCRIPT, THE NO-MID-WORD GUARANTEE IS VACUOUS. It is not violated — nothing
claims a cut is speech-safe — but nothing is checked either. A moment planned
from a null-transcript stream may well cut through a sentence. That is a
missing producer, and it belongs in the report a run prints, which is why
`TranscriptResult.reason` is a sentence rather than a flag.

WIRING A REAL BACKEND
`faster-whisper large-v3-turbo` (CTranslate2) is the build plan's choice, with
AI4Bharat IndicWhisper variants for Indian languages. Implement `transcribe`
against this Protocol, return words in SECONDS, and `to_words()` converts them
onto the stream's frame grid. Word times are deliberately NOT snapped to frames
— rounding a word boundary outward by half a frame is exactly the error that
clips a consonant, and `moments.Word` stores real-valued times for that reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "NullTranscriptBackend",
    "TimedWord",
    "TranscriptBackend",
    "TranscriptResult",
]


@dataclass(frozen=True, slots=True)
class TimedWord:
    """One word with its timing IN SECONDS, as a speech model reports it."""

    word: str
    start_s: float
    end_s: float
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """What a backend produced, including the case where it produced nothing.

    `available` False with `words` non-empty is a contradiction and is refused
    in `__post_init__`: a result that says "no transcript" while carrying words
    would be read one way by a reporter and the other way by the assembler.
    """

    available: bool
    reason: str = ""
    language: str | None = None
    words: tuple[TimedWord, ...] = ()
    model_id: str = ""
    model_version: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.available and self.words:
            raise ValueError(
                "a transcript that reports itself unavailable cannot also carry "
                "words; one of the two is what downstream believes"
            )
        if self.available and self.language is None:
            raise ValueError(
                "an available transcript must name its language: "
                "TranscriptSegment.language is a required BCP-47 tag, and "
                "defaulting it would mislabel the moment in the database"
            )
        for word in self.words:
            if not (word.end_s >= word.start_s):
                raise ValueError(
                    f"word {word.word!r} ends at {word.end_s} before it starts at "
                    f"{word.start_s}; a negative-length word makes the mid-word "
                    "test unable to contain anything"
                )

    def to_words(self, *, rate: float, start_value: float = 0.0) -> tuple[Any, ...]:
        """Convert to `moments.Word`, whose times are in stream rate units.

        `start_value` is the source frame index of sample 0, so a word at t
        seconds into the proxy sits at `start_value + t * rate` — the same
        frame space every other time in the stream uses. Getting this wrong by
        the start offset would move every word relative to the picture, which
        is undetectable from the record and audible in the cut.
        """
        from memory_engine_story.moments import Word  # noqa: PLC0415

        if not (rate > 0):
            raise ValueError("rate must be positive to place words on the frame grid")
        return tuple(
            Word(
                word=word.word,
                start=start_value + word.start_s * rate,
                end=start_value + word.end_s * rate,
                confidence=word.confidence,
            )
            for word in sorted(self.words, key=lambda w: (w.start_s, w.end_s, w.word))
        )


class TranscriptBackend(Protocol):
    """A speech-to-text producer.

    Takes the proxy — analysis never opens a source file — and returns word
    timings in seconds relative to the proxy's audio timeline.
    """

    model_id: str
    version: str

    def transcribe(self, proxy_path: str, *, language: str | None = None) -> TranscriptResult:
        ...


class NullTranscriptBackend:
    """Reports that no transcript is available, and why. Never invents one."""

    model_id = "null-transcriber"
    version = "1.0.0"

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason or (
            "no speech-to-text model is wired: faster-whisper runs in the model "
            "host (workers/ml-runtime) and this worker does not speak to it. "
            "Word timings are therefore unknown, so no cut in this stream can be "
            "certified speech-safe — not unsafe, unknown."
        )

    def transcribe(
        self, proxy_path: str, *, language: str | None = None
    ) -> TranscriptResult:
        del proxy_path, language  # a null backend reads nothing and guesses nothing
        return TranscriptResult(
            available=False,
            reason=self._reason,
            model_id=self.model_id,
            model_version=self.version,
        )


def transcribe(
    proxy_path: str,
    *,
    backend: TranscriptBackend | None = None,
    language: str | None = None,
) -> TranscriptResult:
    """Run a backend, defaulting to the null one.

    The default is explicit rather than implicit: a caller that forgets to pass
    a backend gets a result that SAYS there is no transcript, not an empty word
    list that looks like a video with no speech in it.
    """
    return (backend or NullTranscriptBackend()).transcribe(
        proxy_path, language=language
    )


def words_are_sorted_and_disjoint(words: Sequence[TimedWord]) -> bool:
    """Diagnostic for a real backend's output. Overlap is legal, not an error.

    Diarised output from two speakers legitimately nests one word inside
    another, and `moments._covering_word` walks backwards with an exact
    stopping rule precisely because of that. This exists so a backend author
    can see which case their model produces, not to reject anything.
    """
    ordered = sorted(words, key=lambda w: (w.start_s, w.end_s))
    return all(
        ordered[index].start_s >= ordered[index - 1].end_s
        for index in range(1, len(ordered))
    )
