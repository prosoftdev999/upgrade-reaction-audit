# Delegated event continuation

`event_session.jsonl` continues the same incident after sequence 377. The base custom-element capture, the observer continuation, and both earlier contracts remain authoritative. `event_runtime.json` records the delegated listeners installed by the application during the final hot-reload phase.

These event rules describe the captured runtime for this incident. They are intentionally narrower than the full browser event specification.

## Global sequence and ordinary rows

Every row in `event_session.jsonl` has a unique `seq` greater than 377 and shares the same global sequence domain as the earlier capture files.

Rows named `scope_begin`, `scope_end`, `microtask_checkpoint`, `checkpoint`, `set_attr`, `remove_attr`, `insert`, `remove`, `move`, `adopt`, `shared_mutate`, or `shared_replace` keep the meanings defined by the earlier contracts. The shared slots are the same objects introduced by `observer_runtime.json`; they are not reset at sequence 378.

`final_event_snapshot.json` describes DOM state after the complete event continuation.

## Listener installation and lifetime

An `event_runtime_install` row installs every listener declared by the named generation in `event_runtime.json`. `instance_id` identifies that installation.

Each listener resolves its `listen_target` once, at installation. A `uid` target names that concrete node. A `live_role` target is resolved in the controller's current owner document using the role lookup semantics from `runtime_contract.md`. Later role changes, node moves, or document adoption do not change the listener's stored current target.

Listener captures are also resolved once at installation and keep the resulting concrete UIDs. A captured UID does not follow a later role lookup.

Global listener registration order is installation order across all runtime generations. Declaration order inside a generation is preserved.

A listener with `dispose: disconnect` becomes inactive when its runtime instance is disposed. A listener with `dispose: retain` remains active. `signal_abort` permanently deactivates every listener carrying that signal, including retained listeners. Installing a listener after its signal has already been aborted creates it inactive. A `once` listener becomes inactive after its first invocation.

## Shared state inside listeners

A listener that names a `shared_slot` retains the object currently stored in that slot when the listener is installed.

- `captured_revision` and `captured_lane` read that retained object at invocation time.
- `live_revision` and `live_lane` read the object currently stored in the same shared slot at invocation time.

An in-place `shared_mutate` is therefore visible through both references if they still point to the same object. `shared_replace` changes only the live slot; existing listeners keep their older captured object.

## Event path and visible target

A `dispatch_event` row names one concrete `target_uid`, an event type, `bubbles`, `composed`, and an optional detail string.

The event path is frozen when dispatch starts. Moving nodes during a listener callback does not change the remaining current targets for that dispatch.

The frozen path begins at the original target and follows ordinary parents outward. When it reaches a shadow root:

- that shadow root is included in the path;
- if `composed` is false, the path ends there;
- if `composed` is true, the next entry is the shadow host and traversal continues from the host.

A listener on the target side of a shadow boundary sees the original target as `visible_target_uid`. After the path crosses a shadow root, the host of that shadow root becomes the visible target for the host and every farther-out entry. Crossing another shadow boundary repeats the same rule with the next host.

The original target never changes and remains available as `original_target_uid`.

## Listener selection and dispatch order

At dispatch start, the runtime freezes the registrations that are active, have the same `event_type`, and whose stored current target appears on the frozen path. Later DOM movement does not add or remove registrations from that dispatch. A listener that becomes inactive before its own invocation is skipped.

Capture listeners run first, from the outermost path entry toward the original target. Bubble listeners then run from the original target outward. Bubble listeners on the original target run even when `bubbles` is false; farther-out bubble listeners require `bubbles: true`.

When more than one listener for the same phase is attached to the same current target, registration order decides their order.

Each listener callback is its own custom-element reaction frame. DOM changes made by that callback may queue lifecycle reactions. Those lifecycle reactions, including any re-entrant child reactions, are fully dispatched before the next event listener runs. Observer records created by the same DOM changes remain subject to the observer checkpoint rules and are not delivered merely because the event callback returned.

## Listener action targets

Listener actions use the following target forms:

- `original_target`: the event's fixed original target;
- `visible_target`: the target visible at this listener's frozen path entry;
- `current_target`: the listener's stored current target;
- `controller`: the listener's controller UID;
- `capture`: a concrete UID captured when the listener was installed;
- `uid`: the named concrete UID;
- `live_role`: resolve the named role at action time in the controller's current owner document.

A missing target makes that action a no-op.

`set_attr`, `remove_attr`, and `move` use the ordinary DOM and custom-element semantics from `runtime_contract.md`, including lifecycle reactions, component jobs, and observer record generation. `shared_mutate` and `shared_replace` use the shared-slot rules above.

## Event action templates

String templates in event actions can use:

- `{event_id}`
- `{event_type}`
- `{event_detail}`
- `{original_target_uid}`
- `{visible_target_uid}`
- `{current_target_uid}`
- `{current_target_doc}`
- `{controller_uid}`
- `{controller_doc}`
- `{listener_name}`
- `{generation_id}`
- `{captured_revision}`
- `{captured_lane}`
- `{live_revision}`
- `{live_lane}`

A null template value expands to the empty string.

## Queued event jobs

An `enqueue_event` action creates a later event job. Its target is resolved to a concrete UID when the job is enqueued, and its `event_id` and detail template are rendered at that time. Later node movement or role changes do not retarget the job.

Jobs without a key are appended. Keyed jobs are compared only with pending jobs created by the same listener registration and the same rendered key:

- `replace` replaces the existing job in its current queue position;
- `keep_first` leaves the existing job unchanged;
- `none` appends a new job.

An `event_task_checkpoint` freezes the jobs pending at its start and removes them from the pending queue. Those frozen jobs dispatch in queue order using the listeners that are active when each job dispatches. Jobs enqueued while that checkpoint is running wait for a later `event_task_checkpoint`.

Event jobs and component microtasks are distinct queues. Running one queue never implicitly drains the other.

## Output checkpoints

The output artifact keeps the same schema and checkpoint meaning as the earlier capture. A checkpoint reports current custom-element instance state and every lifecycle reaction dispatched since the preceding output checkpoint. Event listener invocations themselves are not output rows; only the custom-element lifecycle consequences they cause appear in `reactions`.
