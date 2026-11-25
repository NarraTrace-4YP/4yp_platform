import React from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// O1's design notes: every main choice, why it was made, and who decided (Rowan unless
// another name or "team" is given). Dates are 2026. Edit the markdown below to add notes.
const markdown = `
# Design notes

Objective 1 (O1) prepares Domestic Homicide Reviews (DHRs) for use by the LLM wiki. The original JSON files organise text by page, but pages are not useful units for analysing a report. A section, such as a chronology or a set of recommendations, often spans several pages. Reports also use different headings for similar content.

O1 addresses these problems by producing three representations of each review:

- **Page-based:** the original JSON, retained as the source of evidence.
- **Hierarchical:** the same material grouped into sections and subsections, preserving their order and relationships.
- **Standardised:** sections assigned to common categories so equivalent content is easier to find across reviews.

All three are stored in PostgreSQL, alongside links to available Home Office DHR Library case metadata. The wiki uses the review content, while the case metadata is kept separately for evaluation.

These notes explain the main design decisions, why they were made and where the implementation differs from the project specification. Sections 1 to 5 below are processing steps within O1, not the four stages of the overall project. Live figures are available on the Overview and Data tabs.

**Contents:** [Platform](#platform) | [Datasets](#datasets) | [Step 1](#stage-1-load-reviews) | [Step 2](#stage-2-link-case-details) | [Step 3](#stage-3-group-sections) | [Step 4](#stage-4-find-template-candidates) | [Step 5](#stage-5-choose-templates-with-the-llm) | [Pilot set](#pilot-set) | [Specification deviations](#specification-deviations) | [Still open](#still-open)

---

## Platform

| Choice | Why |
|---|---|
| **PostgreSQL** (15 Nov) | All pipeline outputs are stored in one database, rather than separate files for each processing step. SQL views expose the current version of each representation so later stages do not need to work out which run to use. Database permissions restrict the wiki agent to the \`corpus\` schema. |
| **Windmill Community Edition** (15 Nov) | Windmill replaced Airflow during development because the team found Airflow difficult to work with. Windmill provides a browser-based editor for scripts and workflows, so team members do not need a local development environment to make and test pipeline changes. Its community edition avoids additional software costs. Windmill is already named in the submitted specification, so this is a change from the earlier implementation, not a deviation from the specification. |
| **GitHub remains the main code repository** (17 Nov) | Merging changes into the main branch deploys them to Windmill. A custom synchronisation process checks for edits made in Windmill every 15 minutes and returns them to GitHub through a pull request called "Changes made in Windmill". This lets the team work in the browser while keeping a shared record of changes and retaining code review. The custom process avoids the two-user limit of Windmill's free Git synchronisation arrangement. |
| **Runs are started through Windmill only** (19 Nov) | This public app is read-only. It only calls approved read-only \`get_*\` scripts, and an automated test enforces this restriction. Starting a pipeline might submit a GPU job under a team member's university account, so this action requires authentication through Windmill. |
| **Run provenance and metrics** | Each run records the Hugging Face dataset commit, the Git commit of the pipeline code and the SHA-256 checksum of each guidance file. Metrics are logged to MLflow. These records identify the inputs and code behind a result, making it easier to compare runs and reproduce previous experiments. Exact reproduction also depends on retaining the inputs and model configuration. |
| **Local LLM inference on Warwick's Blythe cluster** | Open-weight models run through vLLM on university GPUs rather than through external LLM APIs. This keeps sensitive review content within the project and university environment, in line with the specification. It also gives the team control over model versions and inference settings. |
| **Individual Blythe accounts** (23 Nov) | Each team member uses their own university account, with credentials held in their own Windmill variables. SCRTP agreed to unattended login from the project server. Individual accounts retain accountability and avoid sharing one person's cluster access across the team. |
| **Restricted SLURM job submission** (19 Nov) | Windmill starts only approved SLURM job files already stored on Blythe, rather than submitting arbitrary commands. When a job finishes, it calls a one-time callback URL to resume the waiting workflow. This limits what the integration is allowed to execute and avoids occupying a Windmill worker while a job waits for GPU resources. |
| **No database backups at present** | The approach treats the database as a derived resource which is rebuilt from the source datasets and pipeline code. This avoids maintaining another copy of the database. However, rebuilding depends on continued access to the original inputs and may require expensive GPU runs. Live DHR Library metadata and guidance files might also change. Exact recovery is not guaranteed, so this remains a recovery risk rather than an established guarantee of reproducibility. |

## Datasets

### DHRC-100 and DHRC-RAW

| Dataset | Description | Purpose |
|---|---|---|
| **DHRC-100** | 100 reviews with manually validated headings and parent relationships. | The primary dataset specified in O1. Its checked structure provides a reliable starting point for testing the hierarchy-building process and constructing the wiki. |
| **DHRC-RAW** | 702 reviews converted using Datalab/Marker, without manual structural validation. These include the 100 reviews in DHRC-100. | An additional dataset for testing whether the pipeline works on a larger collection without requiring someone to correct every report first. |

**Deviation from the specification (O1 and Section 4.1):** O1 originally covered DHRC-100 only. DHRC-RAW extends this work to test scalability and dependence on manual correction. DHRC-100 remains the primary corpus. DHRC-RAW is not a wholly unseen test set because 100 reviews overlap with DHRC-100. The remaining 602 reviews provide additional cases, but any independent evaluation must also account for which reviews were used during development.

### How the datasets are handled

- **Both datasets use the same database tables, but are kept separate by a dataset label** (16 Nov). This avoids maintaining two database structures and allows the same pipeline to process both. The overlapping reviews are not merged, so their validated and unvalidated versions remain available for comparison.
- **Future wiki construction:** Stage 2 of the overall project (O2) is intended to support wikis built from DHRC-100, DHRC-RAW or both. How overlapping reviews will be handled in a combined wiki is still undecided. They must not be counted twice in case-level analysis.
- **Different validation policies:** a faulty DHRC-100 review stops the run because its structure is expected to have been checked. For DHRC-RAW, faulty reviews are skipped and recorded. This allows the larger dataset to be processed while making failures visible. The number of successfully processed reviews, rather than the original dataset size alone, should be used when reporting results.
- **OCR is outside the scope of O1.** Both datasets already contain machine-readable JSON. O1 reorganises the extracted content rather than extracting text from the original PDFs again. It therefore inherits any OCR and layout recognition errors in the input.

## Stage 1: load reviews

The first step loads the original page-based JSON into PostgreSQL. This representation is retained even after sections have been grouped and standardised, so later outputs are traceable to the source.

- **Check file integrity.** Each file is checked against the dataset's expected checksum before loading. A checksum identifies changes to the file's contents, preventing a modified or damaged file from being silently accepted.
- **Load one review at a time.** This limits memory use as the dataset grows. The load is committed as one transaction, so a failed load does not leave a partly written version in the database. For DHRC-RAW, this applies to the accepted reviews after the documented skipping policy has been applied.
- **Retain the original JSON unchanged.** The exact Hugging Face commit is stored with it. This preserves the original page order, layout information and block identifiers needed to trace later wiki claims back to their evidence.
- **Remove outdated derived results on reload.** Reloading the same dataset version replaces its stored reviews and removes their sections and template candidates. Otherwise, derived records might refer to text or block identifiers which no longer exist.

This follows Section 4.1 of the specification, which requires the original representation to remain available alongside the two derived representations.

## Stage 2: link case details

The Home Office DHR Library records case characteristics, such as age groups, relationships and themes, alongside published reviews. These characteristics are useful for checking whether the wiki extracts relevant information from the review text.

### Collecting case metadata

The DHR Library exposes its characteristics through search filters rather than a downloadable metadata file. The collector therefore runs one search for every available filter option, 832 searches in total. If a review appears in the results for an option, the corresponding value is recorded against that review. Requests are limited to one per second to reduce the load placed on the website.

Before the collected metadata is written to the database, the collector checks that individual filters return fewer reviews than the complete library, that result pages have no gaps and that the number of results collected agrees with the advertised count. These checks are intended to catch failed or incorrectly applied searches. They do not establish that the Library's labels are correct.

### Keeping metadata separate from review content

The case metadata is stored in the \`evaluation\` schema, which the wiki agent does not have permission to read. This prevents the agent from directly obtaining the reference labels used to evaluate its findings. The specification also requires these labels to be withheld from the RAG index and ordinary construction prompts. Case metadata is only provided where explicitly required, such as the metadata-guided critical-event selection task (T4).

This is consistent with Sections 4.2 and 4.3 of the specification. It ensures the later evaluation tests what the system derives from review content rather than whether it reproduces labels it was already given.

### Missing metadata

- Each review is also assigned a publication source, such as the Home Office, HALT or another source. Around 76 DHRC-RAW reviews are not in the DHR Library. The Attributes tab helps show how metadata coverage and publication sources differ between datasets.
- If a review is not returned for a filter, the value is unrecorded, not necessarily false. The Library warns that its filters might be inaccurate. Missing labels must therefore not be treated as proof that a characteristic is absent.
- Metadata-based evaluation needs to distinguish reviews with available reference labels from reviews without them. The larger corpus does not automatically provide a larger labelled evaluation set.

## Stage 3: group sections

The original JSON groups text blocks by page. A section often begins on one page and continues onto the next, and a single page might contain several sections. Grouping by page alone would break up related information.

The hierarchical representation groups blocks under their headings and preserves the relationships between sections and subsections. For example, an agency-specific subsection under Analysis remains part of Analysis even if the subsection continues onto another page. The wiki can then retrieve the subsection together with its surrounding context.

### Building and checking the hierarchy

- **DHRC-100 uses its manually validated headings and parent relationships.** DHRC-RAW instead uses the headings produced by Datalab, marked as unchecked (17 Nov). On 10 DHRC-100 reviews, the Datalab-based method produced the same sections as the checked method. This supports testing the method on DHRC-RAW, but is not evidence that every raw review has a correct hierarchy.
- **Check structural consistency before storing.** Blocks must belong to an earlier heading, except content before the first heading. A parent must appear before its child and be at a higher heading level. Sections must remain continuous in document order. These checks identify invalid structures but do not prove that headings have been interpreted correctly.
- **Remove running page headers and footers from the hierarchy.** Repeated report titles and page numbers add noise when a section is retrieved across multiple pages. They remain available in the original page-based JSON.
- **Handle content before the first heading.** It is treated as title-page and contents material so it is not dropped or attached to the first substantive section. This is a default assumption, rather than a guarantee that all such material is front matter.
- **Correct one known ordering error in Annie - Hartlepool - 2018_a.** Some appendix titles appear before their APPENDIX n label, causing a child heading to be read before its parent. The fix moves the appendix label to the start of its own section only. The exception is recorded in the script, and the original JSON remains unchanged.

### Paragraphs and footnotes across page boundaries

**Implementation detail and qualification of Section 4.1 (17 Nov):** paragraphs split across pages remain as separate source blocks in O1. Rejoining them into one block would change the block structure used for citations. The hierarchical representation still groups their sections across pages as required by the specification.

Instead, O2 is expected to join consecutive pieces of a paragraph when preparing text for the wiki agent and place a footnote after the block which refers to it. The original block identifiers must remain available for citations. This preserves source traceability while providing more natural reading context. The joining and footnote handling are downstream requirements, not completed O1 features.

## Stage 4: find template candidates

Grouping text into sections does not make reports directly comparable. For example, one report might use the heading "Lessons Learnt" and another "Learning from the Review" for similar material. Standardisation assigns these sections to a common category without replacing their original headings or text.

### Choosing the templates

Templates are taken from the statutory guidance published by the Home Office:

| Guidance | Templates used | Purpose |
|---|---|---|
| **2016 DHR guidance** | Individual Management Review (IMR) outline, IMR, overview report, executive summary and action plan. | The main standard for O1 and the reference specified for the DHR corpus. |
| **September 2026 DARDR guidance** | DARDR report, action plan, scoping review and IMR. | An additional comparison to examine how the existing DHR material maps to the newer reporting structure. |

**Deviation from Section 4.1:** the specification only proposed the 2016 overview-report template. Some documents in the corpus are executive summaries or IMRs, and an overview-report template is not designed for these document types. Using the other official templates avoids forcing them into inappropriate categories. The trade-off is that sections belonging to different templates may need an additional shared mapping for comparisons across document types.

**Scope extension relative to Section 8:** the newer DARDR guidance is used as a comparison schema only. The corpus still consists of DHRs, and no new DARDR cases are introduced. This does not establish that results from DHRs generalise to the wider range of deaths covered by DARDRs. The 2016 mapping remains the primary result required by O1.

### Loading the guidance

The guidance files are downloaded from GOV.UK at the start of each run. Expected headings are defined in the code, and their descriptions are extracted from the guidance. The run fails if the expected structure no longer matches the downloaded file.

The SHA-256 checksum of each file records exactly which version was used. This makes changes detectable, although retaining a copy of each version would also be necessary if a previous run needed to be reproduced after the published file changed.

### Finding possible matches

Two methods are used to find potential template sections for every source section:

1. **Heading similarity.** Section numbers and common words are removed from headings before fuzzy string matching. This compares how similar the remaining wording is, allowing minor spelling and wording differences. Only matches scoring at least 60 out of 100 are retained.
2. **Content similarity.** The BAAI/bge-small-en-v1.5 embedding model converts the section text and template descriptions into numerical representations. Their similarity helps identify sections which discuss the same subject even when their headings use different words. This runs on the project server's CPU, leaving Blythe GPUs available for LLM inference.

The five highest-ranked matches from each method are stored, together with their scores and ranks. Using both methods matters because neither consistently identifies the correct section on its own. Of 1,261 DHRC-100 sections with headings identical to a 2016 template heading, only 440 had the corresponding template section ranked first by content similarity. This demonstrates disagreement between the methods, not which method was correct in every case.

The results are potential matches rather than final classifications. Tied scores are ordered according to template order, so the highest rank alone is not reliable evidence of a better match. The threshold of 60 and the limit of five matches are current design settings, rather than demonstrated optimal values.

## Stage 5: choose templates with the LLM

Stage 4 measures how similar sections are to the guidance. An LLM is then used to interpret the purpose of the section in the context of the report and decide where it belongs. Its output is a label, not a rewritten version of the review.

**Model:** Qwen3.8-27B (FP8), run through offline vLLM on one Blythe L40 GPU. The model is used for all four dataset and guidance combinations: DHRC-100 and DHRC-RAW, each against the 2016 and 2026 guidance.

### 1. Identify the type of report

The model first selects the template for the whole review (19 Nov). It then classifies individual sections using that template, rather than selecting unrelated template sections from several document types. This makes the standardised representation more consistent within a report. An action plan is also available alongside the selected template because reports often contain action plans as additional material.

If the model selects "none of these" for the whole review with a probability below 80%, the most likely named template is used and the review is marked doubtful. This allows incomplete or unusual reports to be processed instead of losing all their section mappings. The decision is provisional because the report may genuinely not fit a template. The 80% threshold is a heuristic.

### 2. Classify each section

**Deviation from the candidate-selection approach in Section 4.1 and Appendix B:** rather than restricting the LLM to the matches retrieved in Stage 4, the model sees every section in the selected template, the associated action plan and a "none of these" option. This prevents a correct label within the selected template from being excluded because fuzzy or embedding retrieval ranked it poorly. Stage 4's results are still stored for inspection and comparison. The trade-off is a larger set of choices in the prompt, and errors in the document-level template choice still restrict the available labels.

The model returns a single letter identifying its chosen option. The probabilities for every available option are retained. This makes it possible to examine alternative answers or apply different rules later without rerunning inference. Answers with a probability below 50% are marked doubtful, both at section level and for review-level decisions. The probabilities reflect model preference between the options, not a validated probability of being correct. The threshold helps prioritise manual checking.

Several source sections are allowed to share the same template label. A review might divide its chronology into several subsections, for example, even though the guidance provides only one Chronology category. Requiring a one-to-one mapping would incorrectly exclude some of this material.

### 3. Reconsider doubtful report templates

A wrong choice of report template affects every subsequent section classification. For doubtful reviews, the pipeline therefore tests the sections against the other templates and selects the template which best fits the structure of the whole review (20 Nov). This process is called retemplating.

Using the title page to settle doubtful cases was considered but rejected because a title might not reliably distinguish a full report from a summary or extract. Using the contents of the document provides additional evidence about its type, although this too requires validation.

The structure score was revised on 21 Nov:

**Structure score = fit x number of template sections filled / highest number of sections filled by any candidate template.**

The score balances how well the review's sections fit a template with the number of distinct template sections they cover. A short template might match a few sections well while failing to describe much of a full report. The earlier text-fit-only method assigned 141 DHRC-RAW reviews to the six-section 2026 IMR template, most of which were full reports according to the design notes. The revised score reduced this to nine. Using the proportion of each template filled was also rejected because short templates are easier to fill completely.

The revised score addresses an observed preference for small templates. It might conversely penalise genuinely short or incomplete reports. All model answers are retained so alternative scoring methods remain testable without another GPU run. The reduction in IMR assignments does not itself establish improved classification accuracy.

### 4. Resolve unmatched or doubtful sections

**Deviation from Section 4.1 and Appendix B:** the specification proposed retaining and flagging sections without a suitable template match. The implementation instead applies rules to give every section a final category, including categories outside the official template. The original text is always retained. This makes it easier for O2 to access material which would otherwise be grouped together as unmatched.

The final mapping method is recorded for each section: model, combined, extra section, parent, nearest, subsections, appendix or other content. These labels distinguish direct model selections from rule-based assignments. A combined mapping covers two template sections. Parent, nearest and subsections use structural relationships or nearby classifications to resolve a difficult section. The exact rule conditions remain defined by the implementation.

Extra categories are used for common material not named in the official templates, such as glossaries and front matter. They are positioned according to where the material most commonly appears in reports. Appendices are assigned to Appendices, except when the section has been mapped to an action-plan template, since an action plan remains an action plan even if it appears in an appendix (21 Nov).

This approach aims to retain all useful content in an accessible representation, not to establish that all sections match the statutory guidance. Rule-based assignments, extra categories and Other content must not be counted as confirmed official-template matches. Whether the original unmatched decision remains explicitly queryable needs to be checked against the specification. Doubt flags and mapping methods provide useful information but do not automatically replace an unmatched flag.

### 5. Improve the context provided to the LLM

Prompt v3 (22 Nov) includes the entire review's outline before asking the model to classify a section. The outline provides its position and surrounding headings, which help distinguish sections discussing similar topics. For example, Background and Chronology might describe the same events but serve different purposes, while Analysis and Lessons discuss related findings at different points in the report.

The outline appears first in each prompt so vLLM can reuse the shared beginning when processing multiple sections of the same review. Classifying every section with one model response was considered but is not currently supported by the batch job, which expects one-token answers. Separate questions also retain individual option probabilities.

### 6. Reduce inference time and resource use

- **Parallel runs** (22 Nov): one Windmill run supports multiple dataset and guidance combinations on Blythe. Independent jobs allow pairs to run at the same time, when cluster resources are available. A failed pair does not prevent the other pairs from completing. Parallel execution reduces elapsed processing time when sufficient resources are available, but queue times are still a constraint.
- **One model** (22 Nov): a second model was trialled but removed because its two-GPU jobs did not obtain resources. Using one model reduces resource requirements and keeps prompt comparisons simpler. This does not establish that the selected model is the best for the task, and model-specific behaviour remains a limitation.

### Results of the mapping changes

The following figures show the number of sections marked doubtful before and after the mapping rules in prompt v2 (20-21 Nov), and the stored counts for prompt v3 (22-23 Nov).

| Dataset | Guidance | v2 before rules | v2 after rules | v3 doubtful |
|---|---|---:|---:|---:|
| DHRC-100 | 2016 DHR | 499 | 111 | 52 |
| DHRC-100 | 2026 DARDR | 2,495 | 122 | 51 |
| DHRC-RAW | 2016 DHR | 5,633 | 1,181 | 766 |
| DHRC-RAW | 2026 DARDR | 29,301 | 1,223 | 772 |

No section is left without a final category. Around three-quarters of the doubt remaining after the v2 rules was attributed to the model being torn between two real template sections.

These figures show how the pipeline changes the number of assignments marked doubtful. They do not measure mapping accuracy. Rules can remove a doubt flag without necessarily correcting an assignment, and a model can select an incorrect answer with high probability. A manually labelled sample is needed to establish whether the changes improve accuracy, particularly for sections assigned by rules.

## Pilot set

Section 5.2 of the specification proposes running around ten reviews through the complete four-stage project pipeline before scaling to DHRC-100. This is intended to identify integration problems while they are still inexpensive to fix.

The O1 notes record a ten-review comparison of the validated and Datalab-based hierarchy-building methods. Both produced the same sections for those reviews. This is a useful check of one O1 component, but it is not the full end-to-end pilot described in the specification, nor does it validate the LLM mappings.

The recorded results also show that O1 processing has been scaled beyond 100 reviews. Whether the full project pilot preceded that expansion, and whether the ten reviews were fixed before experimentation, is not established in these notes. The end-to-end pilot should still be recorded separately.

## Specification deviations

The following compares the current implementation with the submitted CS407 specification, rather than with earlier development plans.

| Original requirement | Current approach and justification | Limitation or follow-up |
|---|---|---|
| O1 and Section 4.1: prepare DHRC-100 using validated headings. | DHRC-100 is retained and DHRC-RAW is added using unchecked headings to test scalability and reduce dependence on manual preparation. | Report results separately and account for the 100 overlapping reviews. |
| Section 4.1 and Appendix D: standardise against the 2016 overview-report template. | Select from all five 2016 templates because the corpus contains different document types. | Cross-template comparisons need a consistent way of identifying equivalent categories. |
| O1 and Section 8: use the 2016 guidance and study DHRs only. | Also map the existing DHR corpus to the 2026 DARDR guidance for comparison. No DARDR cases are added. | Keep 2016 results primary. Do not generalise the findings to DARDR cases without separate evidence. |
| Section 4.1 and Appendix B: retrieve candidate matches, then use an LLM to choose an appropriate match. | Offer the LLM all sections of the selected template and its action plan rather than restricting it to retrieved candidates. This reduces the risk of excluding the correct label. | Candidate retrieval remains an intermediate result, not a restriction on the final classifier. Compare the approaches if making claims about the benefit of retrieval. |
| Section 4.1 and Appendix B: retain sections with no suitable match and flag them as unmatched. | Apply structural rules and additional categories so all content receives a final placement. | Preserve the distinction between genuine official-template matches and fallback assignments. Verify that original unmatched decisions remain identifiable. |
| Section 4.1: group blocks across pages while preserving content, references and layout. | Group sections across pages, retain page-split paragraph blocks for citations, omit running headers and footers from the hierarchy, and defer paragraph and footnote reassembly to O2. | Verify source coverage and citation traceability, including the known appendix-ordering correction. The original JSON remains unchanged. |
| O2 and O5: construct and evaluate the wiki over DHRC-100. | Additional RAW and combined wiki conditions are planned to investigate corpus size and structural quality. | These are extensions, not replacements for the specified DHRC-100 wiki and matched RAG comparison. Deduplication is unresolved. |

The use of PostgreSQL, Windmill, Blythe, GitHub, local inference and separate evaluation metadata is consistent with the specification or provides implementation details which the specification did not prescribe. The switch from Airflow to Windmill is therefore not a deviation from the submitted document.
`;


export default function NotesTab() {
  return (
    <div className="page">
      <main className="notes-content">
        <Markdown
          remarkPlugins={[remarkGfm]}
          components={{
            // Headings get ids so the contents links can scroll to them (the app runs inside
            // Windmill's frame, so a click scrolls rather than changing the URL).
            h2: ({ children }) => <h2 id={slug(String(children))}>{children}</h2>,
            a: ({ href, children }) =>
              href?.startsWith('#') ? (
                <a
                  href={href}
                  onClick={(event) => {
                    event.preventDefault()
                    document
                      .getElementById(href!.slice(1))
                      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }}
                >
                  {children}
                </a>
              ) : (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              ),
          }}
        >
          {markdown}
        </Markdown>
      </main>
    </div>
  )
}

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}
