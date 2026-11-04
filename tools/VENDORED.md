# Vendored tools

These files are copies. Never edit them here: fix the source, then copy the file over and update its line in [`vendored.sha256`](vendored.sha256). CI runs `shasum -a 256 -c tools/vendored.sha256`, so an edit here without a sync fails.

| File | Source | Synced at |
|---|---|---|
| `tools/jarvis-build.py` | [JarvisAIGeorge `tools/jarvis-build.py`](https://github.com/georgewangyu/JarvisAIGeorge/blob/ee8baa34d585ef5027f378135464f56c63ab3fde/tools/jarvis-build.py) | [`ee8baa3`](https://github.com/georgewangyu/JarvisAIGeorge/commit/ee8baa34d585ef5027f378135464f56c63ab3fde) |
| `tools/bitter_lesson.py` | [JarvisAIGeorge `principles-bitter-lesson/scripts/bitter_lesson.py`](https://github.com/georgewangyu/JarvisAIGeorge/blob/ee8baa34d585ef5027f378135464f56c63ab3fde/plugins/jarvis-ai-george/skills/principles-bitter-lesson/scripts/bitter_lesson.py) | [`ee8baa3`](https://github.com/georgewangyu/JarvisAIGeorge/commit/ee8baa34d585ef5027f378135464f56c63ab3fde) |
| `tools/scrub-lint/` (engine, wrapper, public list) | [JarvisAIGeorge `tools/scrub-lint/`](https://github.com/georgewangyu/JarvisAIGeorge/tree/ee8baa34d585ef5027f378135464f56c63ab3fde/tools/scrub-lint) | [`ee8baa3`](https://github.com/georgewangyu/JarvisAIGeorge/commit/ee8baa34d585ef5027f378135464f56c63ab3fde) |

The engine's docstring points to `../SKILL.md`. That contract doc and the engine's test suite are not public yet. Until they are, `scrub-lint <command> --help` is the reference here.
