"""State persistence helpers for paper-trading mode."""

import json
import os
import tempfile


_EMPTY_STATE = {
    "positions": [],
    "history": [],
    "cycle_journal": [],
    "updated_at": None,
    "meta": {},
}


def load_paper_state(path):
    """Load paper-trading state from disk or return an empty state."""
    if not os.path.exists(path):
        return dict(_EMPTY_STATE)

    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
    except (OSError, json.JSONDecodeError):
        return dict(_EMPTY_STATE)

    positions = data.get("positions", []) if isinstance(data, dict) else []
    history = data.get("history", []) if isinstance(data, dict) else []
    cycle_journal = data.get("cycle_journal", []) if isinstance(data, dict) else []
    updated_at = data.get("updated_at") if isinstance(data, dict) else None
    meta = data.get("meta") if isinstance(data, dict) else None

    if not isinstance(positions, list):
        positions = []
    if not isinstance(history, list):
        history = []
    if not isinstance(cycle_journal, list):
        cycle_journal = []
    if not isinstance(meta, dict):
        meta = {}

    return {
        "positions": positions,
        "history": history,
        "cycle_journal": cycle_journal,
        "updated_at": updated_at,
        "meta": meta,
    }


def save_paper_state(state, path):
    """Persist paper-trading state to disk."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".paper_state_",
            suffix=".tmp",
            dir=directory,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as file_handle:
            json.dump(state, file_handle, indent=2, sort_keys=True)
            file_handle.flush()
            os.fsync(file_handle.fileno())

        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
