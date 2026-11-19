# Choose the template section

The fifth and last O1 stage. An open-weight LLM (Qwen3.8-27B, FP8) running on one of
Blythe's L40 GPUs maps each review onto the statutory guidance's templates. This gives the
standardised representation.

It asks two questions, both answered with a single letter:

1. **Which template is the whole review written to?** The model is shown the review's title,
   the text before its first heading and its headings, plus the guidance's document
   templates: for 2016, the overview report, executive summary and individual management
   reviews; for 2026, the DARDR, scoping review and independent management report.
   "None of these" is also an option.
2. **Which section of that template is each review section?** The options are every section
   of the review's template, then the action plan's, then "none of these". A review is mapped
   onto one template only, never piece by piece across several.

Several report sections can share one template section, for example a "Timeline" and a
"Timeline analysis" both being the template's timeline. The answers are only labels: no
section's text is merged, moved or changed. Sections that fit no template section are kept
and flagged as `unmatched`, and so is every section of a review that matched no template.

Each answer keeps the model's probability for every option (read from its first-token
probabilities), so doubtful answers (below 50%) can be checked by hand. Stage 4's candidates
are not used to limit the options. They are kept only to compare against.

## Files

| File                                    | What it is                                                                                 |
| --------------------------------------- | ------------------------------------------------------------------------------------------ |
| `choose_template_section.py`            | The Windmill workflow: builds the prompts, runs two GPU jobs on Blythe, stores the answers |
| `prompts.py`                            | The two prompts and how an answer is read (`PROMPT_VERSION` is stored with every answer)   |
| `get_choices.py`                        | Read-only backend for the app's **Standardised** tab                                       |
| `../../blythe.py`                       | Logging in to Blythe and running the fixed SLURM jobs                                      |
| `narratrace-slurm` repository | What runs on Blythe: `llm_batch.py` and the job files (see its README)                                              |

## Running it

Only Rowan (`rshields2004`) can run the workflow: it uses his own Blythe account, and any
other user's run stops on its first line, dry runs included. That is also why it is not a
step of `f/dhr/O1/pipeline`, which anyone in the team can run. Run it on its own, after stage 4
has run for the same dataset and guidance.

| Input                 | Meaning                                                                                            |
| --------------------- | -------------------------------------------------------------------------------------------------- |
| `dataset`, `guidance` | Whose reviews, against which guidance's templates                                                  |
| `reviews`             | `pilot`: the specification's 10 pilot reviews (do this first); `all`: every review                 |
| `hours`               | SLURM time limit for the section job; 0 works it out from the prompts' size (at most 48)           |
| `check_every_minutes` | How often to check a job that hasn't reported back                                                 |
| `give_up_after_hours` | When to stop waiting for a job and cancel it                                                       |
| `dry_run`             | Builds the document prompts and shows a sample, without contacting Blythe or changing the database |

After submitting each job, the workflow pauses without holding a Windmill worker. The job's
last step wakes it, with a safety check every `check_every_minutes` in case that call is
lost. The workflow then asks SLURM whether the job has ended and downloads the answers.
Cancelling the Windmill run does not cancel a job already on Blythe: use `scancel <job>`
there.

## What it stores

Running again for the same dataset version and guidance replaces its choices.

| Table or view                                                 | What it holds                                                                                                                                                                                      |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `corpus.choice_versions`                                      | How each set of choices was made: model and its commit, prompt version, SLURM jobs, Blythe run folders, timings, code, counts                                                                      |
| `corpus.document_choices` / `corpus.current_document_choices` | Each review's template, or none                                                                                                                                                                    |
| `corpus.template_choices` / `corpus.current_template_choices` | Each section's template section, or `unmatched` / `no answer`, with the model's probabilities                                                                                                      |
| `corpus.current_standardised`                                 | Each review in its template's order: one row per template section, listing the review's sections mapped to it (in the review's order), then the unmatched ones. The text stays in `corpus.blocks`. |

Each run is also logged to MLflow, in the experiment `template-choices`.
