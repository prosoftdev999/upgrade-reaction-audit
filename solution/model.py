from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass


@dataclass
class Reaction:
    trigger_seq: int
    uid: str
    definition_id: str
    callback: str
    name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    old_doc: str | None = None
    new_doc: str | None = None

    def context(self):
        return {
            'uid': self.uid,
            'definition_id': self.definition_id,
            'callback': self.callback,
            'name': self.name,
            'old_value': self.old_value,
            'new_value': self.new_value,
            'old_doc': self.old_doc,
            'new_doc': self.new_doc,
        }


class LifecycleModel:
    def __init__(self, documents, definitions):
        docs = documents['documents']
        self.documents = {d['document_id']: d for d in docs}
        self.document_order = [d['document_id'] for d in sorted(docs, key=lambda d: d['order'])]
        self.registry_for_document = {d['document_id']: d['registry_id'] for d in docs}
        self.definitions = {d['definition_id']: d for d in definitions['definitions']}
        self.available_definitions = {}

        self.nodes = {}
        for doc in docs:
            self.nodes[doc['root_uid']] = {
                'uid': doc['root_uid'],
                'kind': 'document_root',
                'local_name': '#document',
                'owner_doc': doc['document_id'],
                'parent_uid': None,
                'children': [],
                'shadow_root_uid': None,
                'host_uid': None,
                'attrs': OrderedDict(),
                'creation_seq': -1,
                'custom_state': 'not_candidate',
                'definition_id': None,
                'captures': {},
            }

        self.scope_stack = []
        self.reaction_frame_stack = []
        self.microtasks = []
        self.dispatched_since_checkpoint = []
        self.dispatch_index = 0
        self.checkpoints = []

    def is_candidate(self, node):
        return node['kind'] == 'element' and '-' in node['local_name']

    def is_connected(self, uid):
        current = uid
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            node = self.nodes[current]
            if node['kind'] == 'document_root':
                return True
            current = node['host_uid'] if node['kind'] == 'shadow_root' else node['parent_uid']
        return False

    def shadow_including_children(self, uid):
        node = self.nodes[uid]
        children = []
        if node['kind'] == 'element' and node['shadow_root_uid']:
            children.extend(self.nodes[node['shadow_root_uid']]['children'])
        children.extend(node.get('children', []))
        return children

    def shadow_including_preorder(self, uid):
        ordered = [uid]
        for child in self.shadow_including_children(uid):
            ordered.extend(self.shadow_including_preorder(child))
        return ordered

    def custom_elements_in_subtree(self, uid, reverse=False):
        ordered = [
            current for current in self.shadow_including_preorder(uid)
            if self.nodes[current]['custom_state'] == 'custom'
        ]
        if reverse:
            ordered.reverse()
        return ordered

    def candidate_order(self, document_id):
        root_uid = self.documents[document_id]['root_uid']
        connected = []
        for child in self.nodes[root_uid]['children']:
            for uid in self.shadow_including_preorder(child):
                if self.is_candidate(self.nodes[uid]):
                    connected.append(uid)

        connected_set = set(connected)
        detached = [
            node['uid'] for node in self.nodes.values()
            if self.is_candidate(node)
            and node['owner_doc'] == document_id
            and node['uid'] not in connected_set
        ]
        detached.sort(key=lambda uid: self.nodes[uid]['creation_seq'])
        return connected + detached

    def query_role(self, document_id, role):
        if role is None:
            return None
        root_uid = self.documents[document_id]['root_uid']
        connected = []
        for child in self.nodes[root_uid]['children']:
            connected.extend(self.shadow_including_preorder(child))
        for uid in connected:
            node = self.nodes[uid]
            if node['kind'] == 'element' and node['owner_doc'] == document_id and node['attrs'].get('data-role') == role:
                return uid
        detached = [
            node for node in self.nodes.values()
            if node['kind'] == 'element'
            and node['owner_doc'] == document_id
            and not self.is_connected(node['uid'])
            and node['attrs'].get('data-role') == role
        ]
        detached.sort(key=lambda node: node['creation_seq'])
        return detached[0]['uid'] if detached else None

    def queue_reaction(self, reaction):
        if self.reaction_frame_stack:
            self.reaction_frame_stack[-1].append(reaction)
            return
        if self.scope_stack:
            self.scope_stack[-1]['queue'].append(reaction)
            return
        raise ValueError(f'lifecycle reaction outside a reaction scope: {reaction}')

    def _render(self, value, subject_uid, context):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        live = dict(context)
        live['owner_doc'] = self.nodes[subject_uid]['owner_doc']
        safe = {k: '' if v is None else str(v) for k, v in live.items()}
        return value.format_map(safe)

    def _resolve_target(self, subject_uid, target):
        kind = target['kind']
        if kind == 'self':
            return subject_uid
        if kind == 'capture':
            return self.nodes[subject_uid]['captures'].get(target['name'])
        if kind == 'live_role':
            role = target.get('role')
            if 'role_from_attr' in target:
                role = self.nodes[subject_uid]['attrs'].get(target['role_from_attr'])
            return self.query_role(self.nodes[subject_uid]['owner_doc'], role)
        raise ValueError(f'unknown target kind: {kind}')

    def _program_for(self, reaction):
        definition = self.definitions[reaction.definition_id]
        programs = definition.get('programs', {})
        actions = list(programs.get(reaction.callback, []))
        if reaction.callback == 'attribute_changed':
            actions.extend(programs.get(f'attribute_changed:{reaction.name}', []))
        return actions

    def _enqueue_microtask(self, subject_uid, action, context):
        key = self._render(action.get('job_key'), subject_uid, context)
        coalesce = action.get('coalesce', 'none')
        job = {
            'owner_uid': subject_uid,
            'key': key,
            'actions': action.get('actions', []),
            'context': dict(context),
        }
        if key is None or coalesce == 'none':
            self.microtasks.append(job)
            return

        existing = None
        for index, pending in enumerate(self.microtasks):
            if pending['owner_uid'] == subject_uid and pending['key'] == key:
                existing = index
                break
        if existing is None:
            self.microtasks.append(job)
        elif coalesce == 'replace':
            self.microtasks[existing] = job
        elif coalesce == 'keep_first':
            return
        else:
            raise ValueError(f'unknown microtask coalescing mode: {coalesce}')

    def _set_attr(self, uid, name, value, trigger_seq):
        if uid is None:
            return
        node = self.nodes[uid]
        old_value = node['attrs'].get(name)
        node['attrs'][name] = value
        if node['custom_state'] == 'custom' and old_value != value:
            definition = self.definitions[node['definition_id']]
            if name in definition['observed_attributes']:
                self.queue_reaction(Reaction(
                    trigger_seq, uid, node['definition_id'], 'attribute_changed',
                    name=name, old_value=old_value, new_value=value
                ))

    def _remove_attr(self, uid, name, trigger_seq):
        if uid is None:
            return
        node = self.nodes[uid]
        if name not in node['attrs']:
            return
        old_value = node['attrs'].pop(name)
        if node['custom_state'] == 'custom':
            definition = self.definitions[node['definition_id']]
            if name in definition['observed_attributes']:
                self.queue_reaction(Reaction(
                    trigger_seq, uid, node['definition_id'], 'attribute_changed',
                    name=name, old_value=old_value, new_value=None
                ))

    def _move_uid(self, uid, parent_uid, trigger_seq):
        if uid is None or parent_uid is None:
            return
        if self.is_connected(uid):
            for current in self.custom_elements_in_subtree(uid, reverse=True):
                node = self.nodes[current]
                self.queue_reaction(Reaction(trigger_seq, current, node['definition_id'], 'disconnected'))
        self.detach_from_parent(uid)
        self.nodes[parent_uid]['children'].append(uid)
        self.nodes[uid]['parent_uid'] = parent_uid
        if self.is_connected(uid):
            for current in self.custom_elements_in_subtree(uid):
                node = self.nodes[current]
                self.queue_reaction(Reaction(trigger_seq, current, node['definition_id'], 'connected'))

    def _adopt_uid(self, uid, new_doc, trigger_seq):
        if uid is None:
            return
        if self.is_connected(uid) or self.nodes[uid]['parent_uid'] is not None:
            raise ValueError('program adoption requires a detached subtree root')
        affected = self.shadow_including_preorder(uid)
        old_documents = {current: self.nodes[current]['owner_doc'] for current in affected}
        for current in affected:
            self.nodes[current]['owner_doc'] = new_doc
        for current in affected:
            node = self.nodes[current]
            old_doc = old_documents[current]
            if node['custom_state'] == 'custom' and old_doc != new_doc:
                self.queue_reaction(Reaction(
                    trigger_seq, current, node['definition_id'], 'adopted',
                    old_doc=old_doc, new_doc=new_doc
                ))

    def _execute_actions(self, subject_uid, actions, context, trigger_seq):
        for action in actions:
            kind = action['op']
            if kind == 'set_attr':
                uid = self._resolve_target(subject_uid, action['target'])
                value = self._render(action['value'], subject_uid, context)
                self._set_attr(uid, action['name'], value, trigger_seq)
            elif kind == 'enqueue_microtask':
                self._enqueue_microtask(subject_uid, action, context)
            else:
                raise ValueError(f'unknown program operation: {kind}')

    def _dispatch_one(self, reaction, dispatch_seq):
        self.dispatch_index += 1
        self.dispatched_since_checkpoint.append({
            'dispatch_index': self.dispatch_index,
            'dispatch_seq': dispatch_seq,
            'trigger_seq': reaction.trigger_seq,
            'uid': reaction.uid,
            'definition_id': reaction.definition_id,
            'callback': reaction.callback,
            'name': reaction.name,
            'old_value': reaction.old_value,
            'new_value': reaction.new_value,
            'old_doc': reaction.old_doc,
            'new_doc': reaction.new_doc,
        })

        child_queue = []
        self.reaction_frame_stack.append(child_queue)
        self._execute_actions(reaction.uid, self._program_for(reaction), reaction.context(), dispatch_seq)
        self.reaction_frame_stack.pop()
        return child_queue

    def dispatch(self, reactions, dispatch_seq):
        queue = deque(reactions)
        while queue:
            reaction = queue.popleft()
            child_queue = self._dispatch_one(reaction, dispatch_seq)
            if child_queue:
                self.dispatch(child_queue, dispatch_seq)

    def begin_scope(self, row):
        self.scope_stack.append({'scope_id': row['scope_id'], 'queue': []})

    def end_scope(self, row):
        if not self.scope_stack or self.scope_stack[-1]['scope_id'] != row['scope_id']:
            raise ValueError(f'reaction-scope mismatch at seq {row["seq"]}')
        scope = self.scope_stack.pop()
        self.dispatch(scope['queue'], row['seq'])

    def define(self, row):
        definition = self.definitions[row['definition_id']]
        if definition['registry_id'] != row['registry_id']:
            raise ValueError('definition catalog and registry capture disagree')
        key = (row['registry_id'], definition['local_name'])
        if key in self.available_definitions:
            raise ValueError(f'duplicate definition for {key}')
        self.available_definitions[key] = row['definition_id']

    def definition_available_for(self, uid):
        node = self.nodes[uid]
        registry_id = self.registry_for_document[node['owner_doc']]
        return self.available_definitions.get((registry_id, node['local_name']))

    def _resolve_captures(self, uid, definition):
        captures = {}
        for spec in definition.get('captures', []):
            role = self.nodes[uid]['attrs'].get(spec['role_from_attr'])
            captures[spec['name']] = self.query_role(self.nodes[uid]['owner_doc'], role)
        return captures

    def _drain_microtasks(self, checkpoint_seq):
        pending = self.microtasks
        self.microtasks = []
        for job in pending:
            if job['owner_uid'] not in self.nodes:
                continue
            frame = []
            self.reaction_frame_stack.append(frame)
            self._execute_actions(job['owner_uid'], job['actions'], job['context'], checkpoint_seq)
            self.reaction_frame_stack.pop()
            if frame:
                self.dispatch(frame, checkpoint_seq)

    def microtask_checkpoint(self, row):
        if self.scope_stack:
            raise ValueError('microtask checkpoint occurred inside a reaction scope')

        self._drain_microtasks(row['seq'])

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

    def create(self, row):
        uid = row['uid']
        if uid in self.nodes:
            raise ValueError(f'duplicate node uid: {uid}')
        local_name = row['local_name']
        custom_state = 'uncustomized' if '-' in local_name else 'not_candidate'
        self.nodes[uid] = {
            'uid': uid,
            'kind': 'element',
            'local_name': local_name,
            'owner_doc': row['owner_doc'],
            'parent_uid': None,
            'children': [],
            'shadow_root_uid': None,
            'host_uid': None,
            'attrs': OrderedDict(row.get('attrs', {}).items()),
            'creation_seq': row['seq'],
            'custom_state': custom_state,
            'definition_id': None,
            'captures': {},
        }

    def attach_shadow(self, row):
        host = self.nodes[row['host_uid']]
        shadow_uid = row['shadow_uid']
        if shadow_uid in self.nodes or host['shadow_root_uid'] is not None:
            raise ValueError('invalid shadow-root attachment')
        self.nodes[shadow_uid] = {
            'uid': shadow_uid,
            'kind': 'shadow_root',
            'local_name': '#shadow-root',
            'owner_doc': host['owner_doc'],
            'parent_uid': None,
            'children': [],
            'shadow_root_uid': None,
            'host_uid': row['host_uid'],
            'attrs': OrderedDict(),
            'creation_seq': row['seq'],
            'custom_state': 'not_candidate',
            'definition_id': None,
            'captures': {},
        }
        host['shadow_root_uid'] = shadow_uid

    def detach_from_parent(self, uid):
        node = self.nodes[uid]
        parent_uid = node['parent_uid']
        if parent_uid is not None:
            self.nodes[parent_uid]['children'].remove(uid)
            node['parent_uid'] = None

    def insert(self, row):
        uid = row['uid']
        if self.nodes[uid]['parent_uid'] is not None or self.is_connected(uid):
            raise ValueError(f'insert requires detached subtree root: {uid}')
        parent_uid = row['parent_uid']
        self.nodes[parent_uid]['children'].append(uid)
        self.nodes[uid]['parent_uid'] = parent_uid
        if self.is_connected(uid):
            for current in self.custom_elements_in_subtree(uid):
                node = self.nodes[current]
                self.queue_reaction(Reaction(row['seq'], current, node['definition_id'], 'connected'))

    def remove(self, row):
        uid = row['uid']
        if self.is_connected(uid):
            for current in self.custom_elements_in_subtree(uid, reverse=True):
                node = self.nodes[current]
                self.queue_reaction(Reaction(row['seq'], current, node['definition_id'], 'disconnected'))
        self.detach_from_parent(uid)

    def move(self, row):
        self._move_uid(row['uid'], row['parent_uid'], row['seq'])

    def adopt(self, row):
        self._adopt_uid(row['uid'], row['new_doc'], row['seq'])

    def set_attr(self, row):
        self._set_attr(row['uid'], row['name'], row['value'], row['seq'])

    def remove_attr(self, row):
        self._remove_attr(row['uid'], row['name'], row['seq'])

    def output_checkpoint(self, row):
        if self.scope_stack:
            raise ValueError('output checkpoint occurred inside a reaction scope')
        candidates = [node for node in self.nodes.values() if self.is_candidate(node)]
        candidates.sort(key=lambda node: node['creation_seq'])
        instances = [{
            'uid': node['uid'],
            'local_name': node['local_name'],
            'owner_doc': node['owner_doc'],
            'connected': self.is_connected(node['uid']),
            'custom_state': node['custom_state'],
            'definition_id': node['definition_id'],
        } for node in candidates]

        self.checkpoints.append({
            'checkpoint_id': row['checkpoint_id'],
            'seq': row['seq'],
            'instances': instances,
            'reactions': self.dispatched_since_checkpoint,
        })
        self.dispatched_since_checkpoint = []

    def apply(self, row):
        source = row['_source']
        kind = row['kind']

        if source == 'scheduler':
            if kind == 'scope_begin':
                self.begin_scope(row)
            elif kind == 'scope_end':
                self.end_scope(row)
            elif kind == 'microtask_checkpoint':
                self.microtask_checkpoint(row)
            elif kind == 'checkpoint':
                self.output_checkpoint(row)
            else:
                raise ValueError(f'unknown scheduler record: {kind}')
            return

        if source == 'registry':
            if kind != 'define':
                raise ValueError(f'unknown registry record: {kind}')
            self.define(row)
            return

        if source != 'dom':
            raise ValueError(f'unknown source: {source}')

        if kind == 'create':
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
            raise ValueError(f'unknown DOM record: {kind}')

    def result(self):
        if self.scope_stack:
            raise ValueError('capture ended with an open reaction scope')
        return {'version': 1, 'checkpoints': self.checkpoints}

    def final_dom_snapshot(self):
        rows = []
        for node in self.nodes.values():
            if node['kind'] == 'document_root':
                continue
            rows.append({
                'uid': node['uid'],
                'kind': node['kind'],
                'local_name': node['local_name'],
                'owner_doc': node['owner_doc'],
                'parent_uid': node['parent_uid'],
                'host_uid': node['host_uid'],
                'shadow_root_uid': node['shadow_root_uid'],
                'attrs': dict(node['attrs']),
            })
        rows.sort(key=lambda row: row['uid'])
        return {'nodes': rows}
