# summarization-claim-audit

An offline static auditor that compares promises about condensing long text with Python source fingerprints. Version 0.1.0 uses only the standard library at runtime and never executes inspected code.

Install with `python3 -m pip install .`. Requires Python 3.9 or later.

```sh
summarization-claim-audit /path/to/project
python3 -m summarization_claim_audit /path/to/project --json
python3 -m summarization_claim_audit --version
python3 -m unittest discover
```

The auditor reads root README.md and Markdown files beneath any docs directory. It skips fenced blocks, matches normalized paragraphs, and checks negative phrases before positive phrases. Inline code is included. For example, these phrases trigger checks when used in documentation prose:

```text
summarizes text
does not support summarization
```

Recognized source fingerprints come from Transformers task pipelines and specialized pipeline constructors, invoked sumy objects, and the gensim condensation API. Imports, strings, slicing, external commands, and unrelated language operations do not establish evidence. Resolution is static within each module; local definitions override imports. This is a deliberately bounded heuristic, not proof of runtime behavior or output quality.

Symlinks are skipped. Python files under fixtures, docs, and examples do not supply evidence. Files under tests, named test_*.py, or named *_test.py supply test evidence only. Invalid Python supplies no evidence. A positive promise without production evidence produces HIGH severity, or MEDIUM when only tests supply evidence. A negative promise contradicted by production evidence produces HIGH. Each family produces at most one finding. No promises means PASS, including projects without Python files.

Options:

- `--json`: emit a JSON object with status, findings, matched claims, evidence paths, and Python file count.
- `--verbose`: include detailed inspection results in text output.
- `--ignore GLOB`: repeatable exclusion matched against relative paths and individual path components; ignored directories are pruned.
- `--fail-on HIGH|MEDIUM`: default HIGH; MEDIUM also fails for test-only findings.
- `--version`: print 0.1.0.

Exit codes are 0 for PASS or findings below the selected threshold, 2 for findings meeting the threshold, and 1 for usage or input errors. JSON status is FAIL whenever there are findings, even if the threshold permits exit 0.

Build with `python3 -m build`. The wheel and source archive contain the package and MIT license. The Python API exposes `audit(root, ignore=())`, returning the same object emitted by the JSON command.
