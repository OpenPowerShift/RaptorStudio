"""Hard limits shared by the API and the MCP front end.

These are deliberately conservative: the GUI is a local application and a
person works with a handful of models, while an MCP client may be remote or
scripted and can otherwise ask for an unbounded amount of memory, disk,
CPU or response text with one call.  Keep the numbers here so every surface
enforces the same bound rather than each tool inventing its own.
"""
from __future__ import annotations

#: A single model file may not exceed this size (bytes on disk, or the
#: serialized .rbds text before a save).  Large enough for real fixtures
#: (the biggest checked-in model is a few hundred kB), small enough that
#: one request cannot exhaust the process.
MAX_MODEL_BYTES = 64 * 1024 * 1024

#: A model's display name (a label; the file name is generated).
MAX_MODEL_NAME = 100

#: Total models a workspace may hold.  `Workspace.max_models` is only the
#: in-memory cache bound; this is the on-disk/inode bound.
MAX_TOTAL_MODELS = 20_000

#: A template (`create_model(template=...)`) may not build more blocks.
MAX_TEMPLATE_BLOCKS = 1_000

#: `build_model` request limits.  The spec is JSON from an MCP client, so
#: check both the encoded size and the element count before building.
MAX_SPEC_BYTES = 8 * 1024 * 1024
MAX_SPEC_ELEMENTS = 20_000

#: Items in one bulk edit call (`add_elements`, `update_elements`,
#: `connect_many`, `delete_elements`). Each item costs work proportional to the
#: model's size, so a very large batch is quadratic; `build_model` is the
#: path for building a whole model at once.
MAX_BATCH_ITEMS = 1_000

#: Annotations: count and the size of one text field.
MAX_ANNOTATIONS = 1_000
MAX_ANNOTATION_TEXT = 10_000

#: Mission phases and the assignments matrix (phases x elements).
MAX_PHASES = 1_000
MAX_ASSIGNMENT_CELLS = 1_000_000

#: Named rate definitions.
MAX_RATES = 1_000
MAX_RATE_NAME = 120
MAX_RATE_DESCRIPTION = 2_000

#: Response-size guards for tools/resources that can otherwise return an
#: arbitrarily large page.
MAX_TIMESERIES_POINTS = 5_000
MAX_RENDER_PX = 4_000
MAX_SEARCH_RESULTS = 50
MAX_DESCRIBE_ELEMENTS = 5_000
MAX_DIFF_CHANGES = 5_000
MAX_LIST_ANNOTATIONS = 1_000
MAX_LIST_RUNS = 5_000
MAX_RESOURCE_CHARS = 200_000

#: HTTP body limit for one MCP request, and how many long jobs may run at
#: once.  Body is buffered by the transport before it reaches the SDK.
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT_JOBS = 2

#: Background (`start_run`) jobs that may be queued or running at once. They
#: share the `MAX_CONCURRENT_JOBS` slots with simulate/sweep/compare, so the
#: rest wait their turn; a client that asks for more is refused, not queued.
MAX_BACKGROUND_JOBS = 4

#: Simulation argument bounds.
MAX_WORKERS = 64

#: MCP runs only: a model with this many blocks, events and nodes, run this
#: many times, uses several cores unless the caller names `workers`. The
#: desktop app and the command line keep their own defaults.
AUTO_WORKERS_MIN_ELEMENTS = 500
AUTO_WORKERS_MIN_RUNS = 20
AUTO_WORKERS_MAX = 8
MAX_TIMEOUT_S = 86_400.0
