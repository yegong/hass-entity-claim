# hass-entity-claim

`hass-entity-claim` is a Home Assistant integration for coordinating independent state requirements from multiple automations or business flows that control the same entity.

## The problem

In a larger Home Assistant setup, the final state of an entity is often influenced by more than one piece of automation logic.

For example, several independent flows may each have a reason to require the same entity to stay on:

```text
Presence logic       → requires ON
Schedule logic       → no requirement
Environmental logic  → requires ON
```

If every automation directly calls `turn_on` and `turn_off`, they can interfere with each other.

One flow may send `turn_off` when its own condition ends even though another flow still requires the entity to remain on.

The real intent is often not:

```text
"Turn this entity on now."
```

but:

```text
"I require this entity to stay on while my condition is active."
```

`hass-entity-claim` models that distinction explicitly.

## The model

Each managed Home Assistant entity has one or more independent **requesters**.

Each requester owns a **claim entity**:

```text
Claim A ─┐
Claim B ─┼──> Internal Aggregator ───> Source Entity
Claim C ─┘
```

A requester can assert or release its own claim without knowing anything about the other requesters.

The integration combines all current claims according to a configured aggregation policy and applies the resulting desired state directly to the original source entity.

## Example

Given a source entity:

```text
fan.some_entity
```

and several requesters:

```text
presence
schedule
environment
```

the integration can expose logical claim entities such as:

```text
fan.some_entity_required_by_presence
fan.some_entity_required_by_schedule
fan.some_entity_required_by_environment
```

Their states describe requirements, not the actual source state.

For example:

```text
fan.some_entity_required_by_presence = on
```

means:

```text
The "presence" requester currently requires
fan.some_entity to be ON.
```

The real source entity may temporarily be in a different state because of availability, latency, or an external action. The claim remains a statement of desired state.

## Aggregation

Each managed source entity has its own aggregation policy.

Typical Boolean policies include:

### ANY

The source is required to be ON when at least one requester is active.

```text
A = ON
B = OFF
C = ON

Result = ON
```

### ALL

The source is required to be ON only when all configured requesters are active.

```text
A = ON
B = ON
C = OFF

Result = OFF
```

This allows business flows to stay independent while the final device state is resolved in one place.

## Source entities remain unchanged

The integration does not replace the original entity with an aggregate or proxy entity.

The source remains the canonical actual entity:

```text
fan.some_entity
```

and continues to behave as the original Home Assistant integration defines it.

`hass-entity-claim` only adds logical claim inputs and an internal aggregation layer.

There is no second "main" entity that users need to choose between.

## Claim entities

Claim entities are logical entities associated with individual requesters.

They are intended primarily as automation interfaces rather than dashboard controls.

They are hidden by default so that they do not automatically clutter dashboards or bridges, while remaining available to automations, scripts, and tools such as Node-RED.

Claim entities follow the source entity's domain where appropriate.

This keeps the model compatible with richer claims in the future.

For example, a fan claim could eventually carry both:

```text
state = ON
percentage = 70
```

when the source fan supports speed control and a clear percentage aggregation policy is configured.

## Why not just use `input_boolean`?

The same pattern can be built manually with helpers:

```text
input_boolean.target_required_by_a
input_boolean.target_required_by_b
input_boolean.target_required_by_c
```

plus an OR/AND automation.

That works, but it creates repetitive configuration:

- every helper must be created manually;
- entity IDs and friendly names must be maintained;
- aggregation logic must be built separately;
- adding or removing requesters requires edits in several places;
- richer domain-specific claims are difficult to express later.

`hass-entity-claim` turns this recurring pattern into a managed Home Assistant abstraction.

## Why not use a single shared automation?

A central automation can combine every condition and directly control the target.

That works for small systems, but it couples otherwise independent business logic.

Entity claims create a clearer boundary:

```text
Business flow:
    "Do I currently require this entity?"

Entity Claim:
    "How do all current requirements combine?"

Source entity:
    "What is the actual controlled state?"
```

Each layer has one responsibility.

## Configuration model

Each source entity is managed independently.

Conceptually, one configuration contains:

```text
Source Entity
Requesters
Aggregation Policy
Optional Diagnostics
```

Requesters can be added, removed, or renamed independently of other managed source entities.

Claim entity IDs and friendly names are derived from the source entity and requester identity so that the resulting Home Assistant model remains predictable.

## Diagnostics

An optional diagnostic sensor can expose how the current result was produced.

For example:

```yaml
target_entity: fan.some_entity

aggregation:
  state: any

claims:
  requester_a:
    state: "on"
  requester_b:
    state: "off"
  requester_c:
    state: "on"

aggregated:
  state: "on"

target:
  actual_state: "on"

in_sync: true
```

The diagnostic entity is for observability only and does not participate in control.

## Design principles

`hass-entity-claim` follows a few core rules:

- A claim is a persistent requirement, not an instantaneous command.
- Each requester owns its own claim.
- Requesters do not need to know about each other.
- Aggregation is explicit and configurable.
- The original source entity remains unchanged and authoritative for actual state.
- Aggregate results are internal rather than represented by another proxy entity.
- Claim entities are predictable, automation-friendly, and hidden by default.
- Richer domain-specific claims should only be introduced when their aggregation semantics are explicit.

## Beyond Boolean state

The same abstraction can extend beyond simple ON/OFF claims.

For example, a fan could use:

```text
state      → ANY
percentage → MAX
```

so that:

```text
Requester A → ON at 30%
Requester B → ON at 70%
Requester C → ON at 40%
```

aggregates to:

```text
ON at 70%
```

Other domains can support richer claims where the aggregation behavior is well-defined.

The core idea remains the same:

> Independent business flows make claims, the integration combines them, and the original entity receives the resulting required state.
