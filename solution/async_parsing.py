import json
from pathlib import Path

from parsing import load_capture

DATA = Path('/app/data')


def load_json(name):
    return json.loads((DATA / name).read_text())


def load_jsonl(name):
    rows = []
    for line in (DATA / name).read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_capture_with_async_trace():
    base = load_capture()
    *prefix, rows = base
    async_rows = load_jsonl('async_trace.jsonl')
    for row in async_rows:
        row['_source'] = 'async'
    rows = sorted(rows + async_rows, key=lambda row: row['seq'])
    return (
        *prefix,
        load_json('async_bundle_index.map.json'),
        load_json('async_chunk_maps.json'),
        load_json('async_runtime_catalog.json'),
        rows,
    )
