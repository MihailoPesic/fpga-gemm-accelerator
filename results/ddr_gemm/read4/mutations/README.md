# External read-stop defect detection

Two deliberately broken private RTL copies are detected by the permanent
`external_cancel_preserves_held_ar_and_cancels_pending` test. Production RTL
is unchanged. Each mutation run executes 12 queue tests: 11 pass and the
designated test fails; no test is skipped. These failing XML files are expected
defect witnesses, separate from passing production regressions.

| Mutation | Defect exercised |
| --- | --- |
| `pending_reads_ignore_external_stop` | Accepted, never-offered reads create fresh AR requests after an external fault |
| `external_stop_arrives_one_edge_late` | A cancel on the first promotion edge still offers an AR, leaving a new obligation |

Run `make mutation-read-dma PYTHON=.venv/bin/python` with a fresh build output
directory, or select one with `python scripts/mutation_read_queue.py --build-dir ...`.
The runner applies exactly one literal replacement to a private copy, checks
the designated failure and all test outcomes, and seals unchanged production
inputs. [Summary](summary.json) records the exact replacements, mutated RTL
hashes, failing XML hashes and tool versions; [manifest](manifest.json) binds
the copied evidence.
