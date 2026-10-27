# Runtime observer continuation

`observer_session.jsonl` continues the same incident after global sequence 273. The base capture files and `runtime_contract.md` remain authoritative for all earlier behavior and for custom-element reactions in the continuation. `observer_runtime.json` describes the observer generations that the application runtime installed during hot replacement.

The continuation adds one more source of DOM mutations: reduced MutationObserver-like runtime callbacks. These rules are specific to this capture. They define what the recorded runtime did; they are not a claim about every browser implementation.

## Sequence and ordinary records

Every row in `observer_session.jsonl` has a unique `seq` greater than 273. Those rows follow the same global sequence as the base capture. Rows named `scope_begin`, `scope_end`, `microtask_checkpoint`, `checkpoint`, `set_attr`, `remove_attr`, `insert`, `remove`, `move`, or `adopt` use the corresponding rule from `runtime_contract.md`.

The base `final_dom_snapshot.json` describes state after sequence 273. `final_extension_snapshot.json` describes state after the continuation.

## Runtime generations and shared slots

`observer_runtime.json` contains `shared_slots` and named runtime `generations`.

A `runtime_install` row installs every observer declared by that generation. The row's `instance_id` distinguishes that installation from other generations. Observer registration order is the order in which observer declarations are installed across the session; it is never recomputed from DOM order.

A `shared_mutate` row updates fields on the existing object stored in the named slot. Observer instances that already captured that object see the updated fields.

A `shared_replace` row replaces the slot with a new object. Existing observer instances keep their previously captured object. Later installations capture the replacement. Program templates can therefore observe two versions at delivery time:

- `captured_*` fields come from the object captured when that observer instance was installed;
- `live_*` fields come from the object currently stored in the same shared slot.

The continuation uses the `revision` and `lane` fields from those objects.

## Installing observers

Each observer declaration names a `controller_uid`, an `observe_target`, a `subtree` flag, an `attribute_filter`, optional captures, a shared slot, disposal behavior, and callback rules.

An observer's root is resolved once, when the generation is installed:

- target kind `uid` names that concrete UID;
- target kind `live_role` resolves the first matching `data-role` in the controller's current owner document using the role lookup rules from `runtime_contract.md`.

The resolved root is a concrete UID and does not change if the root or controller later moves or is adopted.

Observer captures are also resolved once at installation. A capture of kind `uid` stores that concrete UID. The stored UID is not re-resolved later.

Each observer captures the object currently stored in its declared shared slot.

If an installation cannot resolve its observer root, that observer is not installed. A missing captured target is stored as null, and actions aimed at that capture are no-ops.

## Matching attribute mutations

Only actual attribute value changes produce observer records. Setting an attribute to its existing value produces none.

A record contains the concrete mutation target UID, attribute name, old value, new value, and the trigger sequence of the mutation. Removing an attribute uses `new_value = null`.

An active observer receives a record when all of these are true:

- the attribute name is in its `attribute_filter`;
- the mutation target is its concrete observed root, or `subtree` is true and the target is a shadow-including descendant of that root at the moment of mutation.

Shadow-including ancestry follows light-DOM parents and crosses a shadow root through its host, matching the topology rules in `runtime_contract.md`.

Records are appended in mutation order. They are not coalesced. One mutation may enqueue records for several observers.

Observer callbacks, component jobs, lifecycle callback programs, and caller-originated `set_attr`/`remove_attr` rows all use the same attribute-mutation rule. An observer therefore sees qualifying program-driven changes as well as qualifying caller-driven changes.

## Disposal and stale observers

A `runtime_dispose` row applies to every observer installed by that `instance_id`.

An observer whose declaration says `dispose = "disconnect"` becomes inactive immediately and discards any records that had not yet been snapshotted for delivery.

An observer whose declaration says `dispose = "retain"` stays active with the same concrete observed root, captures, generation program, registration position, and captured shared object. This models a stale observer left registered across a hot replacement.

Disposal does not alter custom-element definitions or component microtask jobs.

## Microtask checkpoints

The continuation preserves the component-job and upgrade rules in `runtime_contract.md`, with an observer delivery phase inserted between them.

At the start of a `microtask_checkpoint`:

- snapshot the component jobs that were already pending and clear the pending component-job queue;
- for every active observer, snapshot the records that were already pending for that observer and clear that observer's pending record queue.

The two snapshots are fixed at checkpoint entry. Work created while the checkpoint is running cannot join either snapshot.

The checkpoint then has three phases in this order:

1. run the frozen component-job snapshot in its existing FIFO order;
2. deliver the frozen observer batches in observer registration order, skipping empty batches;
3. perform the custom-element upgrade sweep from `runtime_contract.md`.

Attribute changes made by phase 1 may enqueue observer records, but those records wait for the next explicit `microtask_checkpoint` because the observer snapshots were already frozen. Observer callbacks may likewise enqueue new observer records or component jobs; both wait for a later explicit checkpoint.

A plain output `checkpoint` drains neither component jobs nor observer records.

## Observer callback batches

One observer delivery receives its entire frozen record batch in record order. The callback processes those records in that order. All lifecycle reactions caused by the observer callback are queued in one implicit reaction frame. After the observer callback finishes processing its whole batch, dispatch that frame using the checkpoint sequence as `dispatch_seq`. Re-entrant lifecycle callbacks then follow the child-frame rule from `runtime_contract.md`.

For each record, evaluate the generation's rules in declaration order. A rule applies when the record's attribute name appears in that rule's `attributes` list. All actions from every matching rule run in declaration order.

The continuation's observer actions use only `set_attr`.

Observer action targets mean:

- `record_target`: the concrete UID from the current mutation record;
- `controller`: the observer's `controller_uid`;
- `capture`: the concrete UID stored under that observer capture name;
- `live_role`: resolve the named `data-role` at action time in the controller's current owner document using the role lookup rules from `runtime_contract.md`;
- `uid`: the named concrete UID.

A null or missing target makes that action a no-op.

## Observer program values

Observer `set_attr` values are string templates. The following fields are available:

- `{record_uid}`
- `{record_name}`
- `{record_old}`
- `{record_new}`
- `{record_seq}`
- `{controller_uid}`
- `{controller_doc}`
- `{observer_name}`
- `{generation_id}`
- `{captured_revision}`
- `{captured_lane}`
- `{live_revision}`
- `{live_lane}`

A null record value expands to the empty string. Shared-state values are read when the observer callback action runs: captured fields from the object retained by that observer instance, live fields from the current object in the same shared slot.

The resulting `set_attr` is an ordinary attribute write under `runtime_contract.md`, including lifecycle reaction generation for custom elements and observer-record generation for any matching active observer.
