import json
from pathlib import Path

DATA = Path('/app/data')


def load_json(name):
    return json.loads((DATA / name).read_text())


def load_jsonl(name, source):
    rows = []
    with (DATA / name).open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row['_source'] = source
            rows.append(row)
    return rows


def load_capture():
    documents = load_json('documents.json')
    definitions = load_json('definition_catalog.json')
    prefix_snapshot = load_json('final_dom_snapshot.json')
    observer_snapshot = load_json('final_extension_snapshot.json')
    event_snapshot = load_json('final_event_snapshot.json')
    frame_snapshot = load_json('final_frame_snapshot.json')
    observer_runtime = load_json('observer_runtime.json')
    event_runtime = load_json('event_runtime.json')
    frame_runtime = load_json('frame_runtime.json')
    hmr_catalog = load_json('hmr_module_catalog.json')

    rows = []
    rows += load_jsonl('dom_capture.jsonl', 'dom')
    rows += load_jsonl('registry_capture.jsonl', 'registry')
    rows += load_jsonl('scheduler_capture.jsonl', 'scheduler')
    rows += load_jsonl('observer_session.jsonl', 'observer')
    rows += load_jsonl('event_session.jsonl', 'event')
    rows += load_jsonl('frame_session.jsonl', 'frame')
    rows += load_jsonl('hmr_session.jsonl', 'hmr')
    rows.sort(key=lambda row: row['seq'])

    seqs = [row['seq'] for row in rows]
    if len(seqs) != len(set(seqs)):
        raise ValueError('global seq values are not unique')
    if seqs != sorted(seqs):
        raise ValueError('capture seq order is invalid')

    return (
        documents,
        definitions,
        prefix_snapshot,
        observer_snapshot,
        event_snapshot,
        frame_snapshot,
        observer_runtime,
        event_runtime,
        frame_runtime,
        hmr_catalog,
        rows,
    )
