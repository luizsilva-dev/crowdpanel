# Scenario and personas files

Both are YAML. See `examples/coffee-subscription/` for a complete pair.

## Scenario

```yaml
title: Ridgeway Roasters subscription launch     # required
language: English                                 # language of posts, survey answers and report

brand:                                            # the company's official account
  name: Ridgeway Roasters                         # author of the pinned announcement
  replies: facts                                  # facts (default) | free | silent
  active_hours: 9-18                              # when it checks the forum (default 9-18)
  activity: 0.6                                   # chance of checking in an active hour (default 0.7)
  facts: |                                        # what it may say beyond the offers
    Beans ship within 3 days of roasting and the roast date is printed on every bag.
    Bags are 12 oz only for now; no larger sizes yet.

platform: >                                       # required: where the conversation happens
  A public online forum for home coffee enthusiasts in the United States...

context: |                                        # required: what people here already know
  Prices, competitors, the company's history, common complaints...

announcement: |                                   # optional: opening text of the pinned post
  We are finally launching a subscription...

offers:                                           # required: one or more
  - id: a                                         # defaults to a, b, c...
    name: Monthly plan
    description: |
      $22.90 per 12 oz bag...
  - id: b
    name: Prepaid year
    description: |
      $219 paid upfront for 12 bags...

events:                                           # optional: news dropped during the run
  - day: 2
    hour: 11
    author: Coffee Industry Daily                 # defaults to brand_name
    content: |
      A national coffee chain announced...

questions:                                        # optional: replaces the default survey
  - id: preferred_offer
    text: Which option would you pick?
    type: choice                                  # text | choice | scale | number
    options: [Monthly plan, Prepaid year, None of them]
  - id: purchase_intent
    text: From 0 to 10, how likely are you to sign up?
    type: scale
    min: 0
    max: 10
  - id: main_objection
    text: What is the main thing holding you back?
```

### The company account

The company takes part in the discussion with its own account, the way a founder would answer in a forum:

- `facts` answers only from the published offers and `brand.facts`. Anything else gets an honest "not defined yet". Nothing is invented on your behalf, so write down the answers you already have.
- `free` lets it improvise like a founder: decide details, make concessions, promise things. Useful to explore what customers would ask for, but it speaks for you without your approval. The report lists everything it said so you can review it.
- `silent` never replies. Personas are told nobody from the company answers there. Expect "nobody answered" to become an objection.

The company account is not surveyed and does not count in the results. `brand_name` at the top level still works as a shortcut for `brand.name`.

Tips:

- The `context` is what everybody in the community knows. Put market prices and competitors there: personas compare against them.
- Write offers the way you would publish them. Personas react to the fine print.
- `events` are good for stress tests: a competitor's price cut, a bad review, a news story.
- When a run shows only some offers (`--offer a`), the pinned post shows only those.

### Default survey

When `questions` is empty, every persona answers:

- `preferred_offer` (with two or more offers) or `would_buy` (with one)
- `purchase_intent`, 0 to 10
- `willingness_to_pay`
- `main_objection`
- `what_would_change_your_mind`
- `best_part`

Choice and scale answers are tallied overall and per segment in the report.

## Personas

```yaml
personas:
  - name: Priya Patel                 # required, unique
    profile: >                        # required: background that matters for the offer
      Cancelled three coffee subscriptions in the last two years...
    segment: subscription skeptic     # used to group results (default: general)
    age: 33
    location: Jersey City, New Jersey
    occupation: Product manager
    personality: Analytical, sarcastic, reads the fine print.
    activity: 0.6                     # chance of being online in an active hour, 0 to 1 (default 0.5)
    active_hours: 19-23               # or a list like [7, 8, 12, 20]; default 8-22
```

Tips:

- Concrete beats generic. "Spends about $90 a month on beans and owns a Niche grinder" gives the model far more to work with than "coffee lover".
- Give some personas bad past experiences, tight budgets or no interest at all. A crowd of fans tells you nothing.
- `segment` is how the report groups answers, so keep the labels consistent.
- `activity` and `active_hours` decide how often each persona shows up. Busy people should not post every hour.
- 10 to 30 personas is a good range. More personas cost more calls per round.

`crowdpanel personas` writes this same format, so you can generate a draft and edit it.
