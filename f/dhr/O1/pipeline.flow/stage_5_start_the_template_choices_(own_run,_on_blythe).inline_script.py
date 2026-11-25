"""O1 stage 5, flow: start choose_template_section as its own run.

Stage 5 is a workflow that pauses for days waiting on Blythe. Its wake-up links only work in a
run of its own, not as a step of this flow (tested 24 Nov 2026), so this step starts it without
a parent job and returns its run link. It runs on the Blythe account of whoever runs this flow.
"""

import os

import wmill

STAGE_5 = "f/dhr/O1/choose_template_section/choose_template_section"


def main(dataset: str, guidance: str, reviews: str, prompt: str, dry_run: bool = False):
    client = wmill.Windmill()
    args = {
        "dataset": dataset,
        "guidance": guidance,
        "reviews": reviews,
        "prompt": prompt,
        "stage": "full",
        "dry_run": dry_run,
    }
    # Posting directly (not run_script_by_path_async) leaves out parent_job and root_job.
    job = client.post(f"/w/{client.workspace}/jobs/run/p/{STAGE_5}", json=args).text.strip('"')
    base = os.environ.get("WM_BASE_URL", "").rstrip("/")
    return {
        "result": "Stage 5 started as its own run; follow it from the link.",
        "run": job,
        "link": f"{base}/run/{job}?workspace={client.workspace}",
        "args": args,
    }
