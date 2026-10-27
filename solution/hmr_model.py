from __future__ import annotations

from collections import defaultdict, deque

from frame_model import FrameLifecycleModel


class HmrLifecycleModel(FrameLifecycleModel):
    """Carry the frontend runtime through the captured HMR module-graph continuation."""

    def __init__(
        self,
        documents,
        definitions,
        observer_runtime,
        event_runtime,
        frame_runtime,
        hmr_catalog,
    ):
        super().__init__(documents, definitions, observer_runtime, event_runtime, frame_runtime)
        self.hmr_entries = tuple(hmr_catalog['entries'])
        self.hmr_initial = dict(hmr_catalog['initial_revisions'])
        self.hmr_revisions = {
            revision['revision_id']: revision
            for revision in hmr_catalog['revisions']
        }
        self.hmr_active_revisions = {}
        self.hmr_active_instances = {}
        self.hmr_instances = []
        self.hmr_instance_counter = 0
        self.hmr_seen_transactions = set()
        self.hmr_bootstrapped = False

    def _revision(self, module_id, revision_id=None):
        revision_id = revision_id or self.hmr_active_revisions[module_id]
        revision = self.hmr_revisions[revision_id]
        if revision['module_id'] != module_id:
            raise ValueError(f'revision {revision_id} does not belong to module {module_id}')
        return revision

    def _active_graph(self):
        return {
            module_id: tuple(self._revision(module_id).get('imports', []))
            for module_id in self.hmr_active_revisions
        }

    @staticmethod
    def _parents(graph):
        parents = defaultdict(list)
        for parent, dependencies in graph.items():
            for dependency in dependencies:
                parents[dependency].append(parent)
        for values in parents.values():
            values.sort()
        return parents

    def _topological_modules(self, module_ids, revisions, dependencies_first):
        module_ids = set(module_ids)
        graph = {
            module_id: [dep for dep in self._revision(module_id, revisions[module_id]).get('imports', []) if dep in module_ids]
            for module_id in module_ids
        }
        indegree = {module_id: 0 for module_id in module_ids}
        outgoing = defaultdict(list)
        if dependencies_first:
            for parent, deps in graph.items():
                for dep in deps:
                    outgoing[dep].append(parent)
                    indegree[parent] += 1
        else:
            for parent, deps in graph.items():
                for dep in deps:
                    outgoing[parent].append(dep)
                    indegree[dep] += 1
        ready = deque(sorted(module for module, degree in indegree.items() if degree == 0))
        ordered = []
        while ready:
            module = ready.popleft()
            ordered.append(module)
            for neighbor in sorted(outgoing[module]):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    ready.append(neighbor)
        if len(ordered) != len(module_ids):
            raise ValueError('HMR catalog contains a cycle in the changed module set')
        return ordered

    def _resolve_hmr_target(self, instance, target):
        kind = target['kind']
        if kind == 'controller':
            return instance['controller_uid']
        if kind == 'capture':
            return instance['captures'].get(target['name'])
        if kind == 'uid':
            uid = target['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            controller = self.nodes[instance['controller_uid']]
            return self.query_role(controller['owner_doc'], target['role'])
        raise ValueError(f'unknown HMR target kind: {kind}')

    def _hmr_context(self, instance, phase, extra=None):
        captured = instance['captured_state'] or {}
        slot = instance['shared_slot']
        live = self.shared_slots[slot] if slot else {}
        controller = self.nodes[instance['controller_uid']]
        context = {
            'module_id': instance['module_id'],
            'revision_id': instance['revision_id'],
            'instance_id': instance['instance_id'],
            'controller_uid': instance['controller_uid'],
            'controller_doc': controller['owner_doc'],
            'captured_revision': captured.get('revision'),
            'captured_lane': captured.get('lane'),
            'live_revision': live.get('revision'),
            'live_lane': live.get('lane'),
            'phase': phase,
        }
        context.update({f'state_{key}': value for key, value in instance['state'].items()})
        if extra:
            context.update(extra)
        return context

    @staticmethod
    def _render_hmr_value(value, context):
        if value is None or not isinstance(value, str):
            return value
        safe = {key: '' if item is None else str(item) for key, item in context.items()}
        return value.format_map(safe)

    def _execute_hmr_action(self, instance, action, phase, seq, extra):
        op = action['op']
        if op == 'bump_state':
            key = action['key']
            instance['state'][key] = int(instance['state'].get(key, 0)) + int(action.get('by', 1))
            return

        context = self._hmr_context(instance, phase, extra)
        if op == 'set_attr':
            uid = self._resolve_hmr_target(instance, action['target'])
            if uid is None:
                return
            value = self._render_hmr_value(action['value'], context)
            self._set_attr(uid, action['name'], value, seq)
            return
        if op == 'remove_attr':
            uid = self._resolve_hmr_target(instance, action['target'])
            if uid is not None:
                self._remove_attr(uid, action['name'], seq)
            return
        if op == 'shared_mutate':
            slot = action['slot']
            values = {
                key: self._render_hmr_value(value, context)
                for key, value in action['values'].items()
            }
            self.shared_slots[slot].update(values)
            return
        if op == 'shared_replace':
            slot = action['slot']
            self.shared_slots[slot] = {
                key: self._render_hmr_value(value, context)
                for key, value in action['value'].items()
            }
            return
        if op in {'set_box', 'set_translate', 'set_scroll'}:
            uid = self._resolve_hmr_target(instance, action['target'])
            if uid is None:
                return
            spec = self._layout_spec(uid)
            allowed = {
                'set_box': ('x', 'y', 'width', 'height'),
                'set_translate': ('translate_x', 'translate_y'),
                'set_scroll': ('scroll_x', 'scroll_y'),
            }[op]
            for key in allowed:
                if key in action:
                    rendered = self._render_hmr_value(action[key], context)
                    spec[key] = int(rendered)
            return
        raise ValueError(f'unknown HMR action: {op}')

    def _run_hmr_callback(self, instance, actions, phase, seq, extra=None):
        if not actions:
            return
        frame = []
        self.reaction_frame_stack.append(frame)
        for action in actions:
            self._execute_hmr_action(instance, action, phase, seq, extra or {})
        self.reaction_frame_stack.pop()
        if frame:
            self.dispatch(frame, seq)

    def _install_runtime_effects(self, instance, revision, seq):
        for index, runtime in enumerate(revision.get('runtimes', []), start=1):
            runtime_instance = f"{instance['instance_id']}:{runtime['kind']}:{index}"
            row = {'seq': seq, 'instance_id': runtime_instance, 'generation_id': runtime['generation_id']}
            if runtime['kind'] == 'mutation':
                self.runtime_install(row)
            elif runtime['kind'] == 'event':
                self.event_runtime_install(row)
            elif runtime['kind'] == 'frame':
                self.frame_runtime_install(row)
            else:
                raise ValueError(f"unknown HMR runtime effect: {runtime['kind']}")
            instance['runtime_instances'].append((runtime['kind'], runtime_instance))

    def _dispose_runtime_effects(self, instance, seq):
        for kind, runtime_instance in reversed(instance['runtime_instances']):
            row = {'seq': seq, 'instance_id': runtime_instance}
            if kind == 'mutation':
                self.runtime_dispose(row)
            elif kind == 'event':
                self.event_runtime_dispose(row)
            elif kind == 'frame':
                self.frame_runtime_dispose(row)
            else:
                raise ValueError(f'unknown HMR runtime instance kind: {kind}')

    def _install_module(self, module_id, revision_id, seq, phase='install'):
        revision = self._revision(module_id, revision_id)
        controller_uid = revision['controller_uid']
        captures = {}
        for capture in revision.get('captures', []):
            target = capture['target']
            if target['kind'] == 'uid':
                uid = target['uid']
                captures[capture['name']] = uid if uid in self.nodes else None
            elif target['kind'] == 'live_role':
                controller = self.nodes[controller_uid]
                captures[capture['name']] = self.query_role(controller['owner_doc'], target['role'])
            else:
                raise ValueError(f"unknown HMR capture target: {target['kind']}")

        slot = revision.get('shared_slot')
        self.hmr_instance_counter += 1
        instance = {
            'instance_id': f"{module_id}#{self.hmr_instance_counter}",
            'module_id': module_id,
            'revision_id': revision_id,
            'install_index': self.hmr_instance_counter,
            'controller_uid': controller_uid,
            'captures': captures,
            'shared_slot': slot,
            'captured_state': self.shared_slots[slot] if slot else None,
            'state': dict(revision.get('initial_state', {})),
            'hook_active': True,
            'active': True,
            'runtime_instances': [],
        }
        self.hmr_instances.append(instance)
        self.hmr_active_instances[module_id] = instance
        self.hmr_active_revisions[module_id] = revision_id
        self._install_runtime_effects(instance, revision, seq)
        self._run_hmr_callback(instance, revision.get('install_actions', []), phase, seq)
        return instance

    def _dispose_module(self, module_id, seq, tx_id):
        instance = self.hmr_active_instances[module_id]
        revision = self._revision(module_id, instance['revision_id'])
        self._run_hmr_callback(
            instance,
            revision.get('dispose_actions', []),
            'dispose',
            seq,
            {'tx_id': tx_id},
        )
        self._dispose_runtime_effects(instance, seq)
        instance['active'] = False
        if revision.get('signal_lifetime', 'disconnect') != 'retain':
            instance['hook_active'] = False

    def hmr_bootstrap(self, row):
        if self.hmr_bootstrapped:
            raise ValueError('HMR bootstrap appears more than once')
        revisions = dict(self.hmr_initial)
        order = self._topological_modules(revisions, revisions, dependencies_first=True)
        for module_id in order:
            self._install_module(module_id, revisions[module_id], row['seq'], phase='bootstrap')
        self.hmr_bootstrapped = True

    def _trace_acceptance(self, changes):
        graph = self._active_graph()
        parents = self._parents(graph)
        entries = set(self.hmr_entries)
        boundaries = defaultdict(set)

        for changed_root in sorted(changes):
            active_revision = self._revision(changed_root)
            if active_revision.get('self_accept', False):
                boundaries[changed_root].add(changed_root)
                continue

            queue = deque([(changed_root, changed_root)])
            seen_edges = set()
            accepted_any = False
            while queue:
                child, root = queue.popleft()
                parent_list = parents.get(child, [])
                if not parent_list:
                    return False, {}
                for parent in parent_list:
                    edge = (parent, child, root)
                    if edge in seen_edges:
                        continue
                    seen_edges.add(edge)
                    parent_revision = self._revision(parent)
                    if child in parent_revision.get('accepts', []):
                        boundaries[parent].add(root)
                        accepted_any = True
                        continue
                    if parent in entries:
                        return False, {}
                    queue.append((parent, root))
            if not accepted_any:
                return False, {}

        return True, {key: sorted(values) for key, values in boundaries.items()}

    def hmr_transaction(self, row):
        if not self.hmr_bootstrapped:
            raise ValueError('HMR transaction before bootstrap')
        tx_id = row['tx_id']
        if tx_id in self.hmr_seen_transactions:
            return
        self.hmr_seen_transactions.add(tx_id)

        changes = dict(row['changes'])
        for module_id, revision_id in changes.items():
            if module_id not in self.hmr_active_revisions:
                raise ValueError(f'unknown changed module: {module_id}')
            self._revision(module_id, revision_id)

        accepted, boundaries = self._trace_acceptance(changes)
        if not accepted:
            return

        pre_revisions = dict(self.hmr_active_revisions)
        for module_id in self._topological_modules(changes, pre_revisions, dependencies_first=False):
            self._dispose_module(module_id, row['seq'], tx_id)

        post_revisions = dict(pre_revisions)
        post_revisions.update(changes)
        for module_id in self._topological_modules(changes, post_revisions, dependencies_first=True):
            self._install_module(module_id, changes[module_id], row['seq'])

        for boundary_module in sorted(boundaries):
            instance = self.hmr_active_instances[boundary_module]
            revision = self._revision(boundary_module)
            self._run_hmr_callback(
                instance,
                revision.get('accept_actions', []),
                'accept',
                row['seq'],
                {'tx_id': tx_id, 'accepted': ','.join(boundaries[boundary_module])},
            )

    def hmr_signal(self, row):
        topic = row['topic']
        extra = {
            'signal_topic': topic,
            'payload': row.get('payload', ''),
            'signal_id': row.get('signal_id', ''),
        }
        for instance in sorted(self.hmr_instances, key=lambda item: item['install_index']):
            if not instance['hook_active']:
                continue
            revision = self._revision(instance['module_id'], instance['revision_id'])
            actions = revision.get('signals', {}).get(topic, [])
            self._run_hmr_callback(instance, actions, 'signal', row['seq'], extra)

    def apply(self, row):
        if row['_source'] != 'hmr':
            return super().apply(row)

        kind = row['kind']
        if kind == 'hmr_bootstrap':
            self.hmr_bootstrap(row)
        elif kind == 'hmr_transaction':
            self.hmr_transaction(row)
        elif kind == 'hmr_signal':
            self.hmr_signal(row)
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
            raise ValueError(f'unknown HMR continuation record: {kind}')
