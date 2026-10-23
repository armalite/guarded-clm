# Changelog

## 0.3.0 (2026-10-24)

The first release with the cache-friendly request layout as the default. Earlier versions, 0.1.0.dev0 to 0.3.0.dev0, were development versions; [SPEC.md](SPEC.md) lists them in its version history. This release has the same behaviour as 0.3.0.dev0.

### The default request layout is now cache-friendly

`clm_context()` now sends each rebuilt request as separate content blocks (`request_layout="segmented_task_last"`):
1. the working-context entries, one block each;
2. a five-minute Pydantic AI `CachePoint` after the last entry (`cache_point="5m"`);
3. the task and the runtime status, after the marker.

**Why:** the parts that change on every call, the status and at a new turn the current request, now come after the marker. A provider with prompt caching can reuse the unchanged working context on ordinary calls and when a new user turn starts. In the previous default, the whole request was one block ending with the changing status, so only the tools and the system prompt could be reused.

**Unchanged:** edits and their validation, protection of earlier turns, the output guard, the size estimator's rules, recovery, the step and call limits, the instructions, trace records and the compatibility strings. The defaults select an already tested layout; they add no new mechanism.

**What does change for the model:** it sees the same sections in a different order (the task after its working context) and as several blocks. Its behaviour may differ. The size estimates count the list framing, so they are slightly higher than in the single layout.

### The original layout and the cache marker remain selectable

| You want | Pass |
| --- | --- |
| the previous behaviour exactly (one text block, as the stored transcripts test it) | `clm_context(request_layout="single")` |
| the new layout without the library's cache marker | `clm_context(cache_point=None)` |
| the task-first segmented layout, with a marker | `clm_context(request_layout="segmented", cache_point="5m")` |
| a one-hour marker | `clm_context(cache_point="1h")` |

**How `cache_point` resolves:**
- omitted, it is `"5m"` for `"segmented_task_last"` and no marker for `"single"` and `"segmented"`;
- an explicit value, `None` included, is used as given;
- a marker with `"single"`, an unknown layout or an unknown TTL raises `ValueError`.

### Recommended host settings (Anthropic, through Pydantic AI)

The library never sets or changes model settings. For an Anthropic model, set:

```python
model_settings = {
    "anthropic_cache_tool_definitions": "5m",  # the tool definitions
    "anthropic_cache_instructions": "5m",      # the system prompt and instructions
}
```

- **Leave out** `anthropic_cache` and `anthropic_cache_messages`. Both put a breakpoint on the last block, which in this layout is the changing status tail, so the entry they write is never read.
- **The breakpoints:** with the library's marker, that makes three (the provider allows four). The tool and instruction TTLs may be `"1h"`, but not shorter than the marker's.
- **Caching is not guaranteed.** It depends on the provider and the model:
  - a prefix shorter than the model's minimum cacheable length is not cached;
  - entries expire after their lifetime;
  - a breakpoint looks back only a limited number of blocks;
  - an accepted edit invalidates the cache from the first entry it changes.

  Models without prompt caching ignore the marker.

### Migration requirements

- **Pass back the history unchanged:** `result.all_messages()`, or its JSON form, on every turn, with `conversation_id`. In the default layout the runtime recognises its own rebuilt request by `ModelRequest.metadata`. A history that keeps only text is not supported there.
- **Keep one layout per conversation.**
- **Internal notes:** add them with `add_context_note(request, text)`, from a capability placed before `clm_context()`. In the default layout a user prompt part that merely starts with `[context manager]` is user text and starts a turn. The single and segmented layouts still read that prefix as a note.
- **User turns are recognised by provenance, not text,** in the default layout, so a turn that opens with `<task>`, `<working_context>` or `[context manager]` (a pasted example, for instance) is a turn. The single and segmented layouts keep their original text rules ([SPEC.md](SPEC.md) section 13.19).

### Validation scope

**Offline, in this repository:**
- the 32 stored transcripts pass unchanged with `request_layout="single"` selected explicitly;
- with the options omitted, all 32 scenarios run exactly as with `"segmented_task_last"` and `"5m"` stated;
- request bodies from Pydantic AI's Anthropic adapter (pydantic-ai-slim 2.27.1, anthropic 0.125.0, over an in-memory transport) show where the markers fall and which earlier prefixes are eligible, within and across turns and after edits;
- message origin is covered for pasted prefixes, runtime notes, JSON history and limit continuations.

These checks show structure and eligibility, not cache hits.

**Live, in a downstream evaluation harness** (its protocols and results are kept with that harness), on one model (claude-sonnet-5) and one synthetic multi-turn investigation workload:
- **The sessions:** a two-session development check, then six sessions on instances that harness had used before, compared with its earlier single-layout results.
- **Reuse:** in every session the provider read the cached working context on every ordinary call and every new-turn start, at the boundaries the documented caching rules predict.
- **Task results** in those sessions were not worse than the single layout's.
- **Cost:** it was lower than with the single layout in those sessions, even though some sessions did more work.

**Not validated:** other providers, models, workloads or cloud platforms, one-hour TTLs, and sessions long enough to stress the lookback. A saving in one setting does not imply savings elsewhere, and the model's behaviour is not claimed to be unchanged.

### Also new since the previous development versions

- **`request_layout`:** `"single"`, `"segmented"` and `"segmented_task_last"` (decisions P8, P9).
- **`cache_point`:** the context cache marker.
- **Message origin by runtime metadata** in the default layout (decisions P10, P12).
- **`add_context_note()`:** a public helper for marked runtime notes, used by the step limit's finalize notice.

**Requirements:** Python 3.11 to 3.13, `pydantic-ai-slim>=2.27.1,<2.28`.
