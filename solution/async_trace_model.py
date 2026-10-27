from __future__ import annotations

from source_map_model import resolve_two_stage
from hmr_model import HmrLifecycleModel


class AsyncTraceLifecycleModel(HmrLifecycleModel):
    """Continue the recovered browser state through sampled pending async callbacks."""

    def __init__(self, documents, definitions, observer_runtime, event_runtime, frame_runtime,
                 hmr_catalog, bundle_map, chunk_maps, async_catalog):
        super().__init__(documents, definitions, observer_runtime, event_runtime, frame_runtime, hmr_catalog)
        self.bundle_map = bundle_map
        self.chunk_maps = chunk_maps
        self.async_catalog = async_catalog
        self.async_sites = {
            (site['chunk'], site['source'], site['name']): site
            for site in async_catalog['sites']
        }
        self.async_handlers = async_catalog['handlers']
        self.async_lane_rank = {lane: i for i, lane in enumerate(async_catalog['lane_order'])}
        self.async_pending = []
        self.async_internal_ordinal = 1_000_000

    def _install_module(self, module_id, revision_id, seq, phase='install'):
        instance = super()._install_module(module_id, revision_id, seq, phase)
        instance['install_seq'] = seq
        instance['dispose_seq'] = None
        return instance

    def _dispose_module(self, module_id, seq, tx_id):
        instance = self.hmr_active_instances[module_id]
        instance['dispose_seq'] = seq
        return super()._dispose_module(module_id, seq, tx_id)

    def _instance_at(self, module_id, revision_id, scheduled_seq):
        matches = []
        for instance in self.hmr_instances:
            if instance['module_id'] != module_id or instance['revision_id'] != revision_id:
                continue
            if instance['install_seq'] > scheduled_seq:
                continue
            disposed = instance.get('dispose_seq')
            if disposed is not None and scheduled_seq >= disposed:
                continue
            matches.append(instance)
        if len(matches) != 1:
            raise ValueError(
                f'expected one {revision_id} instance at seq {scheduled_seq}, found {len(matches)}'
            )
        return matches[0]

    def _coalesce_async(self, task):
        handler = self.async_handlers[task['handler_id']]
        mode = handler.get('coalesce', 'none')
        key = handler.get('job_key')
        if mode == 'none' or not key:
            self.async_pending.append(task)
            return
        for existing in self.async_pending:
            if existing['instance'] is task['instance'] and existing['job_key'] == key:
                if mode == 'keep_first':
                    if task['queue_order'] < existing['queue_order']:
                        task['content_order'] = task['queue_order']
                        existing.clear()
                        existing.update(task)
                    return
                if mode == 'replace':
                    existing['queue_order'] = min(existing['queue_order'], task['queue_order'])
                    current_content = existing.get('content_order', existing['queue_order'])
                    if task['queue_order'] >= current_content:
                        existing.update({
                            'task_id': task['task_id'],
                            'handler_id': task['handler_id'],
                            'payload': task['payload'],
                            'scheduled_seq': task['scheduled_seq'],
                            'content_order': task['queue_order'],
                        })
                    return
                raise ValueError(f'unknown async coalescing mode: {mode}')
        task['content_order'] = task['queue_order']
        self.async_pending.append(task)

    def async_trace_sample(self, row):
        resolved = resolve_two_stage(
            self.bundle_map, self.chunk_maps, row['generated_line'], row['generated_column']
        )
        if resolved is None:
            raise ValueError(f"unresolved async trace sample {row['task_id']}")
        site = self.async_sites.get((resolved['chunk'], resolved['source'], resolved['name']))
        if site is None:
            raise ValueError(f"no async catalog site for {resolved}")
        instance = self._instance_at(site['module_id'], site['revision_id'], row['scheduled_seq'])
        handler = self.async_handlers[site['handler_id']]
        task = {
            'task_id': row['task_id'],
            'handler_id': site['handler_id'],
            'instance': instance,
            'payload': row.get('payload', ''),
            'scheduled_seq': row['scheduled_seq'],
            'queue_order': (row['scheduled_seq'], row['sample_ordinal']),
            'job_key': handler.get('job_key'),
        }
        self._coalesce_async(task)
        self.async_pending.sort(key=lambda item: item['queue_order'])

    def _queue_internal_async(self, instance, handler_id, payload, seq):
        handler = self.async_handlers[handler_id]
        self.async_internal_ordinal += 1
        task = {
            'task_id': f"internal-{self.async_internal_ordinal}",
            'handler_id': handler_id,
            'instance': instance,
            'payload': payload,
            'scheduled_seq': seq,
            'queue_order': (seq, self.async_internal_ordinal),
            'job_key': handler.get('job_key'),
        }
        self._coalesce_async(task)

    def _execute_async_action(self, task, action, seq):
        if action['op'] == 'queue_async':
            context = self._hmr_context(task['instance'], 'async', {
                'task_id': task['task_id'],
                'payload': task['payload'],
                'scheduled_seq': task['scheduled_seq'],
            })
            payload = self._render_hmr_value(action.get('payload', ''), context)
            self._queue_internal_async(task['instance'], action['handler_id'], payload, seq)
            return
        self._execute_hmr_action(
            task['instance'], action, 'async', seq,
            {'task_id': task['task_id'], 'payload': task['payload'], 'scheduled_seq': task['scheduled_seq']},
        )

    def _run_async_task(self, task, seq):
        actions = self.async_handlers[task['handler_id']].get('actions', [])
        frame = []
        self.reaction_frame_stack.append(frame)
        for action in actions:
            self._execute_async_action(task, action, seq)
        self.reaction_frame_stack.pop()
        if frame:
            self.dispatch(frame, seq)

    def async_task_checkpoint(self, row):
        snapshot = list(self.async_pending)
        self.async_pending = []
        snapshot.sort(key=lambda task: (
            self.async_lane_rank[self.async_handlers[task['handler_id']]['lane']],
            task['queue_order'],
        ))
        for task in snapshot:
            self._run_async_task(task, row['seq'])

    def apply(self, row):
        if row['_source'] != 'async':
            return super().apply(row)
        kind = row['kind']
        if kind == 'async_trace_sample':
            self.async_trace_sample(row)
        elif kind == 'async_task_checkpoint':
            self.async_task_checkpoint(row)
        elif kind == 'set_attr':
            self.set_attr(row)
        elif kind == 'remove_attr':
            self.remove_attr(row)
        elif kind == 'shared_mutate':
            self.shared_mutate(row)
        elif kind == 'shared_replace':
            self.shared_replace(row)
        elif kind == 'microtask_checkpoint':
            self.microtask_checkpoint(row)
        elif kind == 'event_task_checkpoint':
            self.event_task_checkpoint(row)
        elif kind == 'render_checkpoint':
            self.render_checkpoint(row)
        elif kind == 'dispatch_event':
            self.dispatch_event(row)
        elif kind == 'box_update':
            self.box_update(row)
        elif kind == 'scroll_update':
            self.scroll_update(row)
        elif kind == 'translate_update':
            self.translate_update(row)
        elif kind == 'checkpoint':
            self.output_checkpoint(row)
        else:
            raise ValueError(f'unknown async continuation record: {kind}')
