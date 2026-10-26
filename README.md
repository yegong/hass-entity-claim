# hass-entity-claim

`hass-entity-claim` is a Home Assistant custom integration that lets multiple independent automations or business flows declare and maintain requirements for the same entity.

It models more than a one-time command. A claim means: **keep this target in the requested state for as long as my condition remains active.**

```text
Presence Claim ─┐
Schedule Claim ─┼─> Aggregate (ANY = MAX / ALL = MIN) ─> Source Entity
Safety Claim   ─┘
```

The current version supports `switch`, `light`, `fan`, and `cover` Source Entities. Boolean state is the `0..1` form of the same ordered model used for Light brightness (`0..255`), Fan percentage (`0..100`), and Cover position (`0..100`).

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
- Provide one predictable ordered model: `ANY` selects the maximum request and `ALL` selects the minimum request.
- Control the original Source Entity without creating a competing proxy entity.
- Keep requester identities and Entity Registry entries stable.
- Make claims observable, persistent, and accessible to automations and Node-RED.
- Expose only capabilities with explicitly defined aggregation semantics.
- Support a small set of ordered structured claims without turning the integration into a general policy engine.

## Core concepts

### Source Entity

The Source Entity is the existing Home Assistant entity that represents the real device, for example:

```text
fan.example_target
```

It remains the canonical representation of the device's actual state. This integration does not replace, rename, or hide it.

### Requester

A requester represents one independent source of demand, such as motion, a schedule, or an air-quality automation. Each requester has:

- a user-visible name entered during configuration;
- a stable machine ID generated and maintained by the integration.

The machine ID is intentionally not exposed as a separate configuration field.

### Claim Entity

Each requester receives a Claim Entity in the same domain as the Source Entity:

```text
Source:  fan.example_target
Claim:   fan.example_target_required_by_presence
```

The Claim Entity represents only that requester's ordered requirement. Which endpoint is neutral depends on aggregation:

```text
ANY / MAX: minimum is neutral; a higher value is a Claim
ALL / MIN: maximum is neutral; a lower value is a Claim
```

For a structured Claim, the domain attribute is part of that same desired input:

```text
Light Claim = ON @ brightness 160
Fan Claim   = ON @ percentage 70
Cover Claim = position 40
```

A claim is not the device's actual state. A claim can remain `on` while `fan.example_target` is temporarily `off`; the integration will reconcile the source from the stored desired state.

When the Source Entity belongs to a Device, its Claim Entities and optional Diagnostic Sensor are linked to that same Device. They therefore follow the Device's area automatically. If the Source Entity has its own Entity-level area assignment, that area is copied to generated entities that do not already have a user-assigned area.

### Internal Aggregator

The integration combines all Claim states into an internal desired state. It does not create an additional Aggregate Entity by default.

Two aggregation policies are supported across Boolean and ordered values:

| Policy | Ordered result | Boolean `0..1` result |
| --- | --- | --- |
| `ANY` | Highest requested value (`MAX`) | ON when at least one Claim is ON |
| `ALL` | Lowest requested value (`MIN`) | OFF when any Claim is OFF |

When there are no requesters, the aggregate result is always OFF for both policies.

For example:

| Claim | State |
| --- | --- |
| Presence | ON |
| Schedule | OFF |
| Air Quality | ON |

- `ANY` produces ON (`MAX(1, 0, 1) = 1`).
- `ALL` produces OFF (`MIN(1, 0, 1) = 0`).

The same policy applied to brightness requests of `180`, `80`, and `0` produces `180` for `ANY` and `0` for `ALL`.

This makes an ON request the constraining Claim for `ANY`, while an OFF request is the constraining Claim for `ALL`. The opposite endpoint is that policy's no-Claim identity.

### Reconciliation

When the aggregate desired value differs from the Source Entity's actual value, the integration calls the smallest applicable domain action: ON/OFF, Light brightness, Fan percentage, or Cover position.

No action is called when both states already match. If the source is `unknown` or `unavailable`, reconciliation is deferred until its state becomes explicit again.

### Control authority

Each Config Entry selects one control policy:

| Policy | Behavior |
| --- | --- |
| `authoritative` | Continuously reconcile the Source to the aggregate. |
| `override` | Temporarily yield when an external change conflicts with the aggregate. |
| `scoped` | Control and later restore only an effect Entity Claim visibly created. |

With `override`, an explicit external state or supported value change that conflicts with the current aggregated desired value starts a time-limited Manual Override:

```text
Claims → Aggregation → Desired State → Override Gate → Reconcile → Source
```

During the override, Claims continue to update and aggregation continues to calculate the latest desired state. Only reconciliation is suppressed, so `desired != actual` is an expected state while the override is active. A further conflicting external change refreshes the timer.

When the period expires, the integration compares the latest desired and actual states and reconciles only if they still differ. It never restores a snapshot taken when the override began. Changes to or from `unknown` and `unavailable` do not start an override.

Commands issued by Entity Claim carry their own Home Assistant Context. The Controller correlates subsequent Source state changes with that Context so its own reconciliation is not mistaken for an external override. A short-lived expected-state match is retained only as a fallback for device integrations that do not propagate Context.

With `scoped`, a Claim receives control only after its Source command produces an observable, correlated change. Later Claims can join the same control session. If the first requester releases while another participant remains, control passes to that participant without replacing the original baseline. The last participant restores the state captured before the first successful Claim. Any external operation relinquishes the entire session immediately, discards that baseline, and prevents old Claims from restoring or reasserting it. A later Claim establishes a new session only if it actually changes the Source. Session provenance and its original baseline are persisted across reloads and restarts.

Cover movement receives additional protection. `opening` and `closing` are treated as transient progress, not user intent. Position reports are checked against the commanded interval and direction, with a short no-Context guard and a longer pending-movement lifetime. The final explicit position is then compared with the latest aggregate.

## Current scope

The current version supports:

- Source domains: `cover`, `fan`, `light`, and `switch`;
- Boolean ON/OFF claims for binary Sources;
- Light brightness when supported by the Source;
- Fan percentage when supported by the Source;
- Cover open/close and position when supported by the Source;
- ordered `ANY = MAX` and `ALL = MIN` aggregation;
- adding, removing, and renaming requesters;
- restoration of Claim state;
- authoritative, time-limited override, and Claim-scoped control policies;
- an optional Diagnostic Sensor;
- Claim Entities that are hidden by default but remain enabled.

It does not currently aggregate or forward features such as:

- Light color or color temperature;
- Fan presets, direction, or oscillation;
- Cover tilt, speed, or stop;
- Valve claims;
- requester priority;
- retry, backoff, or complex conflict arbitration.

Capabilities are exposed only when both the Source supports them and their Claim aggregation semantics are defined. Cover tilt and Valve claims are intentionally excluded because their demand semantics are not sufficiently clear for this model.

## Installation

1. Copy `custom_components/entity_claim` into your Home Assistant configuration directory:

   ```text
   <config>/custom_components/entity_claim
   ```

2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**.
4. Search for and add **Entity Claim**.

## Configuration

Each Config Entry manages exactly one Source Entity. Configuration first asks for the Source so the second step can show capability-specific aggregation wording. Configure:

- **Source entity**: the `cover`, `fan`, `light`, or `switch` to manage;
- **Requester names**: one item for each independent source of demand;
- **Aggregation policy**: `ANY = MAX` or `ALL = MIN`, described in the UI using state, brightness, speed, or position as appropriate;
- **Control authority**: choose `Always managed`, `Allow manual override`, or `Claim-scoped control`;
- **Manual Override duration**: how long reconciliation is suppressed after the latest conflicting external change when the override policy is selected;
- **Diagnostic sensor**: whether to create the optional diagnostic entity.

Enter the first requester name, then use the **Add** button to create another input. For example, add these three items:

```text
Motion
Automation
Schedule
```

The integration automatically generates and maintains a stable machine ID for each requester. The entered text remains the requester name used in the Claim Entity's Friendly Name. At least one requester is required when creating a configuration.

This configuration produces entities similar to:

```text
fan.example_target_required_by_motion
fan.example_target_required_by_schedule
fan.example_target_required_by_automation
```

### Reconfiguration

Use **Configure** from Home Assistant's Helpers page, or **Reconfigure** on the Config Entry from the integration page, to:

- add a requester;
- remove a requester;
- change a requester's name;
- change the aggregation policy;
- change the control authority policy;
- change the Manual Override duration;
- enable or disable the Diagnostic Sensor.

Renaming a requester does not create a new Claim Entity during normal reconfiguration because the integration keeps its generated machine ID stable.

Removing every requester and submitting the form opens a deletion confirmation instead of saving an empty configuration. Selecting **Remove Entity Claim** deletes the Config Entry and all Claim Entities and diagnostic entities it created. Cancelling or closing the confirmation leaves the existing configuration unchanged. The Source Entity is never deleted.

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

Structured Claim Entities accept only the capability whose aggregation semantics are defined. For example:

```yaml
# Request brightness 160. Under ANY, this competes by MAX.
- action: light.turn_on
  target:
    entity_id: light.example_required_by_reading
  data:
    brightness: 160

# Request 70 percent fan speed.
- action: fan.set_percentage
  target:
    entity_id: fan.example_required_by_air_quality
  data:
    percentage: 70

# Request a 40 percent open Cover position.
- action: cover.set_cover_position
  target:
    entity_id: cover.example_required_by_daylight
  data:
    position: 40
```

Calling `turn_on`, `fan.turn_on`, or `cover.open_cover` without an ordered value asserts the maximum value. Turning off or closing asserts zero. A Claim Entity does not expose Light color, Fan presets, Cover tilt, or other unsupported Source features.

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
target_entity: light.example_target
aggregation:
  state: any
  ordered_value: max
claims:
  presence:
    state: "on"
    brightness: 180
  schedule:
    state: "off"
    brightness: 0
aggregated:
  state: "on"
  brightness: 180
target:
  actual_state: "off"
  brightness: 0
control:
  policy: override
  state: passive
  desired:
    state: "on"
    brightness: 180
  until: "2026-10-08T08:30:00+00:00"
  reason: external_source_change
```

The diagnostic sensor state is one of `active`, `applying`, `passive`, or `restoring`. `authoritative` uses `active` and `applying`; `override` additionally uses `passive`; `scoped` can use all four states. Only scoped control restores a captured baseline.

The entity uses Home Assistant's diagnostic category. Creation is disabled by default; enable it in the Config Entry when needed.

## Entity naming and stability

- A Claim's `unique_id` is derived from the Config Entry and requester ID.
- The requester name affects only the Friendly Name.
- Changing the Source Entity's Friendly Name does not create a new Claim Entity.
- A Claim Entity ID customized by the user is not forced back to the suggested ID.
- Removing a requester removes its Claim Entity Registry entry.
- A newly added requester starts at the aggregation identity: the minimum value for `ANY`, or the maximum value for `ALL`. Existing requesters restore their saved Claim states.

## Project structure

```text
custom_components/entity_claim/
├── __init__.py          # Config Entry setup, unload, and reconfiguration
├── config_flow.py       # Initial configuration and editing flows
├── capability.py        # Source capability discovery and value profiles
├── const.py             # Domain, platform, and configuration constants
├── controller.py        # Authority policy, correlation, sessions, and reconciliation
├── cover.py             # cover domain adapter
├── entity.py            # Shared Claim Entity base class
├── fan.py               # fan domain adapter
├── light.py             # light domain adapter
├── switch.py            # switch domain adapter
├── sensor.py            # Optional Diagnostic Sensor
├── model.py             # HA-independent aggregation and reconciliation model
├── schema.py            # Requester parsing, validation, and naming rules
├── registry.py          # Entity Registry conflict, area, and lifecycle handling
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
model.py calculates the ordered MAX / MIN aggregate
    ↓
Controller applies the configured authority policy
    ↓
Controller calls the applicable state / value action on the Source Entity
```

`cover.py`, `fan.py`, `light.py`, and `switch.py` are thin Home Assistant domain adapters. Capability discovery, aggregation, movement correlation, and reconciliation remain centralized instead of being copied across platforms.

## Local testing

The core test suite does not require a complete Home Assistant installation. Run it directly with the Python standard library:

```bash
python -m unittest discover -s tests -v
```

You can also verify that all Python modules compile:

```bash
python -m compileall -q custom_components tests
```

These tests cover Boolean and ordered aggregation, tolerance rules, Cover movement intervals, reconciliation decisions, requester parsing, naming rules, and integration metadata. Home Assistant lifecycle behavior, Entity Registry interaction, and service calls should still receive final validation in a real or dedicated Home Assistant instance.

## Roadmap

Roadmap items describe design directions and open questions. They are not promises that the capability is already available or will be implemented in a particular release.

### Automation acknowledgment of Manual Override

A future extension may let an automation receive a semantic notification after Entity Claim classifies a conflicting external Source change. The automation could update its own business state or Claims and then explicitly acknowledge that the external intent has been incorporated, allowing the Manual Override to end before its configured timeout.

This remains in use-case collection. In particular, the design still needs to establish:

- whether notification should use a Home Assistant event or another interface;
- the distinction between acknowledging an absorbed change and forcibly clearing an override to resume reconciliation;
- how an acknowledgment proves that the latest aggregated desired state matches the latest actual state;
- how correlation prevents a delayed acknowledgment from clearing a newer override;
- how races, reloads, unavailable states, and future structured claims affect the interaction.

No override event or acknowledgment action is currently provided. Manual Override continues to end through its configured timeout, followed by reconciliation against the latest desired and actual states.

## Design boundary

The project intentionally keeps one small abstraction clear:

```text
Independent Claims → Aggregation → Desired State
                                      ↓
                               Authority Policy
                                      ↓
                                  Reconcile
                                      ↓
                                Source Actual State
```

A Claim is a desired input, aggregation computes only the current desired state, the authority policy controls whether reconciliation is allowed and whether an effect may later be restored, and the Source Entity remains the canonical actual state. These roles remain separate, and no additional Aggregate Entity competes with the Source Entity.
