import json
from pathlib import Path

ARTIFACT = Path('/app/output/reaction_ledger.json')
EXPECTED = Path('/tests/expected.json')

CHECKPOINT_KEYS = {'checkpoint_id', 'seq', 'instances', 'reactions'}
INSTANCE_KEYS = {'uid', 'local_name', 'owner_doc', 'connected', 'custom_state', 'definition_id'}
REACTION_KEYS = {
    'dispatch_index', 'dispatch_seq', 'trigger_seq', 'uid', 'definition_id', 'callback',
    'name', 'old_value', 'new_value', 'old_doc', 'new_doc'
}


def load_artifact():
    assert ARTIFACT.is_file(), f'missing artifact: {ARTIFACT}'
    try:
        return json.loads(ARTIFACT.read_text())
    except Exception as exc:
        raise AssertionError(f'artifact is not valid JSON: {exc}') from exc


def test_schema_is_exact():
    data = load_artifact()
    assert isinstance(data, dict)
    assert set(data) == {'version', 'checkpoints'}
    assert data['version'] == 1 and type(data['version']) is int
    assert isinstance(data['checkpoints'], list)

    checkpoint_ids = []
    last_seq = -1
    last_dispatch = 0
    for cp in data['checkpoints']:
        assert isinstance(cp, dict) and set(cp) == CHECKPOINT_KEYS
        assert isinstance(cp['checkpoint_id'], str)
        assert type(cp['seq']) is int and cp['seq'] > last_seq
        last_seq = cp['seq']
        checkpoint_ids.append(cp['checkpoint_id'])
        assert isinstance(cp['instances'], list)
        assert isinstance(cp['reactions'], list)

        seen_uids = set()
        for inst in cp['instances']:
            assert isinstance(inst, dict) and set(inst) == INSTANCE_KEYS
            assert isinstance(inst['uid'], str) and inst['uid'] not in seen_uids
            seen_uids.add(inst['uid'])
            assert isinstance(inst['local_name'], str)
            assert isinstance(inst['owner_doc'], str)
            assert type(inst['connected']) is bool
            assert inst['custom_state'] in {'uncustomized', 'custom'}
            assert inst['definition_id'] is None or isinstance(inst['definition_id'], str)
            if inst['custom_state'] == 'uncustomized':
                assert inst['definition_id'] is None
            else:
                assert inst['definition_id'] is not None

        for reaction in cp['reactions']:
            assert isinstance(reaction, dict) and set(reaction) == REACTION_KEYS
            assert type(reaction['dispatch_index']) is int
            assert reaction['dispatch_index'] == last_dispatch + 1
            last_dispatch = reaction['dispatch_index']
            assert type(reaction['dispatch_seq']) is int
            assert type(reaction['trigger_seq']) is int
            assert isinstance(reaction['uid'], str)
            assert isinstance(reaction['definition_id'], str)
            assert reaction['callback'] in {'constructor', 'attribute_changed', 'connected', 'disconnected', 'adopted'}
            for key in ('name', 'old_value', 'new_value', 'old_doc', 'new_doc'):
                assert reaction[key] is None or isinstance(reaction[key], str)

    assert len(checkpoint_ids) == len(set(checkpoint_ids))


def test_checkpoint_coverage_and_order():
    data = load_artifact()
    expected = json.loads(EXPECTED.read_text())
    assert [(x['checkpoint_id'], x['seq']) for x in data['checkpoints']] == [
        (x['checkpoint_id'], x['seq']) for x in expected['checkpoints']
    ]


def test_instance_history_matches():
    data = load_artifact()
    expected = json.loads(EXPECTED.read_text())
    actual_rows = [(cp['checkpoint_id'], cp['instances']) for cp in data['checkpoints']]
    expected_rows = [(cp['checkpoint_id'], cp['instances']) for cp in expected['checkpoints']]
    assert actual_rows == expected_rows


def test_reaction_ledger_matches():
    data = load_artifact()
    expected = json.loads(EXPECTED.read_text())
    actual_rows = [(cp['checkpoint_id'], cp['reactions']) for cp in data['checkpoints']]
    expected_rows = [(cp['checkpoint_id'], cp['reactions']) for cp in expected['checkpoints']]
    assert actual_rows == expected_rows
