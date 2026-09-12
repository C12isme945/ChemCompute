from chemcompute.runtime_lock import acquire_runtime_lock


def test_runtime_lock_exclusion_and_release(tmp_path):
    release = acquire_runtime_lock(tmp_path)
    assert release is not None
    try:
        assert acquire_runtime_lock(tmp_path) is None
        other = tmp_path/'other'
        other.mkdir()
        release_other = acquire_runtime_lock(other)
        assert release_other is not None
        release_other()
    finally:
        release()
    acquired_again = acquire_runtime_lock(tmp_path)
    assert acquired_again is not None
    acquired_again()
