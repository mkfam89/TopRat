# src/resume — turn a posting into a tailored resume

Two paths make the same output. The AI path is always optional.

- **Template path (no AI):** `tailor_local.py` picks the closest base resume and copies it.
- **AI path (optional):** `llm_tailor.py` rewrites the bullets with Claude, if a Claude CLI
  is configured. If the CLI is absent, the app uses the template path.

In both paths, `render_resume.py` makes the `.docx` and `.pdf` pair. Then `lint_resume.py`
approves or rejects the result.

| File | What it does |
|------|--------------|
| `tailor_local.py` | Fast, deterministic template tailoring. It is the default, and it uses no tokens. |
| `llm_tailor.py` | The optional Claude layer. It rewrites the bullets for one posting. |
| `render_resume.py` | Turns the content into a `.docx` file. Then LibreOffice makes the `.pdf` file. |
| `lint_resume.py` | Applies the format rules in `CLAUDE.md`: minimum 10pt font, minimum two bullets for each role, banned headers, and the PDF copy. The app does not deliver a resume that fails the lint. |
| `validate_bullets.py` | Makes sure that `resume_template/bullets.json` agrees with its schema. |

The content comes only from `resume_template/` in the data folder. That folder holds the
base resumes and the STAR stories. **No script here invents experience.** If a posting
names a tool with no evidence, the app flags the job for a person. It does not guess.
