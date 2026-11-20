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

| File                          | What it is                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------ |
| `choose_template_section.py`  | The Windmill workflow: builds the prompts, runs two GPU jobs on Blythe, stores the answers |
| `prompts.py`                  | The prompts and how an answer is read (`PROMPT_VERSION` is stored with every answer)       |
| `refine.py`                   | The extra sections, the rules that give every section a mapping, and where extras go       |
| `get_choices.py`              | Read-only backend for the app's **Standardised** tab                                       |
| `../../blythe.py`             | Logging in to Blythe and running the fixed SLURM jobs                                      |
| `narratrace-slurm` repository | What runs on Blythe: `llm_batch.py` and the job files (see its README)                     |

## Running it

Only Rowan (`rshields2004`) can run the workflow: it uses his own Blythe account, and any
other user's run stops on its first line, dry runs included. That is also why it is not a
step of `f/dhr/O1/pipeline`, which anyone in the team can run. Run it on its own, after stage 4
has run for the same dataset and guidance.

| Input                 | Meaning                                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------------- |
| `dataset`, `guidance` | Whose reviews, against which guidance's templates                                                   |
| `reviews`             | `pilot`: the specification's 10 pilot reviews (do this first); `all`: every review                  |
| `hours`               | SLURM time limit for the section job; 0 works it out from the prompts' size (at most 48)            |
| `check_every_minutes` | How often to check a job that hasn't reported back                                                  |
| `give_up_after_hours` | When to stop waiting for a job and cancel it                                                        |
| `retemplate`          | For stored choices: the section-led template choice, then the extra question and rules (2 GPU jobs) |
| `refine_only`         | For stored choices: only the extra question (1 GPU job) and the rules (see below)                   |
| `rules_only`          | For stored choices: only the rules, using the extra answers already stored (no GPU, about a minute) |
| `dry_run`             | Shows what would be sent or done, without contacting Blythe or changing the database                |

After submitting each job, the workflow pauses without holding a Windmill worker. The job's
last step wakes it, with a safety check every `check_every_minutes` in case that call is
lost. The workflow then asks SLURM whether the job has ended and downloads the answers.
Cancelling the Windmill run does not cancel a job already on Blythe: use `scancel <job>`
there.

## Reviews the model wasn't sure of

A review the model said follows none of the templates, or gave its template under 50%, is
then judged by its structure (Rowan, 20 Nov 2026): each of its sections is asked the second
question against every other document template too, and the review gets the template whose
sections cover most of its text (each section weighted by its own text, up to 4,000
characters). `document_choices.method` is then `'sections'`, with `sections_fit` (the share of
the text that fits) and `sections_margin` (how far ahead of the next template). The review's
template is doubtful only if the margin is under 0.1. A full run does this automatically
after the first two questions.

## Giving every section a mapping

Then every section gets a final mapping, in reading order (Rowan, 20 Nov 2026; `refine.py`).
The model's own answer always stays in `answer`, `probability`, `probabilities` and
`model_template`; `method` says how the final one was made:

| `method`      | When                                                                                                                                          | Doubtful?          |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| `model`       | The model's own choice                                                                                                                        | Under 50%          |
| `combined`    | Torn between two template sections that together have 80% or more (an analysis that is also lessons learnt): the likelier, `also_*` the other | No                 |
| `extra`       | Asked a third question, one of the **extra sections** many DHRs have but the templates don't name, and surer of that than of its own match    | Under 50%          |
| `parent`      | Said "none of these", inside a mapped section: goes with that section                                                                         | If the parent is   |
| `nearest`     | Said "none of these" but under 80% sure: its likeliest template section                                                                       | Always             |
| `subsections` | A heading with under 200 characters of its own text: what most of its subsections' text got, or "Report part heading" if nothing has most     | If that mapping is |
| `other`       | Nothing else fits: "Other content", the catch-all                                                                                             | Always             |
| `appendix`    | Under an Appendix or Annex heading: "Appendices", whatever it is, with what it is about kept in `also_*`                                      | No                 |

The third question is asked for sections that said "none of these" and sit in no matched
section, and for doubtful matches that aren't two sections in one. The extra sections are
front matter; about the review; glossary and definitions; local and national context;
agency-by-agency detail; family, friends and perpetrator; references and correspondence;
against the 2026 guidance, equality and diversity, and publication and dissemination; and the
three the rules give: Appendices, Report part heading and Other content.

A section is doubtful only for its own mapping. A doubtful review template is shown on the
review (`review_doubtful` on each section), not on every section of it.

Each extra section is placed among a template's own sections where reviews written to that
template most often have it: after the template section that most often comes just before it
(`corpus.extra_placements`). Front matter goes first if it has no place yet, Appendices
always last. The text before a review's first heading (its title page) is shown under the
template's title page, or Front matter if the template has none (`n_opening_blocks` in
`corpus.current_standardised`).

`retemplate = true` and `refine_only = true` run these parts on stored choices;
`rules_only = true` runs only the rules, with the extra answers already stored, so changes
to the rules need no GPU.

## What it stores

Running again for the same dataset version and guidance replaces its choices.

| Table or view                                                 | What it holds                                                                                                                                                                                            |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `corpus.choice_versions`                                      | How each set of choices was made: model and its commit, prompt version, SLURM jobs, Blythe run folders, timings, code, counts                                                                            |
| `corpus.document_choices` / `corpus.current_document_choices` | Each review's template, or none, and whether its sections chose it (`method`)                                                                                                                            |
| `corpus.template_choices` / `corpus.current_template_choices` | Each section's final mapping and how it was made (`method`), the model's own answer and probabilities, and `also_*`                                                                                      |
| `corpus.extra_placements`                                     | Where each extra section sits among each template's own sections                                                                                                                                         |
| `corpus.current_standardised`                                 | Each review in its template's order, extra sections in their place: one row per template section, listing the review's sections mapped to it (in the review's order). The text stays in `corpus.blocks`. |

Each run is also logged to MLflow, in the experiment `template-choices`.
