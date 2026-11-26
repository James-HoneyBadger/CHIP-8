# Contributing to CHIP-8

This independently maintained project welcomes small, verifiable improvements. No support or review timetable is promised.

- Start with the README and technical documentation. Fork and branch from the default branch.
- Describe the problem, reproduction, expected result and affected platform.
- Preserve 1977 and SuperChip dialect differences and firmware provenance. Run `python3 -m unittest discover -s tests -t .`; add opcode or assembler regressions and report skipped display tests.
- Include a regression test where practical. Report commands actually run, results and limitations; update documentation alongside behavior changes.
- Do not submit secrets, private records or material without redistribution rights. Preserve existing license and provenance notices.
- Open a focused pull request. Formal releases remain a separate maintainer decision.

For independent maintenance or a fork, begin with the documented build/test path and repository history, subject to applicable licenses.
