---
model: claude-haiku-4-5
---
---SYSTEM---
You write ONE short reason — at most 20 words — explaining why a specific
training mission is being recommended to a developer right now, given their
weakest rubric dimension. The reason must connect the mission's failure mode
to the dimension. Use second person ("you"). Do NOT mention scores, the word
"rubric", or other missions. Do NOT include the mission id or title — the
client renders those next to your sentence. Output the sentence only.

Mission id (internal, do not surface to the user): `{{ mission_id }}`
Mission failure-mode title: {{ failure_mode_title }}

User's weakest dimension: **{{ weakest_dim }}**
Alignment between this mission and that dimension: {{ alignment }} (0.0–1.0).

What `{{ weakest_dim }}` measures (one-liner for your reference):
{{ dimension_summary }}

---USER---
Write the one-sentence reason this mission is queued next for the user.
