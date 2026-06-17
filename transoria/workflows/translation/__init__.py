"""Translation workflow public API.

The orchestrator wires `formats/` (parsers + writers), `prompts.py`,
`llm/` (client + decoders), and `runtime/` (cache + executor) into the
end-to-end Translation pipeline described in
`docs/translation-module-design.md`.
"""

from transoria.workflows.translation.chunker import (
    ChunkSegment,
    PreparedSegment,
    TranslationChunk,
    assemble_user_prompt,
    build_chunks,
    format_context_section,
    format_glossary_section,
)
from transoria.workflows.translation.config import (
    BILINGUAL_OUTPUT_FOLDER_EN,
    BILINGUAL_OUTPUT_FOLDER_ZH,
    TranslationConfig,
)
from transoria.workflows.translation.orchestrator import (
    TranslationOrchestrator,
    TranslationRunResult,
)
from transoria.workflows.translation.preprocessor import (
    PreprocessedSegment,
    ProtectionMap,
    postprocess_segment,
    preprocess_segment,
)
from transoria.workflows.translation.rules import (
    Glossary,
    GlossaryEntry,
    ReplacementRule,
    TextPreserveRule,
)
from transoria.workflows.translation.runner import (
    SUBTASK_PAYLOAD_VERSION,
    TranslationSubtaskRunner,
    encode_subtask_payload,
)
from transoria.workflows.translation.confidence import (
    ConfidenceVerdict,
    evaluate_segment_confidence,
)
from transoria.workflows.translation.glossary_report import (
    GLOSSARY_REPORT_FILENAME_JSON,
    GLOSSARY_REPORT_FILENAME_MD,
    GlossaryApplicationRecord,
    GlossaryApplicationReport,
    GlossaryApplicationReportPaths,
    build_glossary_application_report,
    target_term_present,
    write_glossary_application_report,
)
from transoria.workflows.translation.statistics import (
    STATISTICS_FILENAME_JSON,
    FailedFile,
    LowConfidenceSegment,
    TranslationStatistics,
    write_translation_statistics,
)

__all__ = [
    "BILINGUAL_OUTPUT_FOLDER_EN",
    "BILINGUAL_OUTPUT_FOLDER_ZH",
    "ChunkSegment",
    "ConfidenceVerdict",
    "FailedFile",
    "GLOSSARY_REPORT_FILENAME_JSON",
    "GLOSSARY_REPORT_FILENAME_MD",
    "Glossary",
    "GlossaryApplicationRecord",
    "GlossaryApplicationReport",
    "GlossaryApplicationReportPaths",
    "GlossaryEntry",
    "LowConfidenceSegment",
    "PreparedSegment",
    "PreprocessedSegment",
    "ProtectionMap",
    "ReplacementRule",
    "STATISTICS_FILENAME_JSON",
    "SUBTASK_PAYLOAD_VERSION",
    "TextPreserveRule",
    "TranslationChunk",
    "TranslationConfig",
    "TranslationOrchestrator",
    "TranslationRunResult",
    "TranslationStatistics",
    "TranslationSubtaskRunner",
    "assemble_user_prompt",
    "build_glossary_application_report",
    "build_chunks",
    "encode_subtask_payload",
    "evaluate_segment_confidence",
    "format_context_section",
    "format_glossary_section",
    "postprocess_segment",
    "preprocess_segment",
    "target_term_present",
    "write_translation_statistics",
    "write_glossary_application_report",
]
