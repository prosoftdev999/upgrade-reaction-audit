# Recover the custom-element reaction ledger

A frontend platform team lost the lifecycle trace from a regression involving lazy custom-element registration, cross-document adoption, shadow-tree moves, hot-reload observers that survived runtime replacement, delegated event listeners retained across later updates, and render observers spanning geometry changes across subsequent frames.

Reconstruct the trace from the capture under `/app/data` and write the result to:

`/app/output/reaction_ledger.json`

The authoritative behavior contracts are:

- `/app/data/runtime_contract.md` for the custom-element lifecycle capture;
- `/app/data/observer_contract.md` for the observer continuation;
- `/app/data/event_contract.md` for the delegated-event continuation;
- `/app/data/frame_contract.md` for the render-frame continuation;
- `/app/data/hmr_contract.md` for the transactional hot-reload continuation;
- `/app/data/async_trace_contract.md` for the recovered async-stack continuation.

The capture files are:

- `/app/data/documents.json`
- `/app/data/definition_catalog.json`
- `/app/data/dom_capture.jsonl`
- `/app/data/registry_capture.jsonl`
- `/app/data/scheduler_capture.jsonl`
- `/app/data/final_dom_snapshot.json`
- `/app/data/observer_runtime.json`
- `/app/data/observer_session.jsonl`
- `/app/data/final_extension_snapshot.json`
- `/app/data/event_runtime.json`
- `/app/data/event_session.jsonl`
- `/app/data/final_event_snapshot.json`
- `/app/data/frame_runtime.json`
- `/app/data/frame_session.jsonl`
- `/app/data/final_frame_snapshot.json`
- `/app/data/hmr_module_catalog.json`
- `/app/data/hmr_session.jsonl`
- `/app/data/async_bundle_index.map.json`
- `/app/data/async_chunk_maps.json`
- `/app/data/async_runtime_catalog.json`
- `/app/data/async_trace.jsonl`

The manifest files describe the preserved phases of the incident. The HMR continuation starts after the render capture. The async-stack continuation starts after HMR and has no final-state answer snapshot; its sampled callback owners must be recovered from the captured source maps and the historical HMR instance lifetimes. All event streams share one global `seq` domain.

At each output `checkpoint` row across the capture streams, report the custom-element instance state at that point and every lifecycle reaction dispatched since the preceding output checkpoint. Use concrete capture UIDs. Lifecycle ordering, observer delivery, event propagation, render-frame delivery, transactional HMR behavior, async-stack provenance, queueing, and reaction payload semantics come from the six contracts.

The output must be one JSON object with exactly these top-level fields:

- `version`: integer `1`
- `checkpoints`: every output `checkpoint` row exactly once, in global `seq` order

Each checkpoint must contain exactly:

- `checkpoint_id`
- `seq`
- `instances`
- `reactions`

Each `instances` entry must contain exactly:

- `uid`
- `local_name`
- `owner_doc`
- `connected`
- `custom_state`
- `definition_id`

Each `reactions` entry must contain exactly:

- `dispatch_index`
- `dispatch_seq`
- `trigger_seq`
- `uid`
- `definition_id`
- `callback`
- `name`
- `old_value`
- `new_value`
- `old_doc`
- `new_doc`

Preserve contract-defined array order. Use JSON `null` for reaction fields that do not apply.

You have 7200 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
