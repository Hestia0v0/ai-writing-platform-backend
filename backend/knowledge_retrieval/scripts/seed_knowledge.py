"""
Run this script against a running platform to seed writing technique entries.
  python scripts/seed_knowledge.py [--base-url http://localhost:8000]
"""
import sys
import argparse
import urllib.request
import json

TECHNIQUES = [
    {
        "slug": "topic-sentence",
        "title": "Topic Sentence",
        "content": (
            "A topic sentence introduces the main idea of a paragraph and signals to the reader "
            "what the paragraph will discuss. It typically appears as the first sentence and "
            "should directly support the essay's thesis. A strong topic sentence is specific, "
            "arguable, and connects clearly to the evidence that follows within the paragraph."
        ),
    },
    {
        "slug": "coherence",
        "title": "Coherence",
        "content": (
            "Coherence refers to the logical flow and connectedness of ideas within and between "
            "paragraphs. A coherent essay moves smoothly from one idea to the next without "
            "jarring jumps. Techniques for achieving coherence include repeating key terms, "
            "using pronouns to refer back to earlier nouns, and maintaining a consistent "
            "point of view and tense throughout the piece."
        ),
    },
    {
        "slug": "transitions",
        "title": "Transitions",
        "content": (
            "Transition words and phrases guide the reader from one idea to the next, showing "
            "the relationship between sentences and paragraphs. Common transitions include "
            "'however', 'furthermore', 'in contrast', 'as a result', and 'for example'. "
            "Effective transitions signal addition, contrast, causation, sequence, or "
            "exemplification, helping the reader follow the writer's logic without confusion."
        ),
    },
    {
        "slug": "argument-structure",
        "title": "Argument Structure",
        "content": (
            "A well-structured argument presents a clear claim, supports it with relevant "
            "evidence, explains how that evidence supports the claim (the warrant), and "
            "addresses potential counterarguments. The classical structure follows: "
            "introduction with thesis, body paragraphs each with one main point, "
            "counterargument and rebuttal, and a conclusion that synthesises the argument."
        ),
    },
    {
        "slug": "evidence-use",
        "title": "Evidence Use",
        "content": (
            "Strong writing uses evidence—facts, statistics, expert opinions, or textual "
            "quotations—to support each claim. Effective evidence use involves three steps: "
            "introducing the source, presenting the evidence (quote or paraphrase), and "
            "then explaining how it supports your argument. Never drop a quotation into a "
            "paragraph without analysis; always unpack what it means in your own words."
        ),
    },
    {
        "slug": "thesis-statement",
        "title": "Thesis Statement",
        "content": (
            "The thesis statement is the central claim of an essay, usually placed at the end "
            "of the introduction. A strong thesis is specific, debatable, and scoped to what "
            "the essay can prove. Avoid vague statements like 'Technology is important.' "
            "Instead, stake a clear position: 'Social media algorithms exacerbate political "
            "polarisation by prioritising emotionally charged content over factual reporting.'"
        ),
    },
    {
        "slug": "counterargument",
        "title": "Counterargument",
        "content": (
            "Addressing counterarguments strengthens an essay by demonstrating that the writer "
            "has considered opposing perspectives. The technique involves presenting the "
            "strongest version of the opposing view (steelmanning), then explaining why your "
            "argument still holds despite that objection. A concession ('While it is true "
            "that...') followed by a rebuttal ('...nevertheless...') signals intellectual "
            "honesty and persuasive confidence."
        ),
    },
    {
        "slug": "paragraph-development",
        "title": "Paragraph Development",
        "content": (
            "A well-developed paragraph contains a topic sentence, supporting details, "
            "analysis of those details, and a closing sentence that links back to the thesis. "
            "The PEEL model (Point, Evidence, Explain, Link) offers a useful scaffold. "
            "Each paragraph should focus on a single idea; if you find yourself covering "
            "two distinct points, split the paragraph into two."
        ),
    },
    {
        "slug": "conciseness",
        "title": "Conciseness",
        "content": (
            "Concise writing conveys meaning with as few words as necessary—without sacrificing "
            "clarity or completeness. Common techniques include eliminating redundant phrases "
            "('in order to' → 'to'), cutting throat-clearing openers ('It is important to note "
            "that...'), preferring strong verbs over weak verb-noun combos ('make a decision' "
            "→ 'decide'), and removing filler adverbs ('very', 'really', 'quite')."
        ),
    },
    {
        "slug": "active-voice",
        "title": "Active Voice",
        "content": (
            "Active voice makes sentences clearer and more direct by placing the subject before "
            "the verb: 'The committee approved the proposal' rather than 'The proposal was "
            "approved by the committee.' Passive voice is appropriate when the agent is "
            "unknown, unimportant, or deliberately backgrounded, but overuse creates "
            "bureaucratic, lifeless prose. Aim for active constructions in at least "
            "80% of your sentences."
        ),
    },
    {
        "slug": "sentence-variety",
        "title": "Sentence Variety",
        "content": (
            "Varying sentence length and structure keeps prose engaging. A passage of "
            "exclusively short sentences feels choppy; exclusively long ones feel dense. "
            "Mix simple, compound, and complex sentences. Use a short punchy sentence after "
            "a longer explanation to emphasise a key point. Begin some sentences with "
            "participial phrases, others with adverbial clauses, and avoid always leading "
            "with the subject-verb pattern."
        ),
    },
    {
        "slug": "word-choice",
        "title": "Word Choice (Diction)",
        "content": (
            "Precise word choice sharpens meaning and establishes tone. Choose words that "
            "accurately capture your intended meaning rather than approximate synonyms. "
            "Favour concrete, specific language over vague abstractions: 'a 12% drop in "
            "quarterly revenue' is sharper than 'a significant financial decline'. Consider "
            "connotation as well as denotation—'assertive' and 'aggressive' have similar "
            "meanings but very different implications."
        ),
    },
    {
        "slug": "introductions",
        "title": "Writing Introductions",
        "content": (
            "A strong introduction accomplishes three things: it hooks the reader, provides "
            "necessary context, and ends with the thesis. Effective hooks include a striking "
            "statistic, a brief anecdote, a provocative question, or a counterintuitive "
            "claim. Avoid starting with dictionary definitions ('Webster defines...') or "
            "overly broad statements ('Since the dawn of time...'). The introduction should "
            "be proportionate—roughly 10% of the total essay length."
        ),
    },
    {
        "slug": "conclusions",
        "title": "Writing Conclusions",
        "content": (
            "A conclusion should synthesise—not merely summarise—the argument. Restate the "
            "thesis in new language, briefly recall the main supporting points, and then "
            "broaden outward to the larger significance of the argument ('So what?'). "
            "Avoid introducing new evidence in the conclusion. Strong closings often end "
            "with a forward-looking statement, a call to action, or a resonant image that "
            "echoes the introduction."
        ),
    },
    {
        "slug": "citations",
        "title": "Citations and Source Integration",
        "content": (
            "Integrating sources smoothly requires introducing them, presenting the evidence, "
            "and analysing it—the 'sandwich' method. Use signal phrases ('According to Smith', "
            "'As Johnson argues') to attribute ideas before quoting or paraphrasing. "
            "Paraphrasing is generally preferred over direct quotation; reserve quotations "
            "for when the original wording is uniquely powerful or precise. Always follow "
            "the required citation style (APA, MLA, Chicago) consistently."
        ),
    },
    {
        "slug": "clarity",
        "title": "Clarity",
        "content": (
            "Clarity means the reader immediately understands your meaning without needing "
            "to re-read sentences. Achieve it by: keeping subjects and verbs close together, "
            "avoiding excessive nominalisations ('the achievement of goals' → 'achieving "
            "goals'), defining technical terms when first introduced, and structuring "
            "sentences so the most important information comes at the end—the position "
            "of natural emphasis in English."
        ),
    },
    {
        "slug": "tone",
        "title": "Tone",
        "content": (
            "Tone is the writer's attitude toward the subject and audience, conveyed through "
            "word choice, sentence structure, and level of formality. Academic writing "
            "typically uses a formal, objective tone—avoiding contractions, first-person "
            "in some disciplines, and colloquialisms. However, appropriate tone varies by "
            "genre and audience: a personal essay may be conversational, while a legal "
            "brief must be precise and impersonal. Inconsistencies in tone undermine "
            "credibility."
        ),
    },
    {
        "slug": "audience-awareness",
        "title": "Audience Awareness",
        "content": (
            "Writing for a specific audience means calibrating vocabulary, assumed knowledge, "
            "depth of explanation, and tone. Before drafting, ask: Who will read this? What "
            "do they already know? What do they need from this text? An expert audience "
            "tolerates jargon and values density; a general audience requires plain language "
            "and more background. Ignoring audience is the root cause of most communication "
            "failures."
        ),
    },
    {
        "slug": "revision-strategies",
        "title": "Revision Strategies",
        "content": (
            "Effective revision goes beyond proofreading—it involves rethinking argument, "
            "structure, and clarity at the global, paragraph, and sentence levels. "
            "Strategies include: reading the draft aloud to catch awkward phrasing, "
            "reverse-outlining (summarising each paragraph after writing to check structure), "
            "getting feedback from a peer, and leaving time between drafting and revising "
            "so you read with fresh eyes. Revision is where good writing is made."
        ),
    },
    {
        "slug": "proofreading",
        "title": "Proofreading",
        "content": (
            "Proofreading is the final pass that catches surface errors—spelling, punctuation, "
            "grammar, and formatting. Read slowly, ideally in a different medium (print vs "
            "screen). Read backwards sentence by sentence to short-circuit your brain's "
            "tendency to auto-correct errors you know should be there. Use a checklist for "
            "your most common error types. Do not rely solely on spell-checkers, which miss "
            "correctly spelled but contextually wrong words ('their'/'there'/'they're')."
        ),
    },
    {
        "slug": "outlining",
        "title": "Outlining",
        "content": (
            "An outline is a roadmap that prevents structural problems before they occur. "
            "A formal outline lists the thesis, each body paragraph's main claim (topic "
            "sentence), and the key evidence for each claim. A less formal 'mind-map' "
            "brainstorm can serve the same purpose. Writing from an outline dramatically "
            "reduces the time spent reorganising later. Outlines can also be adjusted "
            "mid-draft if the argument evolves—treat them as living documents."
        ),
    },
    {
        "slug": "research-integration",
        "title": "Research Integration",
        "content": (
            "Integrating research means weaving sources into your own argument rather than "
            "stringing quotations together. The writer's voice should dominate; sources "
            "serve as supporting evidence, not the primary content. Techniques include: "
            "using only the most relevant portions of a source, paraphrasing to show "
            "comprehension, synthesising multiple sources to show scholarly conversation, "
            "and always explaining why each piece of evidence matters to your argument."
        ),
    },
]


def index_technique(base_url: str, slug: str, title: str, content: str) -> None:
    payload = json.dumps({
        "document_id": f"technique-{slug}",
        "content": content,
        "metadata": {"type": "technique", "title": title},
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/api/v1/retrieval/index",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    print(f"  [{result.get('status', 'ok')}] technique-{slug}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed writing technique knowledge base")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API gateway base URL")
    args = parser.parse_args()

    print(f"Seeding {len(TECHNIQUES)} techniques → {args.base_url}")
    errors = 0
    for t in TECHNIQUES:
        try:
            index_technique(args.base_url, t["slug"], t["title"], t["content"])
        except Exception as exc:
            print(f"  [ERROR] technique-{t['slug']}: {exc}", file=sys.stderr)
            errors += 1

    if errors:
        print(f"\nCompleted with {errors} error(s).", file=sys.stderr)
        sys.exit(1)
    print("\nAll techniques seeded successfully.")


if __name__ == "__main__":
    main()
