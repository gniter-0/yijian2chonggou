# Operator Runbook — exam-card-engine v1.0.0

## 1. Purpose

This document is an operator guide for the frozen `exam-card-engine` v1.0.0
production workflow. It explains how to run the existing pipeline, handle its
human checkpoints, resume an interrupted run, and investigate delivery states.

This document does not define authority, schemas, identities, checkpoints, or
new workflow behavior. The runtime implementation and its frozen contracts are
authoritative; this runbook only describes how to use them safely.

## 2. Architecture overview

The frozen authority chain is:

```text
Admission
    ↓
Review
    ↓
Release Closure
    ↓
Publication Authority
    ↓
Delivery Authority
    ↓
Filesystem Delivery
    ↓
Operator CLI
```

Ownership is intentionally separated:

- **P5 — Publication Authority:** freezes the publication decision and the
  publication package identity after the required human authorization.
- **P6 — Delivery Authority:** creates and resumes delivery execution using
  the frozen publication identity, including recovery and reconciliation of
  completed, failed, and unknown outcomes.
- **P7 — Filesystem Delivery:** materializes the authorized package at the
  validated filesystem destination and records the delivery result.
- **P8 — Operator CLI:** accepts operator inputs and invokes the existing
  runtime flow. It does not make publication or delivery decisions and does not
  replace any authority boundary.

Admission, review, Step3, Step4, and release closure must complete through the
existing pipeline before publication can be finalized. Human approval remains
an explicit checkpoint between publication materialization and publication
freezing.

## 3. Normal operation flow

1. Start the live pipeline for the intended exam and requested stage set.
2. Let the existing admission flow establish the Step2b admission result.
   Resolve any human review checkpoint using the files and instructions
   emitted for that run.
3. Continue through Step3 and Step4. The run's existing release-closure gate
   must freeze the release before publication is considered.
4. At the P5 publication checkpoint, inspect the emitted frozen publication
   manifest. The normal waiting state means that a manifest is ready and
   explicit human authorization is still required; it is not a successful
   publication.
5. Supply the complete human-created publication authorization through the
   existing resume flow. Do not edit the manifest or alter any identity or
   hash to make authorization pass.
6. Resume the same run. P5 validates the authorization against the frozen
   publication material and freezes the publication receipt.
7. P6 creates or resumes delivery from that frozen P5 identity. P7 writes to
   the validated filesystem destination and persists the delivery receipt or a
   fail-closed status requiring reconciliation.
8. Confirm completion from the run report and the persisted delivery receipt.
   A destination directory by itself is not a substitute for the authoritative
   run state and receipt.

## 4. Human checkpoint procedure

### Files and information supplied to the operator

The operator should use only the files and run information emitted by the
current run, including:

- the run identifier and the resume context;
- the publication checkpoint's `publication-manifest-v2.json` when P5 is
  waiting for authorization;
- the human authorization request and the complete authorization JSON to be
  supplied through the supported resume input; and
- the final run report and delivery receipt when execution completes.

The exact files present are determined by the existing checkpoint and CLI
flow. Do not create substitute checkpoint files or infer missing authority
data.

### Allowed actions

The operator may:

- review the proposed release and publication material;
- make the human authorization decision and provide the resulting
  authorization input through the existing flow; and
- resume the existing run using its existing run identifier and checkpoint.

### Forbidden actions

The operator must not:

- edit a frozen manifest, artifact, receipt, hash, or identity;
- replace artifact bytes or rebuild a checkpoint by hand;
- create a new delivery identity to work around an error;
- bypass the human authorization gate; or
- change the frozen delivery destination.

## 5. Resume procedures

Every resume is bound to the **same run**, the **same identity**, and the
**same frozen checkpoint**. A resume is a continuation of an existing run,
not a new publication or delivery request.

### Publication resume

1. Preserve the run directory and its publication checkpoint.
2. Review the manifest and make the human decision outside the frozen
   material.
3. Supply the complete human-created `PublicationAuthorizationV2` JSON using
   the existing `--publication-authorization` input.
4. Resume with the original exam, stage list, and run identifier.
5. Stop if the runtime reports manifest, identity, or authorization binding
   drift. Do not edit the checkpoint to clear the error.

### Delivery resume

1. Confirm that P5 publication is already frozen for the same run.
2. Use the original run identifier and the originally frozen destination.
3. Resume through the existing filesystem delivery input.
4. Allow P6 to reuse the existing delivery identity and query the adapter when
   the previous execution was incomplete or uncertain.
5. Treat a successful persisted receipt as the completion proof. If the
   result remains unknown, stop normal progression and reconcile it.

## 6. Recovery procedures

### Interrupted publication

**Symptom:** A publication checkpoint exists and is waiting for human
authorization, or the process stopped before the publication receipt was
frozen.

**Safe action:** Complete the human authorization using the existing request
and resume the same run.

**Forbidden action:** Create a new run, delete or edit the checkpoint, or
modify the publication manifest, hashes, or identities.

### Completed delivery with a missing receipt

**Symptom:** The destination appears to contain delivered material, but the
authoritative run receipt is missing.

**Safe action:** Preserve the destination and the run directory and use the
existing reconciliation path to determine the outcome for the same delivery
identity. Escalate if reconciliation cannot establish a safe result.

**Forbidden action:** Start a new delivery attempt, create a new identity, or
declare success from destination contents alone.

### Unknown delivery

**Symptom:** The delivery outcome is `unknown`; this is not equivalent to a
confirmed failure.

**Safe action:** Reconcile by querying the same delivery identity and preserve
the frozen publication and destination bindings.

**Forbidden action:** Change the identity, change the package, or issue a new
delivery request before reconciliation resolves the original state.

### Destination mismatch

**Symptom:** The supplied destination does not match the frozen destination,
is unsafe, or resolves unexpectedly.

**Safe action:** Stop and investigate the path and the run's frozen bindings.
Use the original validated destination only after the mismatch is resolved by
the supported workflow.

**Forbidden action:** Override destination validation, follow a symlink as a
workaround, or rewrite the checkpoint to accept a different destination.

### Failed resume

**Symptom:** Resume fails because a binding, authorization, receipt, or
checkpoint state is invalid.

**Safe action:** Keep the original run and checkpoint intact, capture the
reported failure, verify that the same run, identity, authorization input,
and destination are being used, then follow the applicable recovery path.

**Forbidden action:** Re-run under a new identity, edit internal state, replace
artifacts, or bypass the failed validation.

## 7. CLI usage

The following examples use only options exposed by
`migration/run_exam_pipeline.py`. Values in angle brackets are operator
inputs; they are placeholders and must not be typed literally. Consult the
existing CLI help/source for the current required values.

### Initial run

```bash
uv run python migration/run_exam_pipeline.py \
  --exam <exam> \
  --stages <comma-separated-stage-list> \
  --allow-live \
  --enable-publication
```

The command may exit with the normal human-checkpoint pause. A pause is an
expected control state, not proof of failure; follow the emitted checkpoint
instructions.

### Publication resume

```bash
uv run python migration/run_exam_pipeline.py \
  --exam <exam> \
  --stages <comma-separated-stage-list> \
  --allow-live \
  --resume <run-id> \
  --enable-publication \
  --publication-authorization <authorization-json>
```

The authorization file is human-created input. It must bind to the existing
frozen publication material; it is not a replacement for that material.

### Delivery resume

```bash
uv run python migration/run_exam_pipeline.py \
  --exam <exam> \
  --stages <comma-separated-stage-list> \
  --allow-live \
  --resume <run-id> \
  --delivery-filesystem-root <destination-root>
```

The filesystem destination option is a resume-only input in the existing CLI.
Use the destination already bound to the run. There is no separate operator
command in this runbook for inventing a delivery retry or for overriding an
unknown state; use the existing resume and reconciliation behavior.

## 8. Troubleshooting

| Problem | Cause | Safe action | Forbidden action |
| --- | --- | --- | --- |
| Publication is waiting for a human | The P5 manifest is ready but explicit authorization is incomplete | Review the manifest, provide complete authorization, and resume the same run | Treat the pause as success, edit the manifest, or skip authorization |
| Authorization is rejected | The supplied authorization is invalid or does not bind to the frozen publication material | Re-check the human decision and resubmit through the supported input without changing frozen material | Edit hashes, identities, or the checkpoint to force a match |
| Resume reports identity or manifest drift | A frozen file, binding, or run identity no longer matches | Stop and investigate; preserve the run and escalate if needed | Create a new identity or replace the frozen artifact |
| Delivery is unknown | External or local outcome cannot yet be established | Reconcile using the same delivery identity and destination | Start a second delivery attempt |
| Destination mismatch or unsafe path | Destination drift, symlink use, or path validation failure | Stop and verify the original frozen destination | Override validation or redirect the run |
| Destination has files but receipt is missing | Materialization may have completed without an authoritative persisted receipt | Preserve the destination and reconcile the original delivery | Declare success from directory contents or deliver again |
| Resume fails repeatedly | The run state, authorization, destination, or receipt remains invalid | Keep the original checkpoint and use the error-specific recovery path | Start a replacement run or edit internal state |

## 9. Safety rules

The following rules are mandatory:

- Never make manual hash changes.
- Never make manual identity changes.
- Never replace frozen artifact bytes.
- Never bypass the human authorization gate.
- Never override frozen destination validation or allow destination drift.
- Never delete or rewrite checkpoints to make a resume pass.
- Never treat an unknown delivery as a confirmed failure or success.
- Never start a duplicate delivery while the original delivery identity is
  unresolved.

## 10. Validation

Runtime authority remains in the implementation and its frozen schemas and
checkpoint contracts. This document does not change execution, authority,
identity, or delivery behavior.

Validation of this document consists of confirming that:

- every CLI flag shown is exposed by `migration/run_exam_pipeline.py`;
- the documented paths and filenames are emitted by the existing checkpoint
  flow;
- the ownership descriptions match P5, P6, P7, and P8 responsibilities;
- no section introduces a new authority, schema, checkpoint, workflow, or
  manual override; and
- the change is limited to this documentation file, with no source or test
  mutations.
