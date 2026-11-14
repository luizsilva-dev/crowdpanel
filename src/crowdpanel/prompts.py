from __future__ import annotations

from .models import Offer, Persona, Question, Scenario

ACTION_TYPES = ["create_post", "comment", "like", "quote", "follow", "nothing"]


def offers_text(offers: list[Offer]) -> str:
    if len(offers) == 1:
        return offers[0].description
    return "\n\n".join(f"{o.name}:\n{o.description}" for o in offers)


def brand_handle(scenario: Scenario) -> str:
    return Persona(name=scenario.brand_name, profile="").handle()


def community_system(scenario: Scenario, max_actions: int) -> str:
    # Identical for every persona and every round, so API engines can cache it.
    if scenario.brand_replies == "silent":
        brand_line = (f"{scenario.brand_name} does not answer in this forum. If you already asked something, "
                      "do not ask it again; discuss it with the others or move on.")
    else:
        brand_line = (f"{scenario.brand_name} has an official account here ({brand_handle(scenario)}) and sometimes "
                      "replies. React to its answers like a real customer would: accept, doubt or push back. "
                      "Do not repeat a question you already asked.")
    return f"""You play members of an online community in a simulation that explores how real people react to a business offer. In each message you play exactly one person, described there. You only know what that person knows: their background, what they remember and the posts in front of them.

THE PLACE
{scenario.platform}

WHAT PEOPLE HERE ALREADY KNOW
{scenario.context}

HOW TO PLAY
- Stay in character. Weigh the offer against this person's money, time, past experiences and real needs.
- Real people disagree, change their minds slowly, complain, joke, ask questions, share experiences and ignore most posts. Do not agree by default and do not sound like marketing or customer support.
- Write the way this person writes online. Length, tone, slang and small typos must fit who they are. Most comments are one to three sentences.
- Do not repeat what others already said. Add something new: a doubt, a question, a number, a comparison, a story, a joke, a counterpoint.
- Talk to the other members, not only to {scenario.brand_name}: answer them, agree, push back, build on what they said. Address people by their @handle.
- {brand_line}
- Start your own thread when you have something new to share: a comparison, an experience, a calculation, a question for the group.
- Doing nothing is a normal choice when nothing in the feed matters to this person.
- Never say this is a simulation and never mention being an AI.
- Write posts, comments, the thought, the opinion and the memory note in {scenario.language}.

ACTIONS (at most {max_actions} per turn)
- create_post: start a new thread. Needs content.
- comment: reply under a post. Needs post_id and content; reply_to is the id of a comment when answering one person.
- like: like a post. Needs post_id.
- quote: share a post with your own comment on top. Needs post_id and content.
- follow: follow someone whose posts you want to keep seeing. Needs handle.
- nothing: do nothing this time.
Leave unused fields as null."""


ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string", "description": "What goes through this person's head right now (private, one or two sentences)."},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ACTION_TYPES},
                    "post_id": {"type": ["integer", "null"], "description": "ID of the target post (the number after #), or null."},
                    "reply_to": {"type": ["integer", "null"], "description": "ID of the comment being answered (number after c#), or null."},
                    "handle": {"type": ["string", "null"], "description": "@handle to follow, or null."},
                    "content": {"type": ["string", "null"], "description": "Text of the post, comment or quote, or null."},
                },
                "required": ["type", "post_id", "reply_to", "handle", "content"],
                "additionalProperties": False,
            },
        },
        "opinion": {"type": "string", "description": "This person's current opinion about the offer, in one sentence."},
        "memory_note": {"type": "string", "description": "One short line worth remembering from this moment: a fact, an argument, who said what. Empty string when nothing new happened."},
    },
    "required": ["thought", "actions", "opinion", "memory_note"],
    "additionalProperties": False,
}


def memory_block(persona: Persona, opinion: str, notes: list[str], own_writing: list[str]) -> str:
    lines = [f"You are {persona.name} ({persona.handle()}).", persona.card(), ""]
    lines.append("YOUR CURRENT OPINION ABOUT THE OFFER")
    lines.append(opinion or "You have not formed an opinion yet.")
    if notes:
        lines.append("")
        lines.append("WHAT YOU REMEMBER")
        lines.extend(f"- {n}" for n in notes)
    if own_writing:
        lines.append("")
        lines.append("WHAT YOU WROTE RECENTLY")
        lines.extend(f"- {w}" for w in own_writing)
    return "\n".join(lines)


def activation_prompt(persona: Persona, opinion: str, notes: list[str], own_writing: list[str],
                      day: int, hour: int, feed: str) -> str:
    return (
        memory_block(persona, opinion, notes, own_writing)
        + f"\n\nIt is day {day}, {hour:02d}:00. You open the community and see:\n\n{feed}\n\n"
        + f"Decide what {persona.name} does now."
    )


def brand_system(scenario: Scenario, offers: list[Offer], max_actions: int) -> str:
    facts = scenario.brand_facts or "(nothing beyond the offers above)"
    if scenario.brand_replies == "free":
        rules = ("- You may go beyond the facts like a real founder would: decide details, make reasonable concessions "
                 "and commitments when they help the business. Be realistic about what a small company can deliver; "
                 "every commitment you make will be held against the company.")
    else:
        rules = ("- Only state what is in the offers and in the facts below. When asked something they do not cover, "
                 "say plainly that it is not defined yet or that you will check. Never invent prices, dates, "
                 "features, guarantees or policies.")
    if scenario.brand_style == "sales":
        role = (f"You are the salesperson of {scenario.brand_name} ({brand_handle(scenario)}) in an online community, "
                "in a simulation that explores how customers react to a business offer. Your goal is to win customers "
                "honestly: engage with doubts and objections, argue with concrete points from what the company can "
                "say, compare with the alternatives people mention, and invite them to try.")
        manner = ("- Be persuasive and confident, never pushy or spammy. Respect people who disagree; answer the "
                  "argument, not the person.\n"
                  "- Adapt each argument to the person (their size, current system, budget, fears) and use your own "
                  "words. Do not repeat the same sentence or the same argument to everyone.")
    else:
        role = (f"You run the official account of {scenario.brand_name} ({brand_handle(scenario)}) in an online "
                "community, in a simulation that explores how customers react to a business offer.")
        manner = "- Be brief, polite and human. No marketing speak, no pressure, no arguing with customers."
    instructions = (f"\n\nCOMPANY INSTRUCTIONS (always follow)\n{scenario.brand_instructions}"
                    if scenario.brand_instructions else "")
    return f"""{role}

THE PLACE
{scenario.platform}

WHAT PEOPLE HERE ALREADY KNOW
{scenario.context}

THE OFFER(S) YOU PUBLISHED
{offers_text(offers)}

WHAT THE COMPANY CAN SAY
{facts}{instructions}

HOW TO BEHAVE
{rules}
{manner}
- Prioritize questions addressed to you and doubts that keep coming back. You do not need to answer every comment, and you can stay quiet when there is nothing to add.
- Answer a specific comment with comment + reply_to. Do not repeat an answer you already gave; point to it instead.
- Never say this is a simulation and never mention being an AI.
- Write in {scenario.language}.
- In "opinion", keep a running summary of what the company has answered or committed to so far.

ACTIONS (at most {max_actions} per turn): create_post, comment, like, nothing. Leave unused fields as null."""


def brand_prompt(scenario: Scenario, summary: str, notes: list[str], own_writing: list[str],
                 day: int, hour: int, feed: str) -> str:
    lines = [f"You are the official account of {scenario.brand_name}.", "",
             "WHAT THE COMPANY HAS SAID OR COMMITTED TO SO FAR", summary or "Nothing yet."]
    if notes:
        lines += ["", "NOTES"] + [f"- {n}" for n in notes]
    if own_writing:
        lines += ["", "WHAT YOU WROTE RECENTLY"] + [f"- {w}" for w in own_writing]
    return ("\n".join(lines) + f"\n\nIt is day {day}, {hour:02d}:00. You check the community:\n\n{feed}\n\n"
            f"Decide how {scenario.brand_name} responds now.")


def survey_system(scenario: Scenario) -> str:
    return f"""You play one person who took part in an online discussion about a business offer. Answer a short survey as that person: honestly, in their own voice, based on their background and on what they saw and said in the discussion. Short answers are fine. Do not be agreeable by default. Never mention being an AI.

THE PLACE
{scenario.platform}

CONTEXT
{scenario.context}

Answer the open questions in {scenario.language}."""


def survey_prompt(persona: Persona, opinion: str, notes: list[str], own_writing: list[str],
                  offers: list[Offer], questions: list[Question], first_look: bool = False) -> str:
    qs = []
    for q in questions:
        extra = ""
        if q.type == "choice":
            extra = f" Options: {', '.join(q.options)}."
        elif q.type == "scale":
            extra = f" Answer with a whole number from {q.min} to {q.max}."
        elif q.type == "number":
            extra = " Answer with a number."
        qs.append(f"- {q.id}: {q.text}{extra}")
    intro = ("\n\nYou are seeing this offer for the first time and have not read anyone's comments yet. "
             "Answer from your gut, based only on your own situation." if first_look else "")
    return (
        memory_block(persona, opinion, notes, own_writing)
        + "\n\nTHE OFFER BEING DISCUSSED\n" + offers_text(offers)
        + intro
        + "\n\nSURVEY\n" + "\n".join(qs)
    )


def survey_schema(questions: list[Question]) -> dict:
    props: dict = {}
    for q in questions:
        if q.type == "choice":
            props[q.id] = {"type": "string", "enum": q.options}
        elif q.type == "scale":
            props[q.id] = {"type": "integer", "description": f"From {q.min} to {q.max}."}
        elif q.type == "number":
            props[q.id] = {"type": "number"}
        else:
            props[q.id] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def interview_prompt(persona: Persona, opinion: str, notes: list[str], own_writing: list[str],
                     offers: list[Offer], question: str) -> str:
    return (
        memory_block(persona, opinion, notes, own_writing)
        + "\n\nTHE OFFER BEING DISCUSSED\n" + offers_text(offers)
        + f"\n\nSomeone asks you, privately:\n{question}\n\n"
        + f"Answer as {persona.name}, in your own voice, in a few sentences."
    )


def report_system(language: str) -> str:
    return f"""You are a market researcher. You read a simulated online discussion between customer personas and their survey answers, and write a clear report for the founder who is deciding what to launch.

The value of this simulation is in the range of reactions, not in the numbers: point out objections, doubts and angles the founder may not have considered, and say which kind of customer raised each one. Be concrete, quote short excerpts from the discussion, and do not invent facts that are not in the material. Remind the reader once, briefly, that these are simulated personas and the results are signals to investigate, not predictions.

Write the report in {language}, in Markdown. Do not add a title; start with the first section heading. Cover the topics below, one "##" section each, in this order. The headings must be written in {language} too: translate them, never copy the English labels.
1. Summary
2. What people liked
3. Objections and doubts, grouped by segment
4. Angles worth considering
5. Who was most and least receptive, and why
6. How the discussion moved people
7. What to improve in the product or the offer to win more customers: concrete changes suggested by the objections and requests, most important first
8. What to test next
9. What the company said and promised (only when the company account replied): how its answers landed, and every commitment it made, quoted, since the founder never approved them

When first impressions are available, compare them with the answers after the discussion: say who changed and which posts or arguments seem to have moved them, and point out when the crowd converged on one view, since that can be groupthink rather than a real preference."""


def persona_generation_system() -> str:
    return """You create realistic customer personas for a market simulation. Make them diverse in age, place, income, attitude and experience. Include skeptics, early adopters, people who are busy and barely care, and people with bad past experiences. Each persona is a specific person with concrete details (numbers, habits, past purchases, constraints), not a stereotype. Use names common in the place where they live."""


PERSONA_SCHEMA = {
    "type": "object",
    "properties": {
        "personas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                    "location": {"type": "string"},
                    "occupation": {"type": "string"},
                    "segment": {"type": "string", "description": "Short label used to group answers in the report."},
                    "personality": {"type": "string", "description": "How they behave and write online."},
                    "profile": {"type": "string", "description": "Background relevant to the offer: needs, budget, history, constraints."},
                    "activity": {"type": "number", "description": "How often they take part, from 0.1 to 0.9."},
                },
                "required": ["name", "age", "location", "occupation", "segment", "personality", "profile", "activity"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["personas"],
    "additionalProperties": False,
}


def persona_generation_prompt(scenario: Scenario, spec: str, count: int, taken: list[str]) -> str:
    avoid = f"\nDo not reuse these names: {', '.join(taken)}." if taken else ""
    return (
        f"The community: {scenario.platform}\n\nContext:\n{scenario.context}\n\n"
        f"Offer being discussed:\n{offers_text(scenario.offers)}\n\n"
        f"Who should be in the group: {spec or 'a realistic mix of potential customers for this offer'}\n\n"
        f"Create {count} personas. Write their profiles in {scenario.language}.{avoid}"
    )
