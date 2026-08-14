# Agent prompts

These files hold the instructions for the two Claude scheduled tasks that run the pipeline
automatically. **You do not need to edit these files.** Open the app and go to
**Settings → Run it automatically**. Then click the Copy button. The app fills in your name
and your folder path, and it gives you the three steps.

The files are here so that git keeps them with the code that they call. If a script moves,
the prompt that calls it changes in the same commit.

| File | Task | When |
|---|---|---|
| `daily-discovery.md` | Finds new jobs and tailors the local ones | Weekday mornings |
| `tailor-queue.md` | Tailors the jobs that you pressed **Tailor** on | Each hour, in business hours |

## Placeholders

The function `render_agent_prompt` in `src/web/dashboard_server.py` replaces these tokens
before the app shows you the text. Every other word is literal prompt text.

| Token | Filled with |
|---|---|
| `{{PROJECT_DIR}}` | The project folder, as an absolute path |
| `{{OWNER}}` | `identity.full_name` from `config/profile.json`, or "the user" |
| `{{RESUME_PREFIX}}` | `identity.resume_prefix`, for example `Jane_Doe_Resume_` |
| `{{LOCAL_AREA}}` | `search.location` from the profile, or "your local area" |

## How to edit

Keep the two prompts in agreement. They use almost the same text for the resume rules, the
`item.action` branch, and the contract for a tracker update. If you change how the app
makes a resume, change **both** files. If you cannot change both, change neither.
