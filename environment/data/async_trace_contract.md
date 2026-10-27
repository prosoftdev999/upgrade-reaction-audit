# Async stack provenance continuation

`async_trace.jsonl` continues the same incident after the transactional HMR phase. The custom-element tree, HMR module instances, observers, listeners, layout state, pending browser jobs, and shared objects all carry forward from the earlier capture.

The browser trace exporter recovered pending callback samples from an optimized bundle after the original lifecycle log had been lost. A sample identifies a callback by generated bundle position, not by module name. `async_bundle_index.map.json` and `async_chunk_maps.json` are the captured source maps. `async_runtime_catalog.json` records the runtime behavior associated with original call sites. These are operational artifacts, not expected output.

## Source-map coordinates

All `generated_line` and `generated_column` values in this capture are zero-based. The synthetic bundle is ASCII, so a column is both a UTF-16 code-unit offset and a byte offset.

`async_bundle_index.map.json` is an indexed Source Map v3 file. Choose the last section whose generated offset does not come after the queried position. Subtract that section offset before resolving its embedded map: subtract the section column only when the query is on the section's first generated line; later lines keep their full column.

A basic map uses the standard Source Map v3 `mappings` field. Semicolon separates generated lines and comma separates segments. Segment integers use base64 VLQ. On each generated line the generated-column accumulator starts at zero. Source index, original line, original column, and name index are delta-coded across segments and continue across generated lines. A mapped segment has four fields, with an optional fifth name field.

For a generated position, use the mapped segment on the same generated line with the greatest generated column not greater than the query column. If no mapped segment exists to the left on that line, the position is unresolved.

The first map resolves a bundle position to a transpiled chunk position. Resolve that returned chunk position through the basic map for the named chunk in `async_chunk_maps.json`. The second mapping yields an original source and name. The pair `(chunk, source, name)` must match exactly one `sites` entry in `async_runtime_catalog.json`.

## Historical callback ownership

Each site identifies a module revision and a handler. Bind a trace sample to the concrete HMR module instance of that revision whose lifetime contains `scheduled_seq`: installation is inclusive and disposal is exclusive. Module disposal after `scheduled_seq` does not invalidate an already-scheduled callback. If no such concrete instance exists, the capture is inconsistent.

The callback keeps that concrete module instance, its install-time captures, its install-time shared-object reference, and its module-local state. Live role lookup and live shared-slot lookup still use the runtime state that exists when the callback eventually executes.

This historical binding is the reason a current active revision or a final-state lookup is not interchangeable with the sampled callback owner.

## Pending async tasks

`async_trace_sample` imports one recovered pending callback. Samples are inserted by historical schedule order `(scheduled_seq, sample_ordinal)`, not by the later trace-export `seq` at which the row appears.

The matched handler supplies a `lane`, `job_key`, `coalesce` mode, and action list. Lane precedence is the `lane_order` array in `async_runtime_catalog.json`; within one lane, preserve pending queue position.

Coalescing is scoped to `(concrete module instance, job_key)`:

- `none`: append every sample;
- `keep_first`: keep an already-pending task with that key and ignore the newer one;
- `replace`: replace the newer task's identity and payload into the existing pending entry without changing that entry's queue position.

`async_task_checkpoint` snapshots every task already pending at the start of the row, clears those entries from the pending set, and runs the snapshot lane-by-lane in the catalog's lane precedence. Tasks created while a snapshot is running are pending for the next `async_task_checkpoint`; they never join the snapshot currently being drained.

A task runs the handler actions from `async_runtime_catalog.json` against its captured concrete module instance. Existing HMR action semantics for targets, shared objects, layout changes, reaction frames, and string substitutions still apply. The additional substitution fields are `{task_id}`, `{payload}`, and `{scheduled_seq}`.

`queue_async` schedules an internal handler on the same captured module instance. The referenced handler provides its own lane, key, coalescing mode, and actions. Its payload is rendered from the current callback context before it is queued. Internal tasks use the current `async_task_checkpoint` sequence as their scheduling sequence and receive increasing internal ordinals after all imported sample ordinals.

Output checkpoints do not drain pending async tasks. Ordinary microtask, event-task, and render checkpoints retain their meanings from the earlier contracts.

## Identifiability

Every sampled bundle position in this capture resolves through both source-map levels to one catalog site. Every site's module revision has exactly one concrete HMR instance alive at that sample's historical `scheduled_seq`. These properties are part of the captured evidence, not verifier-only assumptions.
