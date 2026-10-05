"""Voice lock and the wake-word listener are off until asked for, and a lock
that cannot score an utterance lets it through rather than leaving him deaf."""
import pytest


@pytest.fixture
def hearing(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import hearing as h
    monkeypatch.setattr(h, "_enroll_left", 0)
    return h


def test_both_are_off_and_unavailable_until_set_up(hearing):
    s = hearing.status()
    assert not s["voice_lock"] and not s["wake_engine"] and not s["enrolled"]
    assert not s["voice_lock_available"] and not s["wake_available"]     # no models here
    assert hearing.is_user(b"not even audio") == (True, None)


def test_a_lock_that_cannot_score_lets_the_utterance_through(hearing, monkeypatch):
    hearing.save(voice_lock=True, embedding=[1.0, 0.0])
    monkeypatch.setattr(hearing, "_embedding", lambda wav: (_ for _ in ()).throw(RuntimeError("no model")))
    assert hearing.is_user(b"x") == (True, None)


def test_the_lock_answers_only_a_voice_close_to_the_learned_one(hearing, monkeypatch):
    import numpy as np
    hearing.save(voice_lock=True, embedding=[1.0, 0.0], threshold=0.5)
    monkeypatch.setattr(hearing, "_embedding", lambda wav: (np.array(wav), 3.0))
    assert hearing.is_user([0.9, 0.436])[0] is True
    assert hearing.is_user([0.2, 0.98])[0] is False
    assert "embedding" not in hearing.status()               # never handed to the page


def test_three_full_sentences_teach_it_and_switch_it_on(hearing, monkeypatch):
    import numpy as np
    monkeypatch.setattr(hearing, "_embedding", lambda wav: (np.array([1.0, 0.0]), wav))
    hearing.start_enrolling()
    assert "too short" in hearing.enroll_sample(0.5) and hearing.enrolling()
    for _ in range(hearing.ENROLL_SAMPLES):
        said = hearing.enroll_sample(3.0)
    assert "Voice lock is on" in said and not hearing.enrolling()
    assert hearing.status()["voice_lock"] and hearing.status()["enrolled"]
