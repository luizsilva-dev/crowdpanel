# How it works

## The run

1. **Setup.** The scenario and personas are saved in SQLite with the engine, model, seed and limits. The brand's announcement with the offer becomes a pinned post.
2. **First impressions.** Before anyone posts, each persona answers the survey alone, seeing only the offer and its own situation. Skip it with `--no-baseline`.
3. **Rounds.** Each round is one simulated hour, from `--hours` start to end, for `--days` days. In each round:
   - scheduled `events` for that hour are posted as news;
   - each persona whose `active_hours` include the hour wakes up with probability `activity` (seeded, so a resumed run picks the same people);
   - every awake persona gets its feed, built before anyone acts, so people online at the same time see the same state;
   - all awake personas are called in parallel, each in a separate call that only contains its own profile, memory and feed;
   - their actions are checked and applied in random order;
   - the round is committed. Interrupting a run loses at most the current round.
4. **Survey.** Each persona answers the same survey again, in character, from its profile, final opinion, memory and what it wrote.
5. **Report.** Numbers are tallied in code (choices and averages per segment, activity per persona). One model call reads the busiest threads, the final opinions and the survey and writes the narrative part.

## Influence and groupthink

Personas read each other, so the discussion changes their minds, as it does with real people. That influence is part of what you want to see, but a model can also make a crowd agree too easily. Two things help you tell the difference:

- Each persona decides in its own call and only sees what others wrote in public, never their private thoughts.
- The first-impression survey is taken before the discussion, by each persona alone. The report puts it next to the final survey, lists who changed which answer, and points out when the crowd converged on one view.

If almost everyone ends up agreeing, compare with the first impressions before trusting it, and try another seed or more skeptical personas.

## The feed

Each persona sees the pinned offer with its latest replies, plus up to `--feed-size` posts (5 by default) picked by:

- not seen yet, or new comments since the last visit;
- posts by people it follows;
- news events;
- engagement (likes and comments);
- recency, with a little randomness.

Posts show the last few comments, so personas can answer each other. A persona never sees its own posts in the feed, but its memory includes what it wrote recently.

## Memory

Each persona keeps:

- its current opinion of the offer, rewritten by the persona at every turn;
- the last 8 one-line notes it chose to remember;
- the last 3 things it wrote.

This keeps every call about the same size no matter how long the simulation runs, which is what keeps the cost linear.

## Actions

| Action | Effect |
|---|---|
| `create_post` | New thread |
| `comment` | Comment on a post, optionally replying to a specific comment |
| `like` | Like a post (once) |
| `quote` | Share a post with a comment on top |
| `follow` | Follow a persona; their posts rank higher in your feed |
| `nothing` | Skip this turn |

Actions that point to posts or people that do not exist are dropped. At most `--max-actions` per turn (3 by default).

## Failures and limits

- Every model call is logged with tokens, time, cost (when known) and error.
- Failed calls are retried twice with a pause. A persona that still fails just misses that turn; if more than half of a round fails, the run stops as failed with the last error.
- Errors that retrying cannot fix (not logged in, invalid key, unknown model) stop the run right away.
- `--max-calls` and `--max-cost` are checked before every round; when reached, the run pauses and `resume` continues from the same round.

## Interviews

`crowdpanel ask` sends a question to one or more personas with their profile, memory, final opinion and recent writing. Answers are stored and shown in the web interface. It works on finished and paused runs.
