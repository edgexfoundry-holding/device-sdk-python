# Handoff Document — device-sdk-python M1 (Gap G1: Core Metadata Write-Back)

## Context
Continuing the Python port of `edgexfoundry/device-sdk-go` v4.0.0. The codebase was repaired after a docstring sweep broke syntax across the tree. A grilling session (Q1-Q16) settled the v4.0.0 contract: **10 milestones, TDD, one commit per milestone, pythonic (no Go mirroring)**. We are at **M1: G1 metadata runtime write-back**.

**Key decisions from grilling:**
- G1: cache-first + rollback, strict `EdgexError` propagation, in-process `driver.validate_device`, HTTP via bounded `ThreadPoolExecutor`
- G12: progress topic `device/<action>` (action ∈ discovery|profilescan|custom), event topic `edgex/events/device/<svc>/...`, ADR-013 marked historical
- G5: zero new deps (stdlib logging/metrics/secrets)
- G6+G7: DeviceDown retry loop + file-mtime config polling (Consul as doc extension)
- Thread discipline: daemon threads + shared `_shutdown_event` + joinable registry, reverse-order join in `_shutdown`
- Build order 1→10, all G1-G9 in v4.0.0

## Current State (as of this handoff)

### Completed
- Syntax repair: all `*.py` AST parse OK, `"""` balanced, 20 modules import, 22 tests pass (before M1 work)
- Three explore agents: architecture map, gap list G1-G13, concurrency/shutdown audit
- Grilling rounds Q1-Q16 settled → v4.0.0 contract
- Commits: `de49a15` (refactor: de-Go-ify internals) + docs commit (`docs/edgex-reference/`)
- EdgeX v4 wire contracts verified against `app-functions-sdk-python` + `go-mod-core-contracts` v4.0.0
- M1 research: read `device_service.py` (stubs 1657-1775, call sites 163-521, `__init__` 105, `_shutdown` 1104, `_metadata_client` 779), `metadata/client.py`, `metadata/dto.py`, `cache/devices.py`, `validation.py`, `bootstrap.py`

### M1 Implementation Status

**Tests written** (`tests/test_metadata_writeback.py` — 52 tests total, 32 pass / 9 failures / 11 errors):
- `TestMetadataClientWriteEndpoints` (13 wire tests): mock `requests.post/patch/put/delete`; assert route, query params, envelope, camelCase mapping, error modes
- `TestMetadataWriteBack` (25 service tests): inject `_FakeMetadataClient`, bootstrap with `_ValidatingDriver`; assert cache-first + rollback + `EdgexError` propagation + `driver.validate_device` calls

**Code edits applied:**

| File | Changes |
|------|---------|
| `internal/metadata/dto.py` | Added `update_device_request(name, updates)` with snake→camel field map; `update_provision_watcher_request(watcher)` |
| `internal/metadata/client.py` | Added `_delete` helper; extended `_patch`/`_put` to accept `params`; reworked `patch_device` → PATCH `/api/v3/device` (collection) with `bypassValidation`; added single-entity methods: `add_device`, `patch_device`, `delete_device`, `add_device_profile`, `update_device_profile`, `delete_device_profile`, `add_provision_watcher`, `update_provision_watcher`, `delete_provision_watcher` |
| `service/device_service.py` | Added `ThreadPoolExecutor` import + `_metadata_executor` field + `_run_metadata` + `_validate_device`; rewrote 9 stubs (`_add_device_to_metadata`, `_patch_device_in_metadata`, `_delete_device_from_metadata`, `_add_profile_to_metadata`, `_update_profile_in_metadata`, `_delete_profile_from_metadata`, `_add_provision_watcher_to_metadata`, `_update_provision_watcher_in_metadata`, `_delete_provision_watcher_from_metadata`) with cache-first + executor + rollback + `KIND_SERVER_ERROR` propagation |

### Remaining Work (next agent)

1. **Fix test mock unpacking bug** — `call_args` returns `(args, kwargs)`; tests do `url, kwargs = mpost.call_args` making `url` a 1-tuple. Change to `args, kwargs = mpost.call_args; url = args[0]` in all 13 wire tests.

2. **Remove duplicate cache ops in public methods** — `add_device_profile` still calls `Profiles().add(profile)` after helper (now double-add → `DUPLICATE_NAME`); `remove_device_profile_by_name` calls helper (which removes cache) then `Profiles().remove_by_name(name)` (double-remove → `ENTITY_DOES_NOT_EXIST`). Delete those lines.

3. **Update `_shutdown`** — add executor shutdown:
   ```python
   if self._metadata_executor is not None:
       self._metadata_executor.shutdown(wait=False, cancel_futures=True)
       self._metadata_executor = None
   ```

4. **Real client returns metadata-assigned id** — `add_device`/`add_device_profile`/`add_provision_watcher` currently return `None`; should extract `resp[0].get("id")` from response and return it (service already handles `if new_id:`).

5. **Run full test suite + lint** — `python -m unittest discover -s tests` + whatever lint command (check `pyproject.toml` / `Makefile`). Fix any remaining failures.

6. **Commit** — single commit for M1 with message like `feat: G1 Core Metadata write-back with cache-first rollback (M1)`.

## Relevant Files
- `src/device_sdk_py/service/device_service.py` — main edit target (stubs 1657-1775, helpers 779-800, `_shutdown` 1104)
- `src/device_sdk_py/internal/metadata/client.py` — 9 write methods + `_delete`
- `src/device_sdk_py/internal/metadata/dto.py` — 2 new serializers
- `src/device_sdk_py/internal/cache/{devices,profiles,provisionwatchers}.py` — rollback primitives
- `tests/test_metadata_writeback.py` — TDD tests (wire + service)
- `tests/test_bootstrap.py` — conventions (`_make_service`, `_Driver`, `_ValidatingDriver`)
- `docs/edgex-reference/` — EdgeX v4.0.2 progress topic contract, ADR-013
- `~/.config/opencode/skills/` — 13 global skills (see below)

## Suggested Skills for Next Session
| Skill | Why |
|-------|-----|
| **tdd** | Continue red-green-refactor on the remaining test failures |
| **diagnosing-bugs** | If any subtle rollback/cache race or executor shutdown issues appear |
| **code-review** | Review M1 diff against v4.0.0 contract before commit |
| **to-spec** | If you want to formally record the M1 design decisions as an ADR |

## Commands to Resume
```bash
cd /home/jieke/.qwenpaw/workspaces/S5o9Ug/device-sdk-python
python -m unittest discover -s tests -p 'test_*.py' 2>&1 | head -50
# then fix in order: test mock unpacking → duplicate cache ops → _shutdown executor → client id return → full suite
```

## References
- Git: `de49a15` (refactor), docs commit (untracked `docs/edgex-reference/` now tracked)
- Grilling Q1-Q16 log (conversation history) — the source of truth for v4.0.0 decisions
- EdgeX v4 contracts: `docs/edgex-reference/` + `app-functions-sdk-python` device client