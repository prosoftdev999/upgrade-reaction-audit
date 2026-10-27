from __future__ import annotations

from dataclasses import dataclass

from event_model import EventLifecycleModel


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self):
        return max(0, self.w) * max(0, self.h)


def intersect(a: Rect | None, b: Rect | None):
    if a is None or b is None:
        return None
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.w, b.x + b.w)
    y2 = min(a.y + a.h, b.y + b.h)
    if x2 <= x1 or y2 <= y1:
        return Rect(x1, y1, 0, 0)
    return Rect(x1, y1, x2 - x1, y2 - y1)


class FrameLifecycleModel(EventLifecycleModel):
    """Adds a captured render-frame observer continuation to the event replay."""

    def __init__(self, documents, definitions, observer_runtime, event_runtime, frame_runtime):
        super().__init__(documents, definitions, observer_runtime, event_runtime)
        self.frame_generations = {
            item['generation_id']: item for item in frame_runtime.get('generations', [])
        }
        self.viewports = {
            doc: Rect(0, 0, int(spec['width']), int(spec['height']))
            for doc, spec in frame_runtime.get('viewports', {}).items()
        }
        self.layout = {}
        for uid, spec in frame_runtime.get('layout', {}).items():
            self.layout[uid] = {
                'x': int(spec.get('x', 0)),
                'y': int(spec.get('y', 0)),
                'width': int(spec.get('width', 0)),
                'height': int(spec.get('height', 0)),
                'scroll_x': int(spec.get('scroll_x', 0)),
                'scroll_y': int(spec.get('scroll_y', 0)),
                'translate_x': int(spec.get('translate_x', 0)),
                'translate_y': int(spec.get('translate_y', 0)),
                'clip': bool(spec.get('clip', False)),
            }
        self.frame_observers = []
        self.frame_registration_index = 0
        self.frame_jobs = []

    def _layout_parent(self, uid):
        node = self.nodes[uid]
        parent = node.get('parent_uid')
        if parent is not None and self.nodes[parent]['kind'] == 'shadow_root':
            return self.nodes[parent]['host_uid']
        return parent

    def _layout_spec(self, uid):
        return self.layout.setdefault(uid, {
            'x': 0, 'y': 0, 'width': 0, 'height': 0,
            'scroll_x': 0, 'scroll_y': 0,
            'translate_x': 0, 'translate_y': 0, 'clip': False,
        })

    def _global_rect(self, uid):
        if uid not in self.nodes or not self.is_connected(uid):
            return None
        node = self.nodes[uid]
        if node['kind'] == 'shadow_root':
            uid = node['host_uid']
        spec = self._layout_spec(uid)
        x = spec['x'] + spec['translate_x']
        y = spec['y'] + spec['translate_y']
        w = spec['width']
        h = spec['height']
        current = self._layout_parent(uid)
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            parent_node = self.nodes[current]
            if parent_node['kind'] == 'document_root':
                break
            ps = self._layout_spec(current)
            x += ps['x'] + ps['translate_x'] - ps['scroll_x']
            y += ps['y'] + ps['translate_y'] - ps['scroll_y']
            current = self._layout_parent(current)
        return Rect(x, y, w, h)

    def _ancestor_chain(self, uid):
        chain = []
        current = self._layout_parent(uid)
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            chain.append(current)
            node = self.nodes[current]
            if node['kind'] == 'document_root':
                break
            current = self._layout_parent(current)
        return chain

    def _visible_rect(self, uid, root_uid=None):
        target = self._global_rect(uid)
        if target is None:
            return None
        doc = self.nodes[uid]['owner_doc']
        visible = intersect(target, self.viewports.get(doc))
        reached_root = root_uid is None
        for ancestor in self._ancestor_chain(uid):
            if ancestor == root_uid:
                reached_root = True
            if ancestor in self.nodes and self.nodes[ancestor]['kind'] != 'document_root':
                spec = self._layout_spec(ancestor)
                if spec['clip'] or ancestor == root_uid:
                    visible = intersect(visible, self._global_rect(ancestor))
            if ancestor == root_uid:
                break
        if root_uid is not None and not reached_root:
            return Rect(target.x, target.y, 0, 0)
        return visible

    def _resolve_frame_target(self, controller_uid, target):
        kind = target['kind']
        if kind == 'uid':
            uid = target['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            controller = self.nodes[controller_uid]
            return self.query_role(controller['owner_doc'], target['role'])
        if kind == 'viewport':
            return f"@viewport:{self.nodes[controller_uid]['owner_doc']}"
        raise ValueError(f'unknown frame target: {kind}')

    def _resolve_frame_capture(self, controller_uid, target):
        return self._resolve_frame_target(controller_uid, target)

    def frame_runtime_install(self, row):
        generation = self.frame_generations[row['generation_id']]
        for spec in generation.get('observers', []):
            controller_uid = spec['controller_uid']
            target_uid = self._resolve_frame_target(controller_uid, spec['target'])
            if target_uid is None or target_uid.startswith('@viewport:'):
                continue
            root_uid = None
            if spec['kind'] == 'intersection':
                root = spec.get('root', {'kind': 'viewport'})
                resolved = self._resolve_frame_target(controller_uid, root)
                root_uid = None if resolved and resolved.startswith('@viewport:') else resolved
            captures = {
                cap['name']: self._resolve_frame_capture(controller_uid, cap)
                for cap in spec.get('captures', [])
            }
            slot = spec.get('shared_slot')
            captured_state = self.shared_slots[slot] if slot else None
            self.frame_registration_index += 1
            self.frame_observers.append({
                'registration_index': self.frame_registration_index,
                'runtime_instance': row['instance_id'],
                'generation_id': row['generation_id'],
                'name': spec['name'],
                'kind': spec['kind'],
                'controller_uid': controller_uid,
                'target_uid': target_uid,
                'root_uid': root_uid,
                'thresholds': sorted(int(v) for v in spec.get('thresholds_bp', [])),
                'captures': captures,
                'shared_slot': slot,
                'captured_state': captured_state,
                'dispose': spec.get('dispose', 'disconnect'),
                'actions': list(spec.get('actions', [])),
                'active': True,
                'last_measurement': None,
            })

    def frame_runtime_dispose(self, row):
        for observer in self.frame_observers:
            if observer['runtime_instance'] != row['instance_id'] or not observer['active']:
                continue
            if observer['dispose'] == 'retain':
                continue
            if observer['dispose'] != 'disconnect':
                raise ValueError(f"unknown frame observer disposal mode: {observer['dispose']}")
            observer['active'] = False

    def _resize_measurement(self, observer):
        uid = observer['target_uid']
        if uid not in self.nodes or not self.is_connected(uid):
            return {'width': 0, 'height': 0}
        spec = self._layout_spec(uid)
        return {'width': spec['width'], 'height': spec['height']}

    def _intersection_measurement(self, observer):
        uid = observer['target_uid']
        target = self._global_rect(uid)
        if target is None or target.area == 0:
            ratio = 0
        else:
            visible = self._visible_rect(uid, observer['root_uid'])
            ratio = 0 if visible is None else (visible.area * 10000) // target.area
        thresholds = observer['thresholds']
        bucket = sum(1 for threshold in thresholds if ratio >= threshold)
        return {'ratio_bp': ratio, 'bucket': bucket, 'is_intersecting': ratio > 0}

    def _frame_measurement(self, observer):
        if observer['kind'] == 'resize':
            return self._resize_measurement(observer)
        if observer['kind'] == 'intersection':
            return self._intersection_measurement(observer)
        raise ValueError(f"unknown frame observer kind: {observer['kind']}")

    def _measurement_changed(self, observer, current):
        previous = observer['last_measurement']
        if previous is None:
            return True
        if observer['kind'] == 'resize':
            return (previous['width'], previous['height']) != (current['width'], current['height'])
        return (
            previous['bucket'] != current['bucket']
            or previous['is_intersecting'] != current['is_intersecting']
        )

    def _frame_context(self, observer, measurement):
        captured = observer['captured_state'] or {}
        live = self.shared_slots[observer['shared_slot']] if observer['shared_slot'] else {}
        target = self.nodes[observer['target_uid']]
        context = {
            'observer_name': observer['name'],
            'generation_id': observer['generation_id'],
            'target_uid': observer['target_uid'],
            'target_doc': target['owner_doc'],
            'root_uid': observer['root_uid'] or '',
            'controller_uid': observer['controller_uid'],
            'controller_doc': self.nodes[observer['controller_uid']]['owner_doc'],
            'captured_revision': captured.get('revision'),
            'captured_lane': captured.get('lane'),
            'live_revision': live.get('revision'),
            'live_lane': live.get('lane'),
            'width': '', 'height': '', 'ratio_bp': '', 'bucket': '', 'is_intersecting': '',
        }
        context.update(measurement)
        if 'is_intersecting' in measurement:
            context['is_intersecting'] = '1' if measurement['is_intersecting'] else '0'
        return context

    def _render_frame_value(self, value, context):
        if value is None or not isinstance(value, str):
            return value
        safe = {key: '' if item is None else str(item) for key, item in context.items()}
        return value.format_map(safe)

    def _resolve_frame_action_target(self, observer, target):
        kind = target['kind']
        if kind == 'observed':
            return observer['target_uid']
        if kind == 'controller':
            return observer['controller_uid']
        if kind == 'capture':
            return observer['captures'].get(target['name'])
        if kind == 'uid':
            uid = target['uid']
            return uid if uid in self.nodes else None
        if kind == 'live_role':
            controller = self.nodes[observer['controller_uid']]
            return self.query_role(controller['owner_doc'], target['role'])
        raise ValueError(f'unknown frame action target: {kind}')

    def _freeze_job_action(self, observer, action, context):
        frozen = {'op': action['op']}
        if 'target' in action:
            frozen['uid'] = self._resolve_frame_action_target(observer, action['target'])
        for key in ('name', 'value', 'x', 'y', 'width', 'height', 'scroll_x', 'scroll_y', 'translate_x', 'translate_y'):
            if key in action:
                value = self._render_frame_value(action[key], context)
                if key in {'x', 'y', 'width', 'height', 'scroll_x', 'scroll_y', 'translate_x', 'translate_y'}:
                    value = int(value)
                frozen[key] = value
        return frozen

    def _enqueue_frame_job(self, observer, action, context):
        key = self._render_frame_value(action.get('job_key'), context)
        coalesce = action.get('coalesce', 'none')
        job = {
            'owner': f"observer:{observer['registration_index']}",
            'key': key,
            'actions': [self._freeze_job_action(observer, sub, context) for sub in action.get('actions', [])],
        }
        if key is None or coalesce == 'none':
            self.frame_jobs.append(job)
            return
        existing = next((i for i, pending in enumerate(self.frame_jobs)
                         if pending['owner'] == job['owner'] and pending['key'] == key), None)
        if existing is None:
            self.frame_jobs.append(job)
        elif coalesce == 'replace':
            self.frame_jobs[existing] = job
        elif coalesce == 'keep_first':
            return
        else:
            raise ValueError(f'unknown frame job coalescing mode: {coalesce}')

    def _execute_frozen_frame_action(self, action, trigger_seq):
        uid = action.get('uid')
        if uid is None:
            return
        if action['op'] == 'set_attr':
            self._set_attr(uid, action['name'], action['value'], trigger_seq)
            return
        spec = self._layout_spec(uid)
        if action['op'] == 'set_box':
            for key in ('x', 'y', 'width', 'height'):
                if key in action:
                    spec[key] = action[key]
            return
        if action['op'] == 'set_scroll':
            for key in ('scroll_x', 'scroll_y'):
                if key in action:
                    spec[key] = action[key]
            return
        if action['op'] == 'set_translate':
            for key in ('translate_x', 'translate_y'):
                if key in action:
                    spec[key] = action[key]
            return
        raise ValueError(f"unknown frozen frame action: {action['op']}")

    def _execute_frame_action(self, observer, action, context, trigger_seq):
        if action['op'] == 'enqueue_frame':
            self._enqueue_frame_job(observer, action, context)
            return
        frozen = self._freeze_job_action(observer, action, context)
        self._execute_frozen_frame_action(frozen, trigger_seq)

    def _drain_frame_jobs(self, seq):
        pending = self.frame_jobs
        self.frame_jobs = []
        for job in pending:
            frame = []
            self.reaction_frame_stack.append(frame)
            for action in job['actions']:
                self._execute_frozen_frame_action(action, seq)
            self.reaction_frame_stack.pop()
            if frame:
                self.dispatch(frame, seq)

    def captured_frame_job(self, row):
        owner = row['owner_uid']
        key = row.get('job_key')
        coalesce = row.get('coalesce', 'none')
        actions = []
        for action in row.get('actions', []):
            target = action.get('target')
            uid = None
            if target:
                kind = target['kind']
                if kind == 'uid':
                    uid = target['uid'] if target['uid'] in self.nodes else None
                elif kind == 'live_role':
                    uid = self.query_role(self.nodes[owner]['owner_doc'], target['role'])
                else:
                    raise ValueError(f'unknown captured frame target: {kind}')
            frozen = {'op': action['op'], 'uid': uid}
            for name in ('name', 'value', 'x', 'y', 'width', 'height', 'scroll_x', 'scroll_y', 'translate_x', 'translate_y'):
                if name in action:
                    frozen[name] = action[name]
            actions.append(frozen)
        job = {'owner': f"capture:{owner}", 'key': key, 'actions': actions}
        if key is None or coalesce == 'none':
            self.frame_jobs.append(job)
            return
        existing = next((i for i, pending in enumerate(self.frame_jobs)
                         if pending['owner'] == job['owner'] and pending['key'] == key), None)
        if existing is None:
            self.frame_jobs.append(job)
        elif coalesce == 'replace':
            self.frame_jobs[existing] = job
        elif coalesce == 'keep_first':
            return
        else:
            raise ValueError(f'unknown captured frame coalescing mode: {coalesce}')

    def render_checkpoint(self, row):
        if self.scope_stack:
            raise ValueError('render checkpoint occurred inside a reaction scope')
        self._drain_frame_jobs(row['seq'])
        snapshots = []
        for observer in sorted(self.frame_observers, key=lambda item: item['registration_index']):
            if not observer['active']:
                continue
            measurement = self._frame_measurement(observer)
            changed = self._measurement_changed(observer, measurement)
            observer['last_measurement'] = dict(measurement)
            if changed:
                snapshots.append((observer, measurement))
        for observer, measurement in snapshots:
            context = self._frame_context(observer, measurement)
            frame = []
            self.reaction_frame_stack.append(frame)
            for action in observer['actions']:
                self._execute_frame_action(observer, action, context, row['seq'])
            self.reaction_frame_stack.pop()
            if frame:
                self.dispatch(frame, row['seq'])

    def box_update(self, row):
        spec = self._layout_spec(row['uid'])
        for key in ('x', 'y', 'width', 'height'):
            if key in row:
                spec[key] = int(row[key])

    def scroll_update(self, row):
        spec = self._layout_spec(row['uid'])
        spec['scroll_x'] = int(row.get('scroll_x', spec['scroll_x']))
        spec['scroll_y'] = int(row.get('scroll_y', spec['scroll_y']))

    def translate_update(self, row):
        spec = self._layout_spec(row['uid'])
        spec['translate_x'] = int(row.get('translate_x', spec['translate_x']))
        spec['translate_y'] = int(row.get('translate_y', spec['translate_y']))

    def frame_state_snapshot(self):
        return {
            'layout': {uid: dict(self.layout[uid]) for uid in sorted(self.layout)},
            'pending_frame_jobs': [
                {'owner': job['owner'], 'key': job['key'], 'actions': job['actions']}
                for job in self.frame_jobs
            ],
        }

    def apply(self, row):
        if row['_source'] != 'frame':
            return super().apply(row)
        kind = row['kind']
        if kind == 'frame_runtime_install':
            self.frame_runtime_install(row)
        elif kind == 'frame_runtime_dispose':
            self.frame_runtime_dispose(row)
        elif kind == 'frame_job':
            self.captured_frame_job(row)
        elif kind == 'render_checkpoint':
            self.render_checkpoint(row)
        elif kind == 'box_update':
            self.box_update(row)
        elif kind == 'scroll_update':
            self.scroll_update(row)
        elif kind == 'translate_update':
            self.translate_update(row)
        elif kind == 'shared_mutate':
            self.shared_mutate(row)
        elif kind == 'shared_replace':
            self.shared_replace(row)
        elif kind == 'microtask_checkpoint':
            self.microtask_checkpoint(row)
        elif kind == 'event_task_checkpoint':
            self.event_task_checkpoint(row)
        elif kind == 'checkpoint':
            self.output_checkpoint(row)
        elif kind == 'set_attr':
            self.set_attr(row)
        elif kind == 'remove_attr':
            self.remove_attr(row)
        else:
            raise ValueError(f'unknown frame continuation record: {kind}')
