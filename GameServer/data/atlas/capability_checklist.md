# Atlas capability checklist

Status meanings: **Verified** = exercised by an existing focused test or repeatable command; **Implemented** = present in code but not separately exercised here; **Partial** = useful path exists with known coverage limits; **Open** = capability is absent or evidence is insufficient.

| Area | Capability | Status | Evidence / limit |
|---|---|---|---|
| Extraction | Class and Blueprint code extraction | Implemented | `build` pipeline and SDK-backed extraction in `tools/atlas/build.py`; needs configured local inputs. |
| Extraction | Asset and table extraction | Implemented | `assets`, `areas`, and `weapons` dump paths; source package coverage depends on requested domains. |
| Extraction | Behemoth damage windows | Implemented | `damage-windows`; extraction output is source data, not server or client validation. |
| Discovery | Blueprint lookup (`show`) | Implemented | Class inheritance, properties, RPCs, functions, notes and references. |
| Discovery | Asset/table lookup (`asset`) | Implemented | Asset export and known reference index. |
| Discovery | Setter/caller/table usage lookup | Implemented | `who-sets`, `who-calls`, and `uses-table`; corpus completeness is bounded by extracted inputs. |
| Dependency tracing | Class dependencies | Partial | Blueprint parents/references are followed with bounded depth; not a complete engine dependency graph. |
| Dependency tracing | Asset dependencies | Partial | Package references are followed for configured extraction domains; dynamic/native references can remain unknown. |
| Dependency tracing | Table/data dependencies | Partial | Extracted rows and consumers are searchable; missing source tables remain explicit corpus gaps. |
| Gap reporting | Missing source rows/assets | Partial | Plans and extractors report known missing inputs; no global proof that all game content has been indexed. |
| Gap reporting | Phoenix implementation/readiness gaps | Partial | `plan` and simulation templates separate evidence classes; findings require current source and runtime refresh. |
| Composition | Hunt/island planning | Implemented | `plan` composes selected hunt and optional behemoth, weapon and item references. |
| Composition | Component compatibility | Partial | Authored pool and identity constraints are validated; general weapon/item/modifier compatibility is not modeled. |
| Export | Machine-readable plan | Implemented | Plan output is JSON-capable; portable simulation bundles have deterministic manifests. |
| Export | Phoenix runtime simulation data | Partial | Stage/apply supports server-owned assets; currently two real templates and both have readiness gaps. |
| Evidence | Source attribution and hashes | Implemented | Templates and staged bundles retain contract/acquisition provenance and content hashes when supplied. |
| Evidence | Verified vs unknown status | Partial | Evidence fields and unresolved requirements exist; stale incident records need periodic reconciliation. |
| Evidence | Shared implementation incident tracking | Partial | `evidence_gap_model.json` records findings; links and status updates remain manually maintained. |
| Simulation workflow | Template selection, staging, apply, rollback | Verified | `tests/test_atlas_simulations.py` covers alternate identity, PublicEvents map-only bypass, missing inputs, identity/hash rejection, deterministic staging, interrupted apply, repeat apply and rollback. |
| Simulation workflow | Acquisition orchestration | Implemented | `simulation acquire` runs the targeted Phoenix exporter, regenerates the selected template, then stages the result; composition-specific exporter inputs still must be supplied. |
| Simulation workflow | Runtime preflight parity | Partial | Phoenix preflight validates selected hunt inputs; broader parity across template variants still needs representative cases. |
| Coverage | Remaining authored island compositions | Open | Inventory currently has Island A and Island P contracts; no claim of full game island or species coverage. |
| Acceptance | Stock-client behavior | Open | No current client acceptance record for the regenerated Island A/P simulation bundles. |
