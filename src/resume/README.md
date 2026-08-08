# src/resume — turn a posting into a tailored resume

Two paths to the same output, and the AI one is always optional:

- **Template path (no AI):** `tailor_local.py` picks the closest base resume and copies it.
- **AI path (optional):** `llm_tailor.py` rewrites the bullets through Claude, when a
  Claude CLI is configured. If it is missing, the template path takes over silently.

Either way, `render_resume.py` produces the `.docx` + `.pdf` pair and `lint_resume.py`
gets the last word.

| File | What it does |
|------|--------------|
| `tailor_local.py` | Instant, deterministic template tailoring. The zero-token default. |
| `llm_tailor.py` | Optional Claude layer that rewrites bullets for a specific posting. |
| `render_resume.py` | Content → `.docx` renderer, then `.pdf` via LibreOffice. |
| `lint_resume.py` | Enforces the formatting rules in `CLAUDE.md` (10pt floor, two-bullet minimum, banned headers, PDF twin...). A resume that fails the lint is not shipped. |
| `validate_bullets.py` | Checks `resume_template/bullets.json` against its schema. |

Source content comes only from the data folder's `resume_template/` — base resumes and
the STAR stories. **Nothing here invents experience.** If a posting names a tool with no
evidence behind it, the job is flagged for a human, not guessed at.
