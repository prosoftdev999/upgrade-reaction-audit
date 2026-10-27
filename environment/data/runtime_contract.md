# Web-component lifecycle capture contract

This capture comes from a reduced web-component runtime used by the frontend platform team to reproduce a lazy-registration regression. The rules below are normative for the captured session. They intentionally cover only the behavior exercised by these files; do not substitute a browser's current implementation details for this contract.

## Records and ordering

`dom_capture.jsonl`, `registry_capture.jsonl`, and `scheduler_capture.jsonl` share one strictly increasing integer `seq` domain. A smaller `seq` happened first. Records with different sources are interpreted in that global order.

The two documents and their custom-element registries are listed in `documents.json`. Definition metadata is in `definition_catalog.json`. A definition is unavailable until a matching `define` record appears in `registry_capture.jsonl`.

`final_dom_snapshot.json` is a diagnostic snapshot of final DOM ownership, topology, and attributes. It is not a callback log and does not replace the historical records.

## Concrete identity and ownership

Every `uid` is a concrete node identity for the whole capture. Moving or adopting a node never changes its `uid`.

An element has one `owner_doc`. A document's registry is the registry listed for that document in `documents.json`.

A custom-element candidate is an element whose `local_name` contains `-`. Candidates start in `uncustomized` state. Ordinary elements and shadow roots are not candidates.

Once a candidate upgrades successfully, it enters `custom` state and its `definition_id` is permanent for the rest of the capture. Adopting an already-custom element into another document does not rebind it to the destination registry.

The capture contains no failed constructors and no attempt to define the same custom-element name twice in one registry.

## DOM topology and connectedness

Document roots are named by `root_uid` in `documents.json`.

A regular element is connected when following its `parent_uid` chain reaches a document root. A shadow-root child is connected when its host is connected. Attaching a shadow root does not itself connect or disconnect the host.

For rules that refer to **shadow-including preorder**:

- visit an element itself;
- then visit children of its attached shadow root, recursively, if it has one;
- then visit its light-DOM children, recursively.

Document order is the `order` field in `documents.json`.

## Registry availability and upgrade checkpoints

A `define` record makes the referenced definition available in that registry from that `seq` onward. It does not synchronously upgrade elements.

Upgrades happen only at `microtask_checkpoint` records from `scheduler_capture.jsonl`.

At one microtask checkpoint, every still-uncustomized candidate whose **current** owner document's registry already defines that candidate's `local_name` upgrades exactly once.

Eligible candidates are ordered as follows:

1. documents in `documents.json` order;
2. within each document, connected candidates in shadow-including preorder from the document root;
3. then detached candidates owned by that document in creation-sequence order.

The upgrade sweep is one batch. Determine the ordered eligible set first. Before dispatching any lifecycle callback from that sweep, every member of that set enters `custom` state, binds the definition available from its current owner document's registry, resolves its upgrade-time captures, and snapshots the lifecycle reactions described below. Those bindings and captures remain fixed afterward.

One upgrade snapshots lifecycle reactions in this order:

1. `constructor`;
2. one `attribute_changed` for each currently-present observed attribute, in the definition's `observed_attributes` order, with `old_value = null` and `new_value` equal to the current value;
3. `connected` if the element is connected at the upgrade checkpoint.

After the whole eligible batch has been bound and its upgrade reactions have been snapshotted, dispatch those reactions at the checkpoint's `seq`, in the candidate and per-candidate orders above. A callback from an earlier candidate therefore observes every later member of the same batch as already custom. If such a callback changes an observed attribute on a later member, that write queues the ordinary re-entrant `attribute_changed` reaction immediately; it is not treated as a pre-upgrade write.

## Reaction scopes

`scope_begin` and `scope_end` records delimit reaction scopes. Scopes may nest.

Lifecycle reactions caused by DOM operations inside a scope are queued on that **innermost** scope. They do not dispatch when the operation runs.

When a scope ends, that scope's queued reactions dispatch immediately in FIFO order, with `dispatch_seq` equal to that `scope_end` record's `seq`. A nested scope therefore dispatches its own queue before an older outer-scope queue that has not ended yet.

Reactions from separate attribute changes are never coalesced, including repeated writes to the same observed attribute inside one scope.

The capture places microtask checkpoints and output checkpoints only when no reaction scope is open.

Each output reaction records the state captured when it was queued: `definition_id`, attribute values, and old/new document IDs do not change if the element is later modified before dispatch.

## Attribute operations

`set_attr` changes or creates one attribute. If an element is already custom, the value actually changes, and that name is in its bound definition's `observed_attributes`, queue one `attribute_changed` reaction.

`remove_attr` removes the named attribute if present. For an already-custom element observing that name, queue one `attribute_changed` reaction with the prior value and `new_value = null`.

Attribute writes made before upgrade do not enqueue reactions at write time. If such an attribute is observed by the eventual definition and still present at upgrade, the initial upgrade rule above emits its reaction.

## Insert, remove, and move

`insert` is used with a detached subtree root. After attaching it to `parent_uid`, if the subtree becomes connected, queue `connected` for already-custom elements in that subtree in shadow-including preorder.

`remove` detaches the subtree. If it was connected, queue `disconnected` for already-custom elements in the subtree in **reverse** shadow-including preorder before detaching it.

`move` is an explicit remove-plus-insert within one owner document. If the subtree starts connected, queue the same reverse-preorder `disconnected` reactions; after changing the parent, if it is connected, queue preorder `connected` reactions. A same-document move is therefore observable even when the subtree is connected both before and after the operation.

## Adoption

`adopt` is used only for a detached subtree root. It changes `owner_doc` for the root, its light descendants, its attached shadow root, and all shadow descendants.

For every already-custom element in that adopted subtree whose owner document changes, queue one `adopted` reaction in shadow-including preorder. The payload captures the old and new document IDs.

Uncustomized candidates do not emit `adopted`. Their new owner document determines which registry can upgrade them at a later microtask checkpoint.

Already-custom elements keep their existing definition across adoption, including that definition's observed-attribute set.

## Output checkpoints

At each `checkpoint` record, report all custom-element candidates created so far, ordered by creation `seq`.

Each instance row reports its state **at that checkpoint**:

- concrete `uid`;
- `local_name`;
- current `owner_doc`;
- current `connected` boolean;
- `custom_state` (`uncustomized` or `custom`);
- bound `definition_id`, or JSON `null` while uncustomized.

The checkpoint's `reactions` array contains every reaction that dispatched after the previous output checkpoint and up to this checkpoint, in dispatch order. Each reaction has a monotonically increasing global `dispatch_index` starting at 1.

The fields `name`, `old_value`, `new_value`, `old_doc`, and `new_doc` are always present. Use JSON `null` when a field is not applicable.

## Captured component programs

The later part of this capture includes reduced callback programs in `definition_catalog.json`. These are authoritative runtime behavior, not expected-output data. Older definitions without `captures` or `programs` have no extra callback behavior.

A definition may declare `captures`. Each capture has a `name` and `role_from_attr`. When an element upgrades, read that attribute from the element and resolve the first matching `data-role` in the element's current owner document. Store the concrete UID under the capture name. That UID is fixed for the lifetime of the custom element, even if either element is later moved or adopted.

Role lookup uses the same shadow-including document order as upgrade discovery. Connected elements are considered first. If no connected match exists, detached elements owned by that document are considered in creation-sequence order. If there is no match, the capture is null.

A target in a callback program has one of these meanings:

- `self`: the element whose lifecycle callback is dispatching;
- `capture`: the concrete UID saved under that capture name at upgrade time;
- `live_role`: resolve `data-role` when the action actually runs, in the callback element's current owner document;

A null target makes that one program action a no-op. Captured targets are not re-resolved after adoption. Live-role targets are.

## Callback dispatch and re-entrant reactions

After a lifecycle reaction is appended to the output ledger, run the matching program from its bound definition. For an `attribute_changed` reaction, run the generic `attribute_changed` program first if one exists, then the `attribute_changed:<name>` program if one exists.

Synchronous program mutations use the reaction's `dispatch_seq` as their trigger sequence. If one of those mutations causes another lifecycle reaction, the new reaction belongs to a child reaction frame. Finish the current callback program, dispatch that child frame completely, and only then resume the older reaction queue. This nesting rule applies recursively.

Program-driven mutations are not duplicated in `dom_capture.jsonl`; that file contains the caller-originated DOM operations. The resulting DOM state is still reflected by later operations and by `final_dom_snapshot.json`.

## Program actions

The reduced callback programs use these operations:

- `set_attr`: resolve the target, then apply the same attribute semantics defined above;
- `enqueue_microtask`: append a component job for a later `microtask_checkpoint`.

String values in program actions may contain `{uid}`, `{definition_id}`, `{callback}`, `{name}`, `{old_value}`, `{new_value}`, `{old_doc}`, `{new_doc}`, or `{owner_doc}`. The reaction fields are the values captured by the reaction. `{owner_doc}` is read from the callback element when the action runs. A null reaction field expands to the empty string.

## Component microtask jobs

An `enqueue_microtask` action may have a `job_key` and a `coalesce` mode. Pending jobs are keyed by `(callback element UID, job_key)`.

- `none`: always append a new job;
- `keep_first`: if the same key is already pending for that element, keep the older job;
- `replace`: if the same key is already pending for that element, replace its payload and captured reaction context without changing its queue position.

At the start of each `microtask_checkpoint`, take a snapshot of the component jobs that were already pending and clear the pending queue. Run that snapshot in FIFO order. Jobs enqueued while that snapshot is running are pending for the next explicit `microtask_checkpoint`; they do not join the snapshot currently being drained.

Each job runs its action list as one implicit reaction frame. Lifecycle reactions caused by those actions dispatch immediately at the checkpoint's `seq`, using the re-entrant rule above. Job actions use the reaction context captured when the job was enqueued, while target resolution and `{owner_doc}` use the live state at execution time.

After the checkpoint's pre-existing component-job snapshot is drained, perform the custom-element upgrade sweep described earlier. Jobs enqueued by upgrade callbacks are therefore also left pending until the next explicit `microtask_checkpoint`.

Output `checkpoint` records do not drain component jobs. A checkpoint can legitimately show state and reactions while jobs remain pending for a later scheduler checkpoint.
