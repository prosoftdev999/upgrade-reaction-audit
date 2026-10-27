# Transactional HMR continuation

`hmr_session.jsonl` continues the same frontend incident after sequence 502. The DOM, custom-element instances, mutation observers, delegated listeners, render observers, pending jobs, layout state, and shared `route_bus` / `theme_bus` objects all carry forward unchanged from the earlier phases.

`hmr_module_catalog.json` is the captured application-module metadata for this continuation. It describes the active revision at the start of the phase, each revision's dependency edges and acceptance declarations, and the runtime work installed by that revision. These records are application evidence, not expected output.

## Module revisions and dependency identity

A module has one active revision at a time. `imports` names direct dependency modules. The graph used to decide whether an update is accepted is the graph of **currently active revisions before that transaction starts**. A transaction never uses a proposed revision's new import edges to justify accepting that same transaction.

`self_accept` means the currently active revision accepts replacement of its own module. `accepts` lists direct imported modules whose updates that current revision accepts. Self-acceptance does not imply acceptance of dependencies.

The only HMR entry module in this capture is listed in `entries`. Reaching an entry while propagating an unaccepted dependency update is an unaccepted path.

## Bootstrap record

The single `hmr_bootstrap` row starts this continuation. At that row, instantiate every module named by `initial_revisions`; there is no previously active HMR module instance to dispose and no accept callback at bootstrap. Each initial revision installs its declared runtime registrations, resolves its install-time captures and retained shared-object reference, initializes its private state, and then runs its `install_actions` as one implicit custom-element reaction frame with template field `phase` equal to `bootstrap`. Re-entrant lifecycle reactions from one initial module finish before the next initial module is installed.

Bootstrap installation order is part of the captured loader semantics. For this capture the initial modules install in exactly this order:

`telemetry`, `bridge`, `metrics`, `route`, `panel`, `theme`, `shell`

That order is dependency-respecting and is authoritative when bootstrap callbacks or their retained registrations make later behavior order-sensitive. No other ordering of simultaneously installable initial revisions is equivalent for this capture.

## Update transactions

An `hmr_transaction` names one or more proposed module revisions in `changes`. Treat the transaction atomically.

For each changed module, its current revision is an acceptance boundary when `self_accept` is true. Otherwise the update propagates to every active importer. An importer is a boundary for that path only when its current revision lists the immediately propagated child module in `accepts`; otherwise the propagation continues through that importer. Every path from every changed module must end at a boundary. If any path reaches the entry without an accepting boundary, or has no importer before reaching an entry-level dead end, the whole transaction is rejected and changes no runtime state.

A rejected transaction does not run module dispose, install, or accept actions. Its transaction ID is still consumed. A later transport replay with the same `tx_id` is ignored whether the first copy committed or was rejected.

For an accepted transaction:

- changed module instances are disposed using the pre-transaction dependency graph, with importers before changed dependencies;
- the proposed revisions then become active and are installed using the new graph, with changed dependencies before changed importers;
- after all changed revisions are installed, one accept callback runs for each accepting boundary module, ordered by module ID;
- a self-accepting changed module receives its accept callback on the newly installed instance; a different boundary module receives it on the instance active after the transaction.

The accept callback's `accepted` template field is the comma-separated sorted set of changed root module IDs whose propagation ended at that boundary.

## Module instance lifetime

Installing a revision creates a new concrete module instance. Its `controller_uid`, install-time captures, retained shared-object reference, local state, and runtime registrations belong to that concrete instance and are not transferred to a later revision.

A capture with `live_role` is resolved once when that module instance is installed, using the controller's owner document and the role lookup rules from `runtime_contract.md`. Later moves, adoption, or `data-role` changes do not retarget the capture. Action targets written as `live_role` are resolved when that action runs.

When a revision names `shared_slot`, the instance retains the object currently stored in that slot at installation. `captured_revision` / `captured_lane` read that retained object. `live_revision` / `live_lane` read the object currently stored in the slot. In-place mutation is visible through both references while they still refer to the same object; replacement only changes the live slot for already-installed instances.

`initial_state` is private state for that module instance. `bump_state` changes only that instance. Template fields named `state_<key>` use the current value after all earlier actions in the same callback.

A disposed module instance no longer owns the active module revision. Its signal handler is disconnected unless that revision says `signal_lifetime: retain`. A retained signal handler keeps its original captures, local state, and captured shared-object reference.

## Installed runtime registrations

A revision's `runtimes` entries install the named mutation-observer, event-listener, or render-observer generation from the earlier runtime files. Each module instance owns its own runtime installation even when another module installed the same generation.

When the module instance is disposed, those runtime installations receive their normal disposal operation. Their own contracts still decide which registrations disconnect and which survive. A retained observer or listener therefore can outlive both the module instance that installed it and later HMR revisions.

## Module callbacks and actions

Bootstrap, dispose, install, accept, and signal callbacks each run as one implicit custom-element reaction frame. Lifecycle reactions caused by one callback, including re-entrant reactions, finish before the next module callback begins.

The action records in the module catalog use these meanings:

- `bump_state`: increment the named module-instance state value;
- `set_attr` / `remove_attr`: use the existing custom-element and mutation-observer semantics;
- `shared_mutate` / `shared_replace`: use the shared-object semantics above;
- `set_box`, `set_translate`, `set_scroll`: patch the existing render layout record exactly as the corresponding frame continuation operation does.

Action targets are `controller`, an install-time `capture`, a concrete `uid`, or `live_role`. A missing target makes that action a no-op.

String templates may use `module_id`, `revision_id`, `instance_id`, `controller_uid`, `controller_doc`, `captured_revision`, `captured_lane`, `live_revision`, `live_lane`, `phase`, any `state_<key>`, and callback-specific fields. Dispose and accept callbacks provide `tx_id`; accept callbacks also provide `accepted`. Signal callbacks provide `signal_id`, `signal_topic`, and `payload`.

## Signals and existing queues

An `hmr_signal` invokes every still-active signal handler for that topic in concrete module-instance installation order, including retained handlers from disposed instances.

Rows named `set_attr`, `remove_attr`, `shared_mutate`, `shared_replace`, `dispatch_event`, `microtask_checkpoint`, `event_task_checkpoint`, `render_checkpoint`, `box_update`, `scroll_update`, `translate_update`, and `checkpoint` keep their meanings from the earlier contracts. The HMR phase does not merge those queues or introduce an extra implicit drain.

## Output checkpoints

The output artifact keeps the existing schema. Each `checkpoint` reports current custom-element instance state and lifecycle reactions dispatched since the preceding output checkpoint. HMR transactions, module callbacks, observer deliveries, delegated-event invocations, and render entries are not output rows by themselves; only their custom-element lifecycle consequences appear in `reactions`.

This continuation intentionally has no final-state answer snapshot. Correctness must come from the captured module graph, HMR stream, and the earlier runtime evidence.
