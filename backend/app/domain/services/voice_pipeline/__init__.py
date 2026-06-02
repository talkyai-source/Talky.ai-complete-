"""voice_pipeline package — cohesive pieces extracted from the
``voice_pipeline_service`` god-file (roadmap item 2, strangler-fig).

Leaf-level, pure helpers live here; ``VoicePipelineService`` composes
them. Keeping these framework-free and stateless makes them trivially
unit-testable and safe to call on the real-time audio path (they add only
a plain function call, no I/O).
"""
from app.domain.services.voice_pipeline.sentence_segmentation import (  # noqa: F401
    CLAUSE_CONJUNCTIONS,
    COMMON_ABBREVIATIONS,
    find_sentence_end,
    is_terminal_period_boundary,
)
from app.domain.services.voice_pipeline.transcript_heuristics import (  # noqa: F401
    is_repetitive_transcript,
)

__all__ = [
    "find_sentence_end",
    "is_terminal_period_boundary",
    "is_repetitive_transcript",
    "CLAUSE_CONJUNCTIONS",
    "COMMON_ABBREVIATIONS",
]
