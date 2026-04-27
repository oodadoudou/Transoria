"""End-to-end orchestration: Translation, Glossary Extraction.

This is the only layer permitted to depend on `formats/`, `llm/`, and
`runtime/` together. Lower layers must not import from `workflows/`.
"""
