from __future__ import annotations

from model import LifecycleModel, Reaction


class ObserverLifecycleModel(LifecycleModel):
    """Lifecycle replay plus the captured hot-reload observer continuation."""

    def __init__(self, documents, definitions, observer_runtime):
        super().__init__(documents, definitions)
        self.observer_generations = {
            row['generation_id']: row for row in observer_runtime.get('generations', [])
        }
        self.shared_slots = {
            name: dict(value) for name, value in observer_runtime.get('shared_slots', {}).items()
        }
        self.observers = []
        self.observer_registration_index = 0

    def _shadow_parent(self, uid):
        node = self.nodes[uid]
        if node['kind'] == 'shadow_root':
            return node['host_uid']
        return node['parent_uid']

    def _within_observed_subtree(self, uid, root_uid):
        current = uid
        seen = set()
        while current is not None and current not in seen:
            if current == root_uid:
                return True
            seen.add(current)
            current = self._shadow_parent(current)
        return False

    def _record_observer_attribute(self, uid, name, old_value, new_value, trigger_seq):
        if old_value == new_value:
            return
        for observer in self.observers:
            if not observer['active'] or name not in observer['attribute_filter']:
                continue
            if uid != observer['root_uid']:
                if not observer['subtree']:
                    continue
                if not self._within_observed_subtree(uid, observer['root_uid']):
                    continue
            observer['records'].append({
                'uid': uid,
                'name': name,
                'old_value': old_value,
                'new_value': new_value,
                'seq': trigger_seq,
            })

    def _set_attr(self, uid, name, value, trigger_seq):
        if uid is None:
            return
        old_value = self.nodes[uid]['attrs'].get(name)
        super()._set_attr(uid, name, value, trigger_seq)
        self._record_observer_attribute(uid, name, old_value, value, trigger_seq)

    def _remove_attr(self, uid, name, trigger_seq):
        if uid is None:
            return
        node = self.nodes[uid]
        if name not in node['attrs']:
            return
        old_value = node['attrs'][name]
        super()._remove_attr(uid, name, trigger_seq)
        self._record_observer_attribute(uid, name, old_value, None, trigger_seq)

    def _resolve_install_target(self, controller_uid, target):
        kind = target['kind']
        if kind == 'uid':
            uid = target['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            return self.query_role(self.nodes[controller_uid]['owner_doc'], target['role'])
        raise ValueError(f'unknown observer install target: {kind}')

    def _resolve_install_capture(self, controller_uid, capture):
        kind = capture['kind']
        if kind == 'uid':
            uid = capture['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            return self.query_role(self.nodes[controller_uid]['owner_doc'], capture['role'])
        raise ValueError(f'unknown observer capture target: {kind}')

    def runtime_install(self, row):
        generation = self.observer_generations[row['generation_id']]
        for spec in generation.get('observers', []):
            controller_uid = spec['controller_uid']
            root_uid = self._resolve_install_target(controller_uid, spec['observe_target'])
            if root_uid is None:
                continue
            captures = {
                capture['name']: self._resolve_install_capture(controller_uid, capture)
                for capture in spec.get('captures', [])
            }
            slot_name = spec['shared_slot']
            captured_state = self.shared_slots[slot_name]
            self.observer_registration_index += 1
            self.observers.append({
                'registration_index': self.observer_registration_index,
                'runtime_instance': row['instance_id'],
                'generation_id': row['generation_id'],
                'name': spec['name'],
                'controller_uid': controller_uid,
                'observe_target': dict(spec['observe_target']),
                'root_uid': root_uid,
                'subtree': bool(spec.get('subtree', False)),
                'attribute_filter': set(spec.get('attribute_filter', [])),
                'captures': captures,
                'capture_specs': {capture['name']: dict(capture) for capture in spec.get('captures', [])},
                'shared_slot': slot_name,
                'captured_state': captured_state,
                'dispose': spec.get('dispose', 'disconnect'),
                'rules': spec.get('rules', []),
                'active': True,
                'records': [],
            })

    def runtime_dispose(self, row):
        for observer in self.observers:
            if observer['runtime_instance'] != row['instance_id'] or not observer['active']:
                continue
            if observer['dispose'] == 'retain':
                continue
            if observer['dispose'] != 'disconnect':
                raise ValueError(f"unknown observer disposal mode: {observer['dispose']}")
            observer['active'] = False
            observer['records'] = []

    def shared_mutate(self, row):
        slot = self.shared_slots[row['slot']]
        slot.update(row['values'])

    def shared_replace(self, row):
        self.shared_slots[row['slot']] = dict(row['value'])

    def _take_observer_snapshots(self):
        snapshots = []
        for observer in self.observers:
            if not observer['active']:
                continue
            records = observer['records']
            observer['records'] = []
            if records:
                snapshots.append((observer, records))
        return snapshots

    def _observer_context(self, observer, record):
        captured = observer['captured_state']
        live = self.shared_slots[observer['shared_slot']]
        controller = self.nodes[observer['controller_uid']]
        return {
            'record_uid': record['uid'],
            'record_name': record['name'],
            'record_old': record['old_value'],
            'record_new': record['new_value'],
            'record_seq': record['seq'],
            'controller_uid': observer['controller_uid'],
            'controller_doc': controller['owner_doc'],
            'observer_name': observer['name'],
            'generation_id': observer['generation_id'],
            'captured_revision': captured.get('revision'),
            'captured_lane': captured.get('lane'),
            'live_revision': live.get('revision'),
            'live_lane': live.get('lane'),
        }

    def _render_observer_value(self, value, context):
        safe = {key: '' if item is None else str(item) for key, item in context.items()}
        return value.format_map(safe)

    def _resolve_observer_action_target(self, observer, record, target):
        kind = target['kind']
        if kind == 'record_target':
            return record['uid']
        if kind == 'controller':
            return observer['controller_uid']
        if kind == 'capture':
            return observer['captures'].get(target['name'])
        if kind == 'uid':
            return target['uid'] if target['uid'] in self.nodes else None
        if kind == 'live_role':
            controller = self.nodes[observer['controller_uid']]
            return self.query_role(controller['owner_doc'], target['role'])
        raise ValueError(f'unknown observer action target: {kind}')

    def _execute_observer_action(self, observer, record, action, checkpoint_seq):
        if action['op'] != 'set_attr':
            raise ValueError(f"unknown observer action: {action['op']}")
        uid = self._resolve_observer_action_target(observer, record, action['target'])
        if uid is None:
            return
        value = self._render_observer_value(action['value'], self._observer_context(observer, record))
        self._set_attr(uid, action['name'], value, checkpoint_seq)

    def _deliver_observer_snapshots(self, snapshots, checkpoint_seq):
        for observer, records in snapshots:
            frame = []
            self.reaction_frame_stack.append(frame)
            for record in records:
                for rule in observer['rules']:
                    if record['name'] not in rule.get('attributes', []):
                        continue
                    for action in rule.get('actions', []):
                        self._execute_observer_action(observer, record, action, checkpoint_seq)
            self.reaction_frame_stack.pop()
            if frame:
                self.dispatch(frame, checkpoint_seq)

    def microtask_checkpoint(self, row):
        if self.scope_stack:
            raise ValueError('microtask checkpoint occurred inside a reaction scope')

        observer_snapshots = self._take_observer_snapshots()
        self._drain_microtasks(row['seq'])
        self._deliver_observer_snapshots(observer_snapshots, row['seq'])

        eligible = []
        for document_id in self.document_order:
            for uid in self.candidate_order(document_id):
                node = self.nodes[uid]
                if node['custom_state'] != 'uncustomized':
                    continue
                definition_id = self.definition_available_for(uid)
                if definition_id:
                    eligible.append((uid, definition_id))

        reactions = []
        for uid, definition_id in eligible:
            node = self.nodes[uid]
            if node['custom_state'] != 'uncustomized':
                continue
            node['custom_state'] = 'custom'
            node['definition_id'] = definition_id
            definition = self.definitions[definition_id]
            node['captures'] = self._resolve_captures(uid, definition)

            reactions.append(Reaction(row['seq'], uid, definition_id, 'constructor'))
            for name in definition['observed_attributes']:
                if name in node['attrs']:
                    reactions.append(Reaction(
                        row['seq'], uid, definition_id, 'attribute_changed',
                        name=name, old_value=None, new_value=node['attrs'][name]
                    ))
            if self.is_connected(uid):
                reactions.append(Reaction(row['seq'], uid, definition_id, 'connected'))

        self.dispatch(reactions, row['seq'])

    def apply(self, row):
        if row['_source'] != 'observer':
            return super().apply(row)

        kind = row['kind']
        if kind == 'runtime_install':
            self.runtime_install(row)
        elif kind == 'runtime_dispose':
            self.runtime_dispose(row)
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
            raise ValueError(f'unknown observer continuation record: {kind}')
