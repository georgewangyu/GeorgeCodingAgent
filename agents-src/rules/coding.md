# Coding rules

- Follow the repo's own conventions, structure and idioms. Read a neighbor before adding a file.
- Reuse before you add. Prefer a new implementation of an existing contract over a new class, base class, factory or helper. Shared code does not branch on a mode flag.
- No magic values, no dead code, no speculative knobs. Small, single-purpose functions with intention-revealing names.
- Comments explain a non-obvious why in a line or two. No commented-out code, no ticket tags in comments.
- Log what an operator needs, parameterized, with the cause on errors. Never log secrets or customer data.
- New branches, methods and error handlers get tests in the same change. One behavior per test; parameterize tests that differ only in inputs. No sleep-then-assert timing tests.
- Run the repo's check command before you call a slice done. Never weaken a threshold, skip a test, or silence a checker to get green; if one must change, say so and why.
- Stay in scope: log unrelated problems you notice instead of fixing them.
- Review every slice with the generator and evaluator loop: write the diff to a patch file, spawn `george-coding-agent:reviewer` with the patch, the slice contract and the checklist (`code-review-and-quality/references/cr-review-checklist.md` for code, `doubt-driven-review/references/design-review-checklist.md` for a spec or plan), fix real findings, re-review until clean, at most three rounds, then a ruling. For a risky slice add a blind pass: a reviewer given only the patch. In Codex (no agent files), run the reviewer as `codex exec --sandbox read-only 'Load $role-reviewer first. Review <patch> against <contract> with <checklist>; reply with the findings.'`, which enforces read-only.
