# Developer CLI boundaries

`runtime.py` boots Mission Control and reports its durable Mission outcome.
`resume.py` owns authenticated checkpoint metadata preflight. Its public
`ResumeExecutionPlan` and gate functions remain available through `runtime.py`,
including the existing monkeypatch seams.
`server_environment.py` constructs the subprocess environment, including the
explicit resume target; `runtime.build_server_env` remains a compatibility import.

Execution resume requires a nonterminal protocol-v2 checkpoint, an unchanged
checkpoint anchor in the lease-fenced projection, and the original live lease
owned by the same Runner and attempt. An expired or foreign lease is refused
without recovery or acquiring another attempt. Protocol-v1 and NULL checkpoints
remain readable for diagnostics but cannot produce an executable resume image.
The CLI never constructs a `HarnessResumeInput`, invents a recovered tool result,
or treats a receipt's success label as proof. The production claimed-work resolver
and desktop Harness restore the complete private image, context and receipt bytes
before any model or tool call. Digest-only v2 next actions are valid metadata.
If the worker has already completed, the CLI observes the same durable Mission
instead: terminal outcomes are reported directly, and a completed `VERIFYING`
attempt waits for independent verification. One metadata reread handles a
completion that races preflight; this observation starts no execution.

An execution `--resume` passes its exact Mission to the server before polling
starts. The optional `resumeMissionId` claim fence accepts only that Mission's
existing, live, same-owner lease. It cannot start a PENDING sibling or another
Mission, acquire a new attempt, or recover an expired lease. Compact chat
context retains ordinary polling. Ambient resume-target environment variables
are cleared unless the CLI explicitly supplies the target.

`control_state.py` places database, Artifact data, logs and generated project
instructions under `runner_state_directory(workspace)/control-state`, outside
the writable workspace. New attempt snapshots are also outside that workspace.
User configuration, project facts and conversation history retain their
workspace-local `.agenthub` paths.

On the first boot, an existing `.agenthub/db/agenthub.db` is copied using SQLite
backup and an integrity check, including committed WAL rows. Artifact data is
copied and byte-verified before an atomic directory rename; the old files remain.
Migration refuses links, incomplete inventories, corrupt databases, more than
20,000 entries or 1 GiB, and concurrent or changed sources. A migration marker
binds the private state to its retained source. Existing unmarked or conflicting
private state is never overwritten. Keep the retained source unchanged until an
operator explicitly reconciles a conflict or retires that migration marker.
`doctor_storage.py` and history commands discover the same private database.

Workspace fingerprints cover writable files. Changes to facts, conversation,
transaction journals or other workspace bytes participate in recovery validation;
they are not silently excluded. Ordinary CLI execution reads facts/configuration
and appends chat history outside the running attempt. Concurrent edits can refuse
resume. Moving control state avoids self-generated database/log drift; it is not
an OS sandbox. In particular, a general `code_execute` capability can access files
allowed by the process account, so path-specific tool rules cannot guarantee
protection against arbitrary executed code.
Stopping the local server preserves workspace execution scratch and transaction
journals, including unfinished attempts. They are execution evidence, rather
than disposable diagnostic logs; shutdown must not alter an admitted recovery
fingerprint. The historical cleanup helper remains an explicit compatibility API.

Verification: `tests/cli/test_resume_preflight.py`,
`test_resume_receipt_integration.py`, `test_control_state.py`, the full CLI suite,
and `AGENTHUB_CLI_E2E=1` for real Mission Control/mock-provider flow checks.
