# Agent prompts

These are the instructions for the two Claude scheduled tasks that run the pipeline
automatically. **You do not need to edit these files.** Open the app, go to
**Settings → Run it automatically**, and use the Copy button — the app fills in your own
name and folder path and gives you the three steps.

They live here so they are versioned with the code they call. If a script moves, the
prompt that calls it changes in the same commit.

| File | Task | When |
|---|---|---|
| `daily-discovery.md` | Finds new jobs, tailors the local ones | Weekday mornings |
| `tailor-queue.md` | Tailors the jobs you pressed **Tailor** on | Hourly, business hours |

## Placeholders

`src/web/dashboard_server.py` (`render_agent_prompt`) substitutes these before showing you
the text. Anything not in this list is literal prompt text.

| Token | Filled with |
|---|---|
| `{{PROJECT_DIR}}` | The project folder, as an absolute path |
| `{{OWNER}}` | `identity.full_name` from `config/profile.json`, or "the user" |
| `{{RESUME_PREFIX}}` | `identity.resume_prefix`, e.g. `Jane_Doe_Resume_` |
| `{{LOCAL_AREA}}` | `search.location` from the profile, or "your local area" |

## Editing

Keep the two prompts consistent with each other — they share the resume rules, the
`item.action` branch and the tracker-update contract almost verbatim. A change to how a
resume is produced belongs in **both** files or in neither.
