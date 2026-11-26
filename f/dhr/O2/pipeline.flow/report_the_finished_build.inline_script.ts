export async function main(created: any, checks: any[]) {
  const last = checks[checks.length - 1] ?? {}
  return {
    instance_id: created.instance_id,
    name: created.name,
    weknora_kb_id: created.kb_id,
    uploaded: created.uploaded,
    upload_failures: created.failed,
    documents: last.documents,
    failed_in_weknora: last.failed,
    pages_by_type: last.pages_by_type,
    saved: last.saved,
    checks: checks.length,
  }
}
