from __future__ import annotations

from observer_model import ObserverLifecycleModel


class EventLifecycleModel(ObserverLifecycleModel):
    """Observer replay plus the preserved delegated-event continuation."""

    def __init__(self, documents, definitions, observer_runtime, event_runtime):
        super().__init__(documents, definitions, observer_runtime)
        self.event_generations = {
            row['generation_id']: row for row in event_runtime.get('generations', [])
        }
        self.event_listeners = []
        self.event_registration_index = 0
        self.aborted_signals = set()
        self.event_jobs = []

    def _resolve_event_install_target(self, controller_uid, target):
        kind = target['kind']
        if kind == 'uid':
            uid = target['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            controller = self.nodes[controller_uid]
            return self.query_role(controller['owner_doc'], target['role'])
        raise ValueError(f'unknown event install target: {kind}')

    def _resolve_event_capture(self, controller_uid, capture):
        return self._resolve_event_install_target(controller_uid, capture)

    def event_runtime_install(self, row):
        generation = self.event_generations[row['generation_id']]
        for spec in generation.get('listeners', []):
            controller_uid = spec['controller_uid']
            current_target_uid = self._resolve_event_install_target(controller_uid, spec['listen_target'])
            if current_target_uid is None:
                continue
            captures = {
                capture['name']: self._resolve_event_capture(controller_uid, capture)
                for capture in spec.get('captures', [])
            }
            slot_name = spec.get('shared_slot')
            captured_state = self.shared_slots[slot_name] if slot_name else None
            self.event_registration_index += 1
            signal = spec.get('signal')
            active = signal not in self.aborted_signals
            self.event_listeners.append({
                'registration_index': self.event_registration_index,
                'runtime_instance': row['instance_id'],
                'generation_id': row['generation_id'],
                'name': spec['name'],
                'controller_uid': controller_uid,
                'current_target_uid': current_target_uid,
                'event_type': spec['event_type'],
                'phase': spec.get('phase', 'bubble'),
                'once': bool(spec.get('once', False)),
                'signal': signal,
                'dispose': spec.get('dispose', 'disconnect'),
                'captures': captures,
                'shared_slot': slot_name,
                'captured_state': captured_state,
                'actions': spec.get('actions', []),
                'active': active,
            })

    def event_runtime_dispose(self, row):
        for listener in self.event_listeners:
            if listener['runtime_instance'] != row['instance_id'] or not listener['active']:
                continue
            if listener['dispose'] == 'retain':
                continue
            if listener['dispose'] != 'disconnect':
                raise ValueError(f"unknown listener disposal mode: {listener['dispose']}")
            listener['active'] = False

    def signal_abort(self, row):
        signal = row['signal']
        self.aborted_signals.add(signal)
        for listener in self.event_listeners:
            if listener['signal'] == signal:
                listener['active'] = False

    def _event_path(self, target_uid, composed):
        if target_uid not in self.nodes:
            return []
        path = []
        current = target_uid
        visible_target = target_uid
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            path.append((current, visible_target))
            node = self.nodes[current]
            if node['kind'] == 'shadow_root':
                if not composed:
                    break
                visible_target = node['host_uid']
                current = node['host_uid']
            else:
                current = node['parent_uid']
        return path

    def _listener_context(self, listener, event, current_target_uid, visible_target_uid):
        captured = listener['captured_state'] or {}
        live = self.shared_slots[listener['shared_slot']] if listener['shared_slot'] else {}
        controller = self.nodes[listener['controller_uid']]
        current = self.nodes[current_target_uid]
        return {
            'event_id': event['event_id'],
            'event_type': event['event_type'],
            'event_detail': event.get('detail'),
            'original_target_uid': event['target_uid'],
            'visible_target_uid': visible_target_uid,
            'current_target_uid': current_target_uid,
            'current_target_doc': current['owner_doc'],
            'controller_uid': listener['controller_uid'],
            'controller_doc': controller['owner_doc'],
            'listener_name': listener['name'],
            'generation_id': listener['generation_id'],
            'captured_revision': captured.get('revision'),
            'captured_lane': captured.get('lane'),
            'live_revision': live.get('revision'),
            'live_lane': live.get('lane'),
        }

    def _render_event_value(self, value, context):
        if value is None or not isinstance(value, str):
            return value
        safe = {key: '' if item is None else str(item) for key, item in context.items()}
        return value.format_map(safe)

    def _resolve_event_action_target(self, listener, event, current_target_uid, visible_target_uid, target):
        kind = target['kind']
        if kind == 'original_target':
            return event['target_uid']
        if kind == 'visible_target':
            return visible_target_uid
        if kind == 'current_target':
            return current_target_uid
        if kind == 'controller':
            return listener['controller_uid']
        if kind == 'capture':
            return listener['captures'].get(target['name'])
        if kind == 'uid':
            uid = target['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            controller = self.nodes[listener['controller_uid']]
            return self.query_role(controller['owner_doc'], target['role'])
        raise ValueError(f'unknown event action target: {kind}')

    def _enqueue_event_job(self, listener, event, current_target_uid, visible_target_uid, action, context):
        target_uid = self._resolve_event_action_target(
            listener, event, current_target_uid, visible_target_uid, action['target']
        )
        if target_uid is None:
            return
        key = self._render_event_value(action.get('job_key'), context)
        job = {
            'owner_registration': listener['registration_index'],
            'key': key,
            'event_id': self._render_event_value(action['event_id'], context),
            'event_type': action['event_type'],
            'target_uid': target_uid,
            'detail': self._render_event_value(action.get('detail'), context),
            'bubbles': bool(action.get('bubbles', True)),
            'composed': bool(action.get('composed', True)),
        }
        coalesce = action.get('coalesce', 'none')
        if key is None or coalesce == 'none':
            self.event_jobs.append(job)
            return
        found = None
        for index, pending in enumerate(self.event_jobs):
            if pending['owner_registration'] == listener['registration_index'] and pending['key'] == key:
                found = index
                break
        if found is None:
            self.event_jobs.append(job)
        elif coalesce == 'replace':
            self.event_jobs[found] = job
        elif coalesce == 'keep_first':
            return
        else:
            raise ValueError(f'unknown event job coalescing mode: {coalesce}')

    def _execute_event_action(self, listener, event, current_target_uid, visible_target_uid, action, dispatch_seq):
        context = self._listener_context(listener, event, current_target_uid, visible_target_uid)
        op = action['op']
        if op in {'set_attr', 'remove_attr'}:
            uid = self._resolve_event_action_target(
                listener, event, current_target_uid, visible_target_uid, action['target']
            )
            if uid is None:
                return
            if op == 'set_attr':
                value = self._render_event_value(action['value'], context)
                self._set_attr(uid, action['name'], value, dispatch_seq)
            else:
                self._remove_attr(uid, action['name'], dispatch_seq)
            return
        if op == 'move':
            uid = self._resolve_event_action_target(
                listener, event, current_target_uid, visible_target_uid, action['target']
            )
            parent_uid = self._resolve_event_action_target(
                listener, event, current_target_uid, visible_target_uid, action['parent']
            )
            if uid is not None and parent_uid is not None:
                self._move_uid(uid, parent_uid, dispatch_seq)
            return
        if op == 'shared_mutate':
            slot = self.shared_slots[action['slot']]
            values = {
                key: self._render_event_value(value, context)
                for key, value in action['values'].items()
            }
            slot.update(values)
            return
        if op == 'shared_replace':
            self.shared_slots[action['slot']] = {
                key: self._render_event_value(value, context)
                for key, value in action['value'].items()
            }
            return
        if op == 'enqueue_event':
            self._enqueue_event_job(listener, event, current_target_uid, visible_target_uid, action, context)
            return
        raise ValueError(f'unknown event action: {op}')

    def _invoke_event_listener(self, listener, event, current_target_uid, visible_target_uid, dispatch_seq):
        if not listener['active']:
            return
        if listener['signal'] in self.aborted_signals:
            listener['active'] = False
            return
        frame = []
        self.reaction_frame_stack.append(frame)
        for action in listener['actions']:
            self._execute_event_action(listener, event, current_target_uid, visible_target_uid, action, dispatch_seq)
        self.reaction_frame_stack.pop()
        if listener['once']:
            listener['active'] = False
        if frame:
            self.dispatch(frame, dispatch_seq)

    def dispatch_event(self, row):
        event = {
            'event_id': row['event_id'],
            'event_type': row['event_type'],
            'target_uid': row['target_uid'],
            'detail': row.get('detail'),
            'bubbles': bool(row.get('bubbles', True)),
            'composed': bool(row.get('composed', True)),
        }
        path = self._event_path(event['target_uid'], event['composed'])
        if not path:
            return

        # Freeze the path and listener registrations before any listener runs.
        by_uid = {uid: visible for uid, visible in path}
        path_uids = [uid for uid, _ in path]
        snapshot = [
            listener for listener in self.event_listeners
            if listener['active']
            and listener['event_type'] == event['event_type']
            and listener['current_target_uid'] in by_uid
        ]
        snapshot.sort(key=lambda listener: listener['registration_index'])

        # Capture phase: outermost -> target.
        for current_uid in reversed(path_uids):
            for listener in snapshot:
                if listener['phase'] == 'capture' and listener['current_target_uid'] == current_uid:
                    self._invoke_event_listener(listener, event, current_uid, by_uid[current_uid], row['seq'])

        # Bubble phase includes target. Ancestors participate only when bubbles is true.
        bubble_uids = path_uids if event['bubbles'] else path_uids[:1]
        for current_uid in bubble_uids:
            for listener in snapshot:
                if listener['phase'] == 'bubble' and listener['current_target_uid'] == current_uid:
                    self._invoke_event_listener(listener, event, current_uid, by_uid[current_uid], row['seq'])

    def event_task_checkpoint(self, row):
        if self.scope_stack:
            raise ValueError('event task checkpoint occurred inside a reaction scope')
        pending = self.event_jobs
        self.event_jobs = []
        for job in pending:
            event_row = {
                'seq': row['seq'],
                'event_id': job['event_id'],
                'event_type': job['event_type'],
                'target_uid': job['target_uid'],
                'detail': job['detail'],
                'bubbles': job['bubbles'],
                'composed': job['composed'],
            }
            self.dispatch_event(event_row)

    def apply(self, row):
        if row['_source'] != 'event':
            return super().apply(row)

        kind = row['kind']
        if kind == 'event_runtime_install':
            self.event_runtime_install(row)
        elif kind == 'event_runtime_dispose':
            self.event_runtime_dispose(row)
        elif kind == 'signal_abort':
            self.signal_abort(row)
        elif kind == 'dispatch_event':
            self.dispatch_event(row)
        elif kind == 'event_task_checkpoint':
            self.event_task_checkpoint(row)
        elif kind == 'shared_mutate':
            self.shared_mutate(row)
        elif kind == 'shared_replace':
            self.shared_replace(row)
        elif kind == 'scope_begin':
            self.begin_scope(row)
        elif kind == 'scope_end':
            self.end_scope(row)
        elif kind == 'microtask_checkpoint':
            self.microtask_checkpoint(row)
        elif kind == 'checkpoint':
            self.output_checkpoint(row)
        elif kind == 'create':
            self.create(row)
        elif kind == 'attach_shadow':
            self.attach_shadow(row)
        elif kind == 'insert':
            self.insert(row)
        elif kind == 'remove':
            self.remove(row)
        elif kind == 'move':
            self.move(row)
        elif kind == 'adopt':
            self.adopt(row)
        elif kind == 'set_attr':
            self.set_attr(row)
        elif kind == 'remove_attr':
            self.remove_attr(row)
        else:
            raise ValueError(f'unknown event continuation record: {kind}')
