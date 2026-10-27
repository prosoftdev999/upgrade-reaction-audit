import json
from pathlib import Path

from async_parsing import load_capture_with_async_trace
from async_trace_model import AsyncTraceLifecycleModel

OUTPUT = Path('/app/output/reaction_ledger.json')


def main():
    (
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
        bundle_map,
        chunk_maps,
        async_catalog,
        rows,
    ) = load_capture_with_async_trace()
    model = AsyncTraceLifecycleModel(
        documents, definitions, observer_runtime, event_runtime, frame_runtime,
        hmr_catalog, bundle_map, chunk_maps, async_catalog,
    )

    checked_base = checked_observer = checked_event = checked_frame = False
    for row in rows:
        model.apply(row)
        if row['seq'] == 273:
            if model.final_dom_snapshot() != prefix_snapshot:
                raise ValueError('base capture no longer matches final_dom_snapshot.json')
            checked_base = True
        if row['seq'] == 377:
            if model.final_dom_snapshot() != observer_snapshot:
                raise ValueError('observer continuation no longer matches final_extension_snapshot.json')
            checked_observer = True
        if row['seq'] == 417:
            if model.final_dom_snapshot() != event_snapshot:
                raise ValueError('event continuation no longer matches final_event_snapshot.json')
            checked_event = True
        if row['seq'] == 502:
            if model.frame_state_snapshot() != frame_snapshot['frame_state']:
                raise ValueError('render continuation no longer matches final_frame_snapshot.json')
            if model.final_dom_snapshot() != frame_snapshot['dom']:
                raise ValueError('render continuation DOM no longer matches final_frame_snapshot.json')
            checked_frame = True

    if not all((checked_base, checked_observer, checked_event, checked_frame)):
        raise ValueError('one or more preserved capture boundaries were not found')

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(model.result(), indent=2) + '\n')


if __name__ == '__main__':
    main()
