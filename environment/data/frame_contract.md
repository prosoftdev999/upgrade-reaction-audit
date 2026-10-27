# Render-frame continuation

`frame_session.jsonl` continues the incident after sequence 417. The custom-element, mutation-observer, and delegated-event contracts remain authoritative for the state carried into this phase. `frame_runtime.json` records the reduced layout state and the render-observer generations used by the captured application.

These rules describe the captured runtime for this incident. They model only the resize/intersection behavior needed to reconstruct this trace; they are not a complete browser rendering specification.

## Sequence and carried state

Every row in `frame_session.jsonl` has a unique `seq` greater than 417 and shares the same global sequence domain as the earlier files. Existing DOM nodes, custom-element state, retained mutation observers, event listeners, component microtasks, event jobs, and the `route_bus` / `theme_bus` shared objects continue without reset.

Rows named `set_attr`, `remove_attr`, `shared_mutate`, `shared_replace`, `microtask_checkpoint`, `event_task_checkpoint`, or `checkpoint` keep their earlier meanings.

`final_frame_snapshot.json` is a consistency snapshot for the layout and pending frame-job state after the continuation. It is not a lifecycle answer key.

## Layout state

`frame_runtime.json` gives document viewports and initial layout records. A layout record contains local `x`, `y`, `width`, `height`, scroll offsets, translation offsets, and whether that element clips descendants.

A node's translated border rectangle is positioned relative to its layout parent. For a child of a shadow root, the shadow host is the layout parent; the shadow root itself contributes no separate geometry. Walking toward the document root adds each ancestor's local position and translation, and subtracts each ancestor's scroll offset. Translation changes position only; it does not change the recorded width or height.

A disconnected target has no rendered rectangle. `box_update`, `scroll_update`, and `translate_update` patch the named layout record immediately. Geometry rows do not themselves create custom-element reactions.

## Frame runtime installation and lifetime

A `frame_runtime_install` row installs every render observer declared by that generation. `instance_id` identifies that installation. Global render-observer registration order is installation order across generations and declaration order within one generation.

Observer targets, roots, and captures are resolved once when that observer is installed. A `uid` means that concrete node. A `live_role` lookup uses the controller's owner document and the role lookup semantics from `runtime_contract.md`. Later role changes, moves, or adoption do not retarget an installed observer or any of its captures.

A render observer with `dispose: disconnect` becomes inactive when its runtime instance is disposed. A render observer with `dispose: retain` remains active.

When a render observer names a shared slot, it retains the object stored in that slot at installation. Captured fields read that retained object when a callback runs; live fields read the object currently stored in the slot. In-place mutation is visible through both references when they still share the same object. Replacement changes only the live slot for already-installed observers.

## Resize observations

A resize observer measures the target's recorded `width` and `height`. A disconnected target measures `0 x 0`.

The first render checkpoint after installation produces an entry. Later render checkpoints produce an entry only when width or height differs from that observer's previous render measurement. Translation, scrolling, and clipping do not change a resize measurement.

## Intersection observations

Intersection ratios use integer basis points: `10000` means the full target border rectangle is visible.

The target border rectangle is clipped first by its document viewport, then by every clipping layout ancestor encountered on the shadow-including ancestor path. When the observer has an element root, that root's border rectangle also clips the target and the walk ends at that root. If the installed root is no longer on the target's ancestor path, the visible area is zero. A disconnected target or zero-area target has ratio `0`.

The ratio is `floor(visible_area * 10000 / target_area)`. `is_intersecting` is true exactly when the ratio is greater than zero. A threshold bucket is the number of the observer's listed thresholds whose value is less than or equal to the current ratio.

The first render checkpoint after installation produces an entry. Later render checkpoints produce an entry when either the threshold bucket changes or `is_intersecting` changes. Movement that changes the exact ratio without changing either of those values does not produce an entry.

## Render checkpoints

A `render_checkpoint` is separate from the output `checkpoint` and from the microtask and event-task checkpoints.

At the start of a render checkpoint, the frame jobs already pending are frozen and removed from the pending queue. Those jobs run in queue order. A job's custom-element reactions are fully dispatched before the next frozen job runs.

After those jobs finish, every active render observer takes its measurement from the same resulting layout/DOM state. All entries for that checkpoint are therefore frozen before any render-observer callback runs. Each observer's previous measurement is advanced to that frozen measurement even if another callback later changes DOM attributes or schedules frame work.

Frozen entries are delivered in global render-observer registration order. One observer callback runs as one implicit custom-element reaction frame. Lifecycle reactions caused by its actions, including re-entrant child reactions, finish before the next render-observer callback starts.

MutationObserver records created by render callbacks wait for a later `microtask_checkpoint`. Component microtasks, event jobs, and frame jobs remain separate queues; a render checkpoint does not implicitly drain either of the other queues.

## Frame jobs

A `frame_job` row queues captured application work for a later render checkpoint. The job owner and optional key participate in coalescing. A target written as `live_role` is resolved to a concrete UID when the job is queued, not when it later runs.

Observer callbacks may also enqueue frame jobs. For those jobs, target UIDs, rendered string values, and numeric geometry values are frozen when the callback enqueues the job.

Jobs without a key append. For jobs with the same owner and rendered key while still pending:

- `replace` replaces the earlier job in its existing queue position;
- `keep_first` keeps the earlier job and discards the later one;
- `none` appends another job.

Jobs queued while a render checkpoint is delivering callbacks wait for a later render checkpoint.

A frozen frame job can set an attribute or replace the recorded box, scroll, or translation values named by its action. Attribute writes use the existing custom-element and MutationObserver semantics.

## Render-observer actions

The target forms used by render-observer actions are:

- `observed`: the concrete observed target;
- `controller`: the observer controller;
- `capture`: an install-time concrete capture;
- `uid`: the named concrete UID;
- `live_role`: a role lookup performed when that callback action runs.

A missing target makes the action a no-op.

String templates can use `observer_name`, `generation_id`, `target_uid`, `target_doc`, `root_uid`, `controller_uid`, `controller_doc`, `captured_revision`, `captured_lane`, `live_revision`, `live_lane`, and the measurement fields. Resize entries provide `width` and `height`; intersection entries provide `ratio_bp`, `bucket`, and `is_intersecting`, where the last field renders as `1` or `0`.

## Output checkpoints

The output artifact keeps the existing schema. An output checkpoint reports the current custom-element instance state and every lifecycle reaction dispatched since the preceding output checkpoint. Render-observer entries and frame jobs are not output rows by themselves; only their custom-element lifecycle consequences appear in `reactions`.
