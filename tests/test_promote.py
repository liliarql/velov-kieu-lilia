"""Tests unitaires de la règle de promotion en trois conditions (S4)."""

from velov.promote import decide


def test_challenger_must_beat_persistence():
    """Condition 1 : rejeté si mae_model >= mae_baseline_persistence."""
    challenger = {"mae_model": 2.0, "mae_baseline_persistence": 1.5, "mae_model_peak_hours": 1.8}
    ok, reason = decide(challenger, champion=None)
    assert not ok
    assert "persistance" in reason


def test_first_champion_if_beats_baseline():
    """Pas de champion existant : le premier modèle qui bat la baseline est promu."""
    challenger = {"mae_model": 1.2, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.3}
    ok, reason = decide(challenger, champion=None)
    assert ok
    assert "aucun champion" in reason


def test_must_improve_champion_by_min_gain():
    """Condition 2 : rejeté si le gain sur le champion est insuffisant."""
    champion = {"mae_model": 1.30, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.4}
    # Gain de 0.005 < MIN_GAIN (0.01) → rejeté
    challenger = {"mae_model": 1.295, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.3}
    ok, reason = decide(challenger, champion)
    assert not ok
    assert "gain insuffisant" in reason


def test_must_not_degrade_peak_hours():
    """Condition 3 : rejeté si les heures de pointe se dégradent au-delà de la tolérance."""
    champion = {"mae_model": 1.30, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.2}
    # MAE globale OK (1.28 <= 1.29), mais peak dégradé : 1.30 > 1.25 (1.2 + 0.05)
    challenger = {"mae_model": 1.28, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.30}
    ok, reason = decide(challenger, champion)
    assert not ok
    assert "pointe" in reason


def test_promotion_when_all_conditions_met():
    """Les trois conditions passent → promotion accordée."""
    champion = {"mae_model": 1.30, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.4}
    challenger = {"mae_model": 1.25, "mae_baseline_persistence": 1.8, "mae_model_peak_hours": 1.35}
    ok, reason = decide(challenger, champion)
    assert ok
    assert "peak OK" in reason
