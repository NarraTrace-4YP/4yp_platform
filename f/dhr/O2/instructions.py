"""O2 stages 1-4, shared: the default instructions WeKnora's wiki builder is given for DHRs.

WeKnora's wiki has two places for our own guidance (each at most 4,000 bytes): what to
extract as pages, and how to write them. Its output format and its citations stay its own.
A build can replace either in the flow's form; each build records the text it used.
"""

EXTRACTION = """\
These documents are Domestic Homicide Reviews (DHRs) published in England and Wales. Each \
reviews the death of one person and how agencies were involved beforehand.

Make pages for:
- People in the case, under the pseudonyms the review uses (the person who died, the \
perpetrator, children, family members, friends). Never guess real names. Keep people from \
different reviews apart even when they share a pseudonym: add the review's area and year \
(e.g. "Mary (Bromley, 2018)").
- Agencies and services (police forces, health trusts, GPs, children's and adults' social \
care, housing, probation, IDVA and MARAC services, charities). One page per organisation \
type across reviews (e.g. "Police"), with the specific force or trust named in the text.
- Forms of abuse and risk factors (e.g. coercive control, stalking, economic abuse, \
separation, substance misuse, mental ill health, previous domestic abuse), including \
those a review describes without naming.
- Events and patterns before the death (contacts with agencies, disclosures, referrals, \
risk assessments, missed opportunities, escalation).
- Recommendations and lessons, grouped by the agency they are for and by theme, so the \
same recommendation made in different reviews ends up on one page.

Skip: the review process itself (panel membership, terms of reference, dates of meetings), \
contents pages, glossaries and acknowledgements.
"""

CONTENT = """\
Write in plain, neutral British English for safeguarding practitioners. Report what the \
reviews say; do not add judgements, causes or advice of your own. Say which review each \
point comes from. Where reviews disagree or a review is uncertain, say so. Use the \
pseudonyms exactly as the reviews do. Keep dates as precise as the review gives them.
"""

GRANULARITIES = ("focused", "standard", "exhaustive")
