from velov.promote import MIN_GAIN, PEAK_TOLERANCE, decide

CHAMPION = {"mae_model": 1.34, "mae_baseline_persistence": 1.88, "mae_model_peak_hours": 1.48}


def challenger(**overrides) -> dict:
    return {**CHAMPION, **overrides}


def test_first_model_promoted_if_beats_baseline():
    assert decide(challenger(), None).promote


def test_refused_if_worse_than_baseline():
    assert not decide(challenger(mae_model=1.90), None).promote


def test_promoted_if_clear_gain():
    assert decide(challenger(mae_model=1.34 - MIN_GAIN), CHAMPION).promote


def test_refused_if_gain_within_seed_noise():
    # 1,339 vs 1,345 : l'écart observé entre deux seeds ne justifie pas un changement de champion
    assert not decide(challenger(mae_model=1.335), CHAMPION).promote


def test_refused_if_peak_hours_degrade():
    decision = decide(challenger(mae_model=1.30, mae_model_peak_hours=1.48 + PEAK_TOLERANCE + 0.01), CHAMPION)
    assert not decision.promote
    assert any(r.startswith("ECHEC pointe") for r in decision.reasons)
