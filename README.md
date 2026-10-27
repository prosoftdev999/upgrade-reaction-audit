# Upgrade Reaction Audit

A browser-runtime reconstruction project focused on Custom Elements upgrade reactions and lifecycle callback ordering.

Custom Elements can exist in a document before their definitions are registered. When the browser later receives the corresponding `customElements.define()` call, those existing elements may be upgraded and begin executing their constructors and lifecycle callbacks.

Upgrade Reaction Audit examines that process as historical runtime state rather than treating custom-element lifecycle callbacks as independent function calls.

## Overview

A custom element can move through a sequence such as:

```text
ordinary / undefined element
        ↓
definition registered
        ↓
upgrade reaction enqueued
        ↓
constructor executed
        ↓
lifecycle callbacks queued
        ↓
reaction queue processed
        ↓
custom element active
```

The final behavior depends on more than the element itself.

Relevant state can include:

- document connectivity
- tree order
- attribute state
- custom-element definition timing
- reaction queues
- reaction-stack nesting
- microtasks
- reentrant DOM mutations
- connection and disconnection
- adoption between documents

The project reconstructs these interactions according to the supplied runtime evidence and task contract.

## Repository Structure

```text
upgrade-reaction-audit/
├── environment/
├── solution/
├── tests/
├── instruction.md
└── task.toml
```

### `instruction.md`

Defines the captured browser scenario, reconstruction rules, input evidence, and required artifacts.

### `environment/`

Contains the reproducible runtime and solver-visible evidence.

### `solution/`

Contains the reference reconstruction implementation.

### `tests/`

Contains the independent verifier.

### `task.toml`

Contains task metadata and execution configuration.

## Custom Element Upgrades

A custom element definition can be registered after matching elements already exist.

For example:

```html
<user-panel></user-panel>

<script>
class UserPanel extends HTMLElement {
  constructor() {
    super();
  }
}

customElements.define("user-panel", UserPanel);
</script>
```

The existing `<user-panel>` is not simply replaced.

Instead, the browser upgrades the existing element using the newly registered definition.

Conceptually:

```text
existing element
      +
new definition
      ↓
upgrade reaction
      ↓
constructor
      ↓
custom-element state
```

This allows asynchronous custom-element definitions and progressive enhancement.

## Upgrade Candidates

When a definition becomes available, the browser identifies matching upgrade candidates.

Candidate discovery is affected by factors such as:

- custom-element registry
- namespace
- local name
- customized built-in `is` value
- document membership
- shadow-including tree order

The order in which candidates are discovered can therefore influence the order in which upgrade reactions become observable.

## Reaction Queues

Every custom element has an associated reaction queue.

A reaction can be one of two broad categories:

```text
upgrade reaction
```

or:

```text
callback reaction
```

An element can accumulate several reactions before they are invoked.

Conceptually:

```text
element
  └── reaction queue
       ├── upgrade
       ├── attributeChanged
       ├── connected
       └── other callback
```

Correct reconstruction must preserve this ordering.

## Custom Element Reactions Stack

Browser operations that can trigger custom-element reactions participate in a reaction-stack mechanism.

A simplified model is:

```text
platform operation
      ↓
push element queue
      ↓
perform DOM mutation
      ↓
enqueue reactions
      ↓
pop element queue
      ↓
invoke queued reactions
```

This prevents lifecycle code from being invoked at arbitrary intermediate points inside sensitive DOM algorithms.

## Backup Element Queue

Some reactions occur when there is no active custom-element reactions stack entry.

Those elements are placed into a backup queue.

The backup queue is processed asynchronously through a microtask.

Conceptually:

```text
DOM operation
      ↓
reaction created
      ↓
no active reaction stack
      ↓
backup element queue
      ↓
microtask
      ↓
invoke reactions
```

This means reaction timing can depend on both DOM operations and microtask checkpoints.

## Lifecycle Callbacks

Custom Elements can expose lifecycle callbacks including:

```text
connectedCallback
disconnectedCallback
adoptedCallback
attributeChangedCallback
```

Additional form-associated lifecycle callbacks may also exist for form-associated custom elements.

The project focuses on the runtime ordering produced by these callbacks together with upgrade reactions.

## Attribute Reactions During Upgrade

Existing attributes can matter when an element is upgraded.

If the custom-element definition observes those attributes, the upgrade process can enqueue `attributeChangedCallback` reactions for attributes already present on the element.

Conceptually:

```html
<user-card theme="dark"></user-card>
```

followed later by:

```javascript
customElements.define("user-card", UserCard);
```

can produce lifecycle behavior reflecting the pre-existing `theme` attribute.

Therefore, reconstructing only mutations that happen after registration can be insufficient.

## Connected State

An upgraded element can also receive a connection callback when it is connected to a document.

The observable sequence can therefore resemble:

```text
upgrade
   ↓
attribute reactions
   ↓
connected reaction
```

depending on the element's state and the captured scenario.

Connection state must be determined at the appropriate historical point rather than from the final DOM alone.

## Reentrancy

Custom-element constructors and callbacks can execute author JavaScript.

That JavaScript may immediately mutate the DOM.

For example:

```text
upgrade element A
      ↓
constructor A runs
      ↓
constructor moves element B
      ↓
movement causes another upgrade attempt
      ↓
existing queued reactions interact
```

This creates reentrant execution.

A naive implementation that processes each element independently can therefore produce incorrect behavior.

## Duplicate Upgrade Attempts

An element can sometimes have more than one path that appears to request an upgrade.

The browser still tracks the custom-element state of the element.

Once an element has already entered the upgrade process, later redundant attempts must be handled according to the runtime rules rather than executing the constructor repeatedly.

This is one of the reasons custom-element state must be modeled explicitly.

## DOM Mutations

Operations that can influence the audit include:

- insertion
- removal
- reparenting
- attribute changes
- document adoption
- custom-element definition
- explicit registry upgrade
- shadow-tree operations

A mutation can affect both immediate DOM state and later queued reactions.

## Shadow DOM

Custom Elements and Shadow DOM are separate platform features, but they frequently interact.

Candidate discovery and tree traversal can use shadow-including relationships.

A component hierarchy may therefore resemble:

```text
document
  └── custom-element A
       └── shadow root
            └── custom-element B
```

Upgrade ordering must preserve the tree and registry semantics defined for the captured environment.

## Explicit Upgrades

The Custom Elements registry also provides an explicit upgrade mechanism.

Conceptually:

```javascript
customElements.upgrade(root);
```

can request upgrades within a subtree even when normal connection-driven upgrading would occur at a different point.

Explicit and automatic upgrade paths therefore need to be distinguished during replay when both appear in the evidence.

## Reaction Ordering

A central concern of the project is ordering.

For example, the difference between:

```text
constructor
attributeChanged
connected
```

and:

```text
constructor
connected
attributeChanged
```

can change application-visible behavior.

Similarly, two elements upgraded in a different order can produce different secondary mutations.

The audit therefore reconstructs the ordered reaction history rather than only the final DOM.

## Historical Replay

A high-level reconstruction can be viewed as:

```text
initial DOM
     ↓
definition / mutation event
     ↓
identify affected elements
     ↓
enqueue reactions
     ↓
maintain reaction stack
     ↓
process backup queues / microtasks
     ↓
execute lifecycle effects
     ↓
apply reentrant mutations
     ↓
next event
```

Each stage can affect the state used by later stages.

## Why Naive Approaches Fail

### Calling constructors immediately on `define()`

The platform schedules upgrade reactions rather than treating definition registration as a simple synchronous constructor loop.

### Ignoring tree order

Candidate ordering can affect upgrade execution and reentrant effects.

### Treating lifecycle callbacks independently

Callbacks belong to ordered per-element reaction queues.

### Ignoring microtasks

Backup reaction processing can happen at a later microtask checkpoint.

### Ignoring reentrancy

Constructors and callbacks can mutate other upgrade candidates.

### Upgrading detached and connected elements identically

Connection state affects when automatic upgrades and lifecycle callbacks occur.

### Running duplicate upgrade reactions twice

Custom-element state prevents repeated successful upgrades of the same element.

### Looking only at final DOM state

The same final tree can arise from different reaction histories.

## Audit Pipeline

Conceptually:

```text
captured operations
       ↓
reconstruct DOM state
       ↓
track definitions / registries
       ↓
discover upgrade candidates
       ↓
maintain reaction queues
       ↓
replay stack + backup queue
       ↓
run lifecycle ordering
       ↓
apply reentrant effects
       ↓
emit audit artifacts
```

The exact required output files and schemas are defined by `instruction.md`.

## Technical Areas

This project exercises:

- JavaScript
- Custom Elements
- Web Components
- DOM algorithms
- lifecycle callbacks
- custom-element registries
- reaction queues
- reaction-stack semantics
- microtasks
- reentrant execution
- Shadow DOM
- browser event-loop behavior
- historical state replay
- browser-runtime debugging

## Goal

Upgrade Reaction Audit reconstructs how a browser processes Custom Element upgrades and their resulting lifecycle reactions.

The central principle is:

> Custom-element behavior is an ordered runtime process, not simply a collection of callbacks attached to DOM nodes.

## License

No license is currently specified.
