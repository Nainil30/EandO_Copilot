from evals.run_evals import execution_match


def test_execution_match_is_order_insensitive():
    pred = {"rows": [["b", 2], ["a", 1.0]]}
    gold = {"rows": [["a", 1], ["b", 2]]}
    assert execution_match(pred, gold) == (True, True)


def test_soft_match_tolerates_extra_columns():
    pred = {"rows": [["a", "x", 1], ["b", "y", 2]]}
    gold = {"rows": [["a", 1], ["b", 2]]}
    strict, soft = execution_match(pred, gold)
    assert not strict and soft


def test_mismatch_detected():
    assert execution_match({"rows": [["a", 1]]}, {"rows": [["a", 2]]}) == (False, False)
