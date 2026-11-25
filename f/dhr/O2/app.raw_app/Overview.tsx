import React from 'react'

const STAGES = [
  {
    n: 1,
    title: 'Check the set-up',
    script: 'f/dhr/O2/check_setup',
    text: 'Checks the runner’s WeKnora key and model, and that every chosen review is in the database, grouped by O1. Nothing starts on Blythe until this passes.',
  },
  {
    n: 2,
    title: 'Start the model',
    script: 'f/dhr/O2/start_model',
    text: 'Asks the model one tiny question through the LLM gateway. If the runner’s model job isn’t running, the gateway starts it on their own Blythe account; the step waits until it answers.',
  },
  {
    n: 3,
    title: 'Make the wiki and queue the reviews',
    script: 'f/dhr/O2/create_wiki',
    text: 'Makes a fresh, wiki-only WeKnora knowledge base for this build, with the DHR instructions, then uploads each chosen review as a Markdown file built from O1’s output.',
  },
  {
    n: 4,
    title: 'Wait, and save the wiki',
    script: 'f/dhr/O2/check_wiki',
    text: 'Every few minutes, asks WeKnora how far it has got and copies every page, link and source into the database. When everything is finished and two checks in a row are quiet, the build is marked built.',
  },
]

export default function Overview() {
  return (
    <div className="page">
      <section className="overview-head">
        <h2>Building an LLM wiki from the reviews</h2>
        <p className="aim">
          O2 turns the reviews O1 prepared into a wiki: linked pages about the people,
          agencies, forms of abuse, events and recommendations in them, each citing the
          reviews it came from. The project’s question is whether organising reviews this way
          helps find similar cases, recurring patterns and recurring recommendations.
        </p>
        <p>
          The wiki is written by WeKnora’s wiki mode, using the team’s own model on Blythe.
          Each run of the O2 flow is one <em>build</em>: its own knowledge base, its own
          settings, and its own saved copy, so builds can be compared side by side.
        </p>
      </section>

      <section>
        <h2>The four stages</h2>
        <ol className="stages">
          {STAGES.map((s) => (
            <li key={s.n}>
              <span className="stage-n">{s.n}</span>
              <div>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
                <code>{s.script}</code>
              </div>
            </li>
          ))}
        </ol>
        <p className="muted">
          The flow is <code>f/dhr/O2/pipeline</code>. It runs only in Windmill, behind its login:
          this page can’t start anything. While a build runs, each check writes a progress line
          to the run’s log and moves its progress bar; this page shows the latest one. To
          watch closer, run <code>f/dhr/tools/llm_watch</code> with the build’s id: a line every
          30 seconds with the reviews read, the wiki’s queue, the pages so far, and the model
          job’s state and speed.
        </p>
      </section>

      <section>
        <h2>What the wiki is given</h2>
        <p>Each review goes in as Markdown, built from O1 rather than the raw PDF text:</p>
        <ul>
          <li>
            <strong>In parts.</strong> WeKnora’s wiki reads only the first 32,768 characters of
            a file, and almost every review is longer (about 120,000 characters is typical), so
            each review is cut at its headings into files of at most 30,000 characters. Each part
            names its review, which part it is, and the section and template section it
            continues, so it reads on its own.
          </li>
          <li>
            <strong>O1’s section hierarchy.</strong> Every heading at its own depth, then its
            text, lists, tables and footnotes in reading order. Running page headers and the
            contents page are left out.
          </li>
          <li>
            <strong>O1’s template mapping.</strong> The review’s report template, and under each
            heading the template section stage 5 mapped it to (2016 guidance by default). It is
            shown again only when it changes, so subsections don’t repeat it.
          </li>
          <li>
            <strong>Whole paragraphs.</strong> A paragraph split by a page break is joined back
            together, and any footnote between the two halves is moved to just after it.
          </li>
          <li>
            <strong>No case metadata.</strong> The DHR Library details stay in the evaluation
            schema, which the wiki never sees.
          </li>
        </ul>
        <p>
          A build can switch the template labels off. That gives an “original headings only”
          wiki to compare against, a direct test of whether O1’s standardisation helps.
        </p>
        <p>
          Every knowledge-base setting that can safely change is an input of the flow: the part
          size, WeKnora’s chunking (strategy, size, overlap, separators), how fast the wiki
          works, and the knowledge graph. Each build records the settings it used, shown on the
          Wikis tab.
        </p>
        <p>
          <strong>Knowledge graph.</strong> On by default, WeKnora also builds an
          entity-relationship graph beside the wiki: for every chunk, the people, agencies,
          places and events it mentions and how they relate (partner of, referred to, assessed,
          and so on). It is not this app’s Graph tab, which shows links between wiki pages; open
          the knowledge base in WeKnora to see it. It costs a model call per chunk, so larger
          chunks make it much cheaper.
        </p>
      </section>

      <section>
        <h2>What is kept</h2>
        <p>
          The database’s <code>wiki</code> schema keeps every build (<code>instances</code>),
          exactly what each was given (<code>documents</code>, with a checksum of each file),
          and a copy of what it wrote: <code>pages</code>, <code>links</code> (the graph) and{' '}
          <code>page_sources</code> (which reviews each page came from). The copy is refreshed
          at every check, so this app shows a wiki while it grows.
        </p>
      </section>

      <section>
        <h2>Before your first build</h2>
        <ol>
          <li>
            Check your Blythe set-up with <code>f/dhr/tools/blythe_check</code>.
          </li>
          <li>
            Join the team’s WeKnora space. Every wiki is built there, reached with the shared key{' '}
            <code>f/dhr/weknora_api_key</code>, so everyone can open every build.
          </li>
          <li>
            In that space, add your own entry for the team model (Settings → Models → Add Model,
            chat, source API): provider Custom (OpenAI-compatible), base URL{' '}
            <code>http://llm-gateway:8000/v1</code>, your own Windmill token as the API key, model
            name <code>Qwen/Qwen3.8-27B-FP8</code>, display name{' '}
            <code>Qwen (&lt;your Windmill username&gt;)</code>, context window 32768, max output
            tokens 8192. The flow uses only the entry with your name, so your builds run on your
            own Blythe account and never on someone else’s.
          </li>
          <li>
            Run <code>f/dhr/O2/pipeline</code>. Start with the 10 pilot reviews.
          </li>
        </ol>
      </section>
    </div>
  )
}
