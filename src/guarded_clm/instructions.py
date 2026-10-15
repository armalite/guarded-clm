"""The instructions ClmContext adds to every request, word for word as tested.

The text derives from clm-lib (`src/clm_lib/prompts.py`, `PROTOCOL` and `CLM_INSTRUCTIONS`,
github.com/armalite/clm-lib, commit 024fc76a8e79468e3d160112844c7aa9bd2e94ac), adapted for structured operations,
parallel calls and protected entries. It is kept exactly as tested, including wording that no longer applies
literally (for example "apart from files you write"). Changing it changes the design; see SPEC.md.

    clm-lib is licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in
    compliance with the License. You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0.
    Unless required by applicable law or agreed to in writing, software distributed under the License is
    distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See
    the License for the specific language governing permissions and limitations.
"""

from guarded_clm.context import ContextLimits

INSTRUCTIONS = """\
Each request contains three sections:
- <task>: the fixed task. It is never edited and always present.
- <runtime_status>: limits and budget occupancy computed by the runtime.
- <working_context>: your transcript so far, one JSON object per line with id, role, body.
  This is the only memory you have between steps (apart from files you write).

You may call several tools in one step; they all run.
"edit_context" applies a list of operations to your working context, in order:
{{"op": "delete", "ids": ["..."]}}, {{"op": "replace", "id": "...", "body": "..."}} and
{{"op": "add_note", "id": "...", "body": "..."}}. A short result comes back as an observation entry.

CONTEXT EDITING (you control your working context):
- The entries in <working_context> are your working context. Whatever it contains after your
  step becomes <working_context> in the next request.
  ids: unique, 1-64 chars of [A-Za-z0-9_.:~-]; roles: assistant, observation, note, summary, receipt.
  Limits: {max_entries} entries, {max_body} chars per body.
- With the operations you may delete entries, rewrite or shorten bodies, and add notes.
- Entries marked protected (earlier requests and your earlier answers) can't be edited or deleted; tidy everything else as needed.
- Ordering rule: the context you see at the start of a step holds the context up to the previous
  step. After your step, the runtime validates the edit, then appends this step's action and
  observation (and a short receipt if you edited). Edits therefore cannot remove the current
  step's output; you can remove it in a later step.
- Invalid edits (unknown ids, duplicate ids, other roles, too large) are rejected and the previous
  context is kept. The task and these instructions are outside the context.
- Editing is optional. When <runtime_status> reports pressure, manage the context before it runs
  out. Keep exact values, file:line references and open questions you still need.
"""


def instructions(limits: ContextLimits) -> str:
    return INSTRUCTIONS.format(max_entries=limits.max_entries, max_body=limits.max_body_chars)
