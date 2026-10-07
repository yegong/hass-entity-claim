# hass-entity-claim

`hass-entity-claim` is a Home Assistant custom integration that lets multiple independent automations or business flows declare and maintain requirements for the same entity.

It models more than a one-time command. A claim means: **keep this target in the requested state for as long as my condition remains active.**

```text
Presence Claim ─┐
Schedule Claim ─┼─> Aggregate (ANY / ALL) ─> Source Entity
Safety Claim   ─┘
```

The current version supports Boolean ON/OFF claims for `fan`, `light`, and `switch` entities.

## The problem

Suppose two automations control `fan.example_target`:

- a presence automation requires the fan to stay on while someone is in the room;
- a schedule automation requires the fan to stay on during a configured period.

If both automations directly call `fan.turn_on` and `fan.turn_off`, they can overwrite each other's intent:

```text
Presence still requires ON
The schedule ends and sends OFF
Result: the valid presence requirement is lost
```

A normal service call expresses an immediate command. It does not record who still holds an active requirement.

This integration creates a separate Claim Entity for each requester:

```text
fan.example_target_required_by_presence
fan.example_target_required_by_schedule
```

Each business flow manages only its own claim. The integration stores those claims, aggregates them, and controls the original Source Entity. Releasing one claim does not overwrite another requester's active requirement.

## Goals

- Keep automations decoupled so each one manages only its own requirement.
- Clearly separate desired Claim state from actual Source Entity state.
- Provide simple and predictable `ANY` and `ALL` aggregation policies.
- Control the original Source Entity without creating a competing proxy entity.
- Keep requester identities and Entity Registry entries stable.
- Make claims observable, persistent, and accessible to automations and Node-RED.
- Expose only capabilities with explicitly defined aggregation semantics.
- Leave a clear path for structured claims without over-designing the initial version.

## Core concepts

### Source Entity

The Source Entity is the existing Home Assistant entity that represents the real device, for example:

```text
fan.example_target
```

It remains the canonical representation of the device's actual state. This integration does not replace, rename, or hide it.

### Requester

A requester represents one independent source of demand, such as `presence`, `schedule`, or `air_quality`. Each requester has:

- `id`: a stable machine identifier used as part of its Entity Registry identity;
- `name`: a user-visible name that can be changed safely.

### Claim Entity

Each requester receives a Claim Entity in the same domain as the Source Entity:

```text
Source:  fan.example_target
Claim:   fan.example_target_required_by_presence
```

The Claim Entity state represents only that requester's requirement:

```text
Claim = ON   The requester currently requires the Source Entity to be on
Claim = OFF  The requester currently has no requirement for it to be on
```

A claim is not the device's actual state. A claim can remain `on` while `fan.example_target` is temporarily `off`; the integration will reconcile the source from the stored desired state.

### Internal Aggregator

The integration combines all Claim states into an internal desired state. It does not create an additional Aggregate Entity by default.

Two aggregation policies are supported:

| Policy | Result is ON when |
| --- | --- |
| `ANY` | At least one Claim is ON |
| `ALL` | Every Claim is ON |

When there are no requesters, the aggregate result is always OFF for both policies.

For example:

| Claim | State |
| --- | --- |
| Presence | ON |
| Schedule | OFF |
| Air Quality | ON |

- `ANY` produces ON.
- `ALL` produces OFF.

### Reconciliation

When the aggregate desired state differs from the Source Entity's actual state, the integration calls the domain's `turn_on` or `turn_off` action to bring the source back to the desired state.

No action is called when both states already match. If the source is `unknown` or `unavailable`, reconciliation is deferred until its state becomes explicit again.

## Current scope

The current version supports:

- Source domains: `fan`, `light`, and `switch`;
- Boolean ON/OFF claims;
- `ANY` and `ALL` aggregation;
- adding, removing, and renaming requesters;
- restoration of Claim state;
- an optional Diagnostic Sensor;
- Claim Entities that are hidden by default but remain enabled.

It does not currently aggregate or forward structured features such as:

- fan percentage;
- light brightness, color, or color temperature;
- requester priority, leases, or manual overrides;
- retry, backoff, or complex conflict arbitration.

Such capabilities should be added only after their aggregation semantics are clearly defined. A feature supported by the Source Entity is not automatically exposed by its Claim Entities.

## Installation

1. Copy `custom_components/entity_claim` into your Home Assistant configuration directory:

   ```text
   <config>/custom_components/entity_claim
   ```

2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**.
4. Search for and add **Entity Claim**.

## Configuration

Each Config Entry manages exactly one Source Entity. When adding the integration, configure:

- **Source entity**: the `fan`, `light`, or `switch` to manage;
- **Requesters**: one requester per line in `id: Name` format;
- **Aggregation policy**: `ANY` or `ALL`;
- **Diagnostic sensor**: whether to create the optional diagnostic entity.

Example requester input:

```text
presence: Presence
schedule: Schedule
air_quality: Air Quality
```

Requester IDs must be stable, lowercase machine identifiers. An ID must start with a lowercase letter and may then contain lowercase letters, digits, and underscores. Do not use a display name that is likely to change as the ID.

This configuration produces entities similar to:

```text
fan.example_target_required_by_presence
fan.example_target_required_by_schedule
fan.example_target_required_by_air_quality
```

### Reconfiguration

Use **Reconfigure** on the Config Entry from Home Assistant's integration page to:

- add a requester;
- remove a requester;
- change a requester's name;
- change the aggregation policy;
- enable or disable the Diagnostic Sensor.

Renaming a requester does not create a new Claim Entity because its Registry identity depends on the stable requester ID, not its display name.

The Source Entity is fixed during reconfiguration. Create another Config Entry to manage a different source. If the original Source Entity's `entity_id` is changed, remove and recreate its Entity Claim configuration.

### Entity ID conflicts

Claim Entity IDs are intended to be predictable interfaces for automations and Node-RED. If a proposed Entity ID is already occupied by another Registry Entity, configuration or reconfiguration fails with an explicit error instead of silently creating an `_2` or `_3` suffix.

Rename or remove the conflicting entity, then try again.

## Usage

Use `turn_on` and `turn_off` actions on a Claim Entity just as you would for a normal entity in that domain.

The following automation holds the Presence claim while occupancy is detected:

```yaml
alias: Example target - assert presence claim
triggers:
  - trigger: state
    entity_id: binary_sensor.example_condition
    to: "on"
actions:
  - action: fan.turn_on
    target:
      entity_id: fan.example_target_required_by_presence
```

Release the same claim when the condition ends:

```yaml
alias: Example target - release presence claim
triggers:
  - trigger: state
    entity_id: binary_sensor.example_condition
    to: "off"
actions:
  - action: fan.turn_off
    target:
      entity_id: fan.example_target_required_by_presence
```

Here, `turn_off` only means that Presence no longer requires the fan to be on. It does not directly order `fan.example_target` to turn off. If the Schedule claim is still ON and the policy is `ANY`, the fan remains on.

For `light` and `switch` claims, use their matching domain actions: `light.turn_on` / `light.turn_off` or `switch.turn_on` / `switch.turn_off`.

Node-RED and other tools that call Home Assistant actions follow the same model: each business flow controls its own Claim Entity instead of directly turning the Source Entity off when that flow's condition ends.

## Hidden Claim Entities

Claim Entities are created with `hidden_by = integration` so they do not automatically clutter dashboards, HomeKit Bridge, or other user-facing entity lists.

Hidden does not mean disabled. A Claim Entity still:

- exists in the Home Assistant state machine;
- can be used by automations, scripts, and Node-RED;
- accepts the domain's `turn_on` and `turn_off` actions.

Enable **Show hidden entities** in the entity list when you need to inspect them. You may also unhide a Claim Entity manually; the integration will not overwrite that user choice.

## Diagnostic Sensor

When enabled, each Config Entry creates at most one Diagnostic Sensor. It does not participate in control and exists only to explain the current aggregation state.

Its data includes information similar to:

```yaml
target_entity: fan.example_target
aggregation:
  state: any
claims:
  presence:
    state: "on"
  schedule:
    state: "off"
aggregated:
  state: "on"
target:
  actual_state: "on"
in_sync: true
```

The entity uses Home Assistant's diagnostic category and is disabled by default. Enable it from the entity page when needed.

## Entity naming and stability

- A Claim's `unique_id` is derived from the Config Entry and requester ID.
- The requester name affects only the Friendly Name.
- Changing the Source Entity's Friendly Name does not create a new Claim Entity.
- A Claim Entity ID customized by the user is not forced back to the suggested ID.
- Removing a requester removes its Claim Entity Registry entry.
- A newly added requester starts at OFF; existing requesters restore their saved Claim states.

## Project structure

```text
custom_components/entity_claim/
├── __init__.py          # Config Entry setup, unload, and reconfiguration
├── config_flow.py       # Initial configuration and Reconfigure UI
├── const.py             # Domain, platform, and configuration constants
├── controller.py        # Claim state, aggregation, listeners, and reconciliation
├── entity.py            # Shared Claim Entity base class
├── fan.py               # fan domain adapter
├── light.py             # light domain adapter
├── switch.py            # switch domain adapter
├── sensor.py            # Optional Diagnostic Sensor
├── model.py             # HA-independent aggregation and reconciliation model
├── schema.py            # Requester parsing, validation, and naming rules
├── registry.py          # Entity Registry conflict and lifecycle handling
├── manifest.json        # Integration metadata
├── strings.json         # Base configuration UI strings
└── translations/        # English and Simplified Chinese translations

tests/
├── test_model.py        # Pure aggregation and reconciliation tests
├── test_schema.py       # Configuration parsing and naming tests
└── test_metadata.py     # Manifest and translation resource tests
```

The core control path is:

```text
Config Entry
    ↓
Controller stores each requester's Claim
    ↓
model.py calculates the aggregate and reconciliation decision
    ↓
Controller calls turn_on / turn_off on the Source Entity
```

`fan.py`, `light.py`, and `switch.py` are thin Home Assistant domain adapters. Aggregation behavior is centralized in the shared model and controller so it is not duplicated across platforms.

## Local testing

The core test suite does not require a complete Home Assistant installation. Run it directly with the Python standard library:

```bash
python -m unittest discover -s tests -v
```

You can also verify that all Python modules compile:

```bash
python -m compileall -q custom_components tests
```

These tests cover the pure aggregation model, reconciliation decisions, requester parsing, naming rules, and integration metadata. Home Assistant lifecycle behavior, Entity Registry interaction, and service calls should still receive final validation in a real or dedicated Home Assistant instance.

## Design boundary

The project intentionally keeps one small abstraction clear:

```text
Independent Claims
        ↓
Explicit aggregation
        ↓
One Source Entity
```

A Claim is a desired input, the Source Entity is the actual state, and the aggregate is an internal desired state. These roles remain separate, and no additional Aggregate Entity competes with the Source Entity as the canonical representation of the device.
