import os
import stat

import pytest

from market_discovery_internal.state_persistence import save_paper_state


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits required")
def test_save_paper_state_sets_world_readable_mode(tmp_path):
    state_path = tmp_path / "paper_positions.json"
    save_paper_state({"positions": [], "history": [], "cycle_journal": [], "updated_at": None, "meta": {}}, str(state_path))

    mode = stat.S_IMODE(os.stat(state_path).st_mode)
    assert mode & stat.S_IROTH
