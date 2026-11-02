Return a JSON object with company, role and nonempty claims. Each claim contains
type, text, source_ids and unchanged source-owned employer/title/start/end/metrics
when relevant. Cite only provided confirmed typed sources. Never invent a metric,
skill, employer, degree, title or date, including inside free text. A generated
resume is not a new fact source. Preserve precision and uncertainty.

Set primary_source_id to one cited entry. Supporting sources must have that same
entry_id; never merge unrelated employers or projects. Preserve ownership verbs,
metric qualifiers and the lexical order of the source when shortening text.

Follow required/excluded entries and bullets, rewrite/reselection permissions,
scope, section order, optional GPA, and 1/2-page settings from config. Do not add
missing skills merely to match JD keywords. HAVE requires direct source evidence;
ADJACENT requires explicit supplied adjacency; otherwise MISSING. If missing_facts
is ask, return a questions array for the user instead of fabricating answers; if
omit, omit unsupported content. Never open application forms or request browser
actions. A repair response must address only supplied validation errors while
preserving all confirmed facts. There is at most one repair, no scoring loop.
