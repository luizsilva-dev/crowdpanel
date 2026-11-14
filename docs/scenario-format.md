# Scenario and personas files

Both are YAML. See `examples/coffee-subscription/` for a complete pair.

## Scenario

```yaml
title: Ridgeway Roasters subscription launch     # required
brand_name: Ridgeway Roasters                     # author of the pinned announcement
language: English                                 # language of posts, survey answers and report

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
