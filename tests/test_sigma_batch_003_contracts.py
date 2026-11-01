"""Independent precedence and fail-closed contracts exercised by batch 003."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import UnsupportedSigmaRule, import_rule
from guardoath.sigma_rules.matcher import match_detection

ROOT = Path(__file__).resolve().parents[1]
RECORDS = json.loads((ROOT / 'guardoath/sigma_rules/upstream_batch_003.json').read_text(encoding='utf-8'))['rules']


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
@pytest.mark.parametrize('selection', [
    {'ScriptBlockText|contains': 'not present', 'Image|unsupported': 'x'},
    [{'ScriptBlockText': 'not present'}, {'ScriptBlockText|contains|all': []}],
    {'ScriptBlockText|contains': 'not present', 'Image|re': '['},
])
def test_every_new_rule_rejects_invalid_dead_selection(record, selection):
    altered = deepcopy(record)
    altered['detection']['unreferenced_dead_branch'] = selection
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(altered, altered['source_path'])


CONDITIONS = [
    ('a or b and c', lambda a, b, c: a or (b and c)),
    ('(a or b) and c', lambda a, b, c: (a or b) and c),
    ('a and not b or c', lambda a, b, c: (a and not b) or c),
    ('a and not (b or c)', lambda a, b, c: a and not (b or c)),
    ('not a and b or c', lambda a, b, c: ((not a) and b) or c),
    ('not (a and b) and c', lambda a, b, c: (not (a and b)) and c),
    ('1 of selection_* and not 1 of filter_*', lambda a, b, c: (a or b) and not c),
    ('all of selection_* or filter_c', lambda a, b, c: (a and b) or c),
]


@pytest.mark.parametrize('a,b,c', list(product([False, True], repeat=3)))
@pytest.mark.parametrize('condition,expected', CONDITIONS, ids=[c for c, _ in CONDITIONS])
def test_condition_precedence_truth_table(condition, expected, a, b, c):
    detection = {'a': {'A': 'yes'}, 'b': {'B': 'yes'}, 'c': {'C': 'yes'},
                 'selection_a': {'A': 'yes'}, 'selection_b': {'B': 'yes'},
                 'filter_c': {'C': 'yes'}, 'condition': condition}
    event = {key: 'yes' for key, present in [('A', a), ('B', b), ('C', c)] if present}
    assert match_detection(detection, event) is expected(a, b, c)


@pytest.mark.parametrize('condition', ['a or unknown', 'a or (b and)', 'a or 2 of selection_*'])
def test_true_left_branch_does_not_hide_invalid_condition(condition):
    with pytest.raises(UnsupportedSigmaRule):
        match_detection({'a': {'A': 'yes'}, 'b': {'B': 'yes'}, 'condition': condition}, {'A': 'yes'})
