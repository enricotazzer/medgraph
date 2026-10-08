# Vendored viewer libraries

Inlined into each patient view by `medgraph/graph/view.py`, so a view opens offline from a
local file and loads nothing from the network. `view.py` refuses to render if a file's
SHA-256 differs from the value pinned there (and listed here). Do not edit these files;
pre-commit hooks skip this folder.

| file | library | version | license | downloaded from | SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `cytoscape.min.js` | Cytoscape.js | 3.34.3 | MIT (`LICENSE-cytoscape`) | cdnjs.cloudflare.com/ajax/libs/cytoscape/3.34.3/cytoscape.min.js | `5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91` |
| `uPlot.iife.min.js` | uPlot | 1.6.32 | MIT (`LICENSE-uplot`) | cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js | `19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f` |
| `uPlot.min.css` | uPlot | 1.6.32 | MIT (`LICENSE-uplot`) | cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css | `df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04` |

Verified on download (2026-10-07): `cytoscape.min.js` against the SHA-512 subresource-integrity
hash published by cdnjs, and the uPlot files against the SHA-256 hashes published by the
jsDelivr package API.
