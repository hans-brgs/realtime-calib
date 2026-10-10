"""The names of the files a session folder holds (QLT-17): one place, read everywhere.

Folder paths stay with the store (``session_dir``, ``extrinsic_dir``); these are the
files inside them. A rename here is a rename of the session format.
"""

from __future__ import annotations

# extrinsic/: the recorded sweep (videos and sidecars announced by the manifest) and
# the solve artefacts derived from it.
SWEEP_MANIFEST = "manifest.json"
RESULT_FILE = "result.json"
BA_INPUTS_FILE = "ba_inputs.json"
REFERENCE_FILE = "reference.json"  # the deposited reference calibration (ADR-0061)
# intrinsic/<camera>/: the review metrics next to the capture (ADR-0022).
INTRINSIC_METRICS_FILE = "metrics.json"
